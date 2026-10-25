#include "voice_service.h"
#include "audio_i2s.h"
#include "wakeword.h"
#include "wifi_client.h"
#include "net_client.h"
#include "net_proto.h"
#include "config_store.h"
#include "ble_prov.h"
#include "vw_log.h"
#include "vw_version.h"

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
#include "freertos/portmacro.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "cJSON.h"
#include <stdio.h>
#include <string.h>
#include <math.h>

#define TAG "app:voice"

#define FRAME_SAMPLES (CONFIG_VW_SAMPLE_RATE * CONFIG_VW_FRAME_MS / 1000)  // 320
#define SILENCE_END_FRAMES 40          // 检测到语音后连续静音 800ms 判定说完
#define MAX_CAPTURE_FRAMES 750         // 单次发言上限 15s
#define WAIT_SPEECH_FRAMES 150         // 唤醒后 3s 内没有说话则结束（避免空录满 15s）
#define SPEECH_RMS_TH     300          // 帧能量兜底阈值（去直流后 RMS，16bit）
#define ACK_TIMEOUT_MS    4000         // 唤醒后等待"回复词"音频的最长时间（含上位机首次合成）
#define PLAY_QUEUE_DEPTH  40           // 播放队列：40 帧 ≈ 800ms 抖动缓冲
#define PLAYBACK_IDLE_TIMEOUT_MS 10000 // 播放等待超时

/* 应用状态机 */
typedef enum {
    APP_IDLE = 0,      // 待唤醒
    APP_ACK_WAIT,      // 唤醒后等待/播放"回复词"，播完再采集
    APP_CAPTURE,       // 采集上行
    APP_PLAYBACK,      // 等待/正在播放回复
} app_state_t;

typedef struct {
    int16_t pcm[FRAME_SAMPLES];
    uint8_t flags;     // AUDIO_FLAG_xxx
} play_item_t;

static vw_config_t g_cfg;
static volatile app_state_t g_state = APP_IDLE;
static volatile uint32_t g_session_id = 0;

/* 采集端点检测 */
static bool g_speech_started = false;
static int g_silence_frames = 0;
static int g_capture_frames = 0;
static int g_wait_speech_frames = 0;

/* 播放 */
static QueueHandle_t g_play_q;
static int64_t g_last_play_ms = 0;
static int64_t g_ack_deadline = 0;             // 等待"回复词"音频的截止时间
static int16_t g_ref_buf[FRAME_SAMPLES];       // AEC 回采参考（当前正播放的数据）
static volatile bool g_ref_valid = false;
static portMUX_TYPE g_ref_lock = portMUX_INITIALIZER_UNLOCKED;

/* 现场可观测性：日志限频（都不是功能状态，仅为便于排查） */
static int64_t g_last_offline_warn_ms = 0;     // “唤醒命中但未连上位机”提示限频
static int64_t g_last_level_log_ms = 0;        // 麦克风电平提示限频
static int64_t g_last_mic_bad_ms = 0;          // “麦克风数据无效”提示限频

/* prov_ev_t 在下方定义，这里先用前置定义保证顺序 */
typedef enum {
    PROV_EV_ENTER = 1,
    PROV_EV_CFG,
} prov_ev_t;

static inline int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

/* 前置声明：handle_control 在定义之前就要用到 */
static void send_hello(void);
static void prov_post(prov_ev_t ev);

/* 音量软件缩放（MAX98357 无数字音量接口） */
static inline int16_t apply_volume(int16_t s)
{
    if (g_cfg.volume >= 100) {
        return s;
    }
    return (int16_t)(((int32_t)s * g_cfg.volume) / 100);
}

/* 播放回环参考：audio 任务读取，playback 任务写入（spinlock 保护） */
static void set_play_ref(const int16_t *pcm)
{
    portENTER_CRITICAL(&g_ref_lock);
    memcpy(g_ref_buf, pcm, sizeof(g_ref_buf));
    g_ref_valid = true;
    portEXIT_CRITICAL(&g_ref_lock);
}

static void get_play_ref(int16_t *out)
{
    portENTER_CRITICAL(&g_ref_lock);
    if (g_ref_valid) {
        memcpy(out, g_ref_buf, sizeof(g_ref_buf));
    } else {
        memset(out, 0, sizeof(g_ref_buf));
    }
    portEXIT_CRITICAL(&g_ref_lock);
}

/* ================= 会话控制 ================= */

/**
 * 计算一帧的去直流 RMS（16bit 量纲）。
 * 用途：作为 AFE VAD 的能量兜底判据。INMP441 存在固定直流偏置（实测约 -1900），
 * 必须先减均值再算能量，否则"静音"也会显得很大。
 */
static int frame_ac_rms(const int16_t *buf, int n)
{
    if (buf == NULL || n <= 0) {
        return 0;
    }
    int64_t sum = 0;
    for (int i = 0; i < n; i++) {
        sum += buf[i];
    }
    double mean = (double)sum / (double)n;
    double energy = 0;
    for (int i = 0; i < n; i++) {
        double d = (double)buf[i] - mean;
        energy += d * d;
    }
    return (int)sqrt(energy / (double)n);
}

/**
 * 开始一个会话。
 *
 * need_ack=true 时（且配置开启了 ack_enable）：先进入 APP_ACK_WAIT，
 * 等上位机把"回复词"（如"在的"）的 TTS 音频下发并播完，再开始采集用户语音；
 * 这样能避免回复词被自己的麦克风录进去（AEC 只能压一部分）。
 * 超时（ACK_TIMEOUT_MS）没收到音频也会兜底进入采集，不会卡死。
 */
static void begin_session(bool need_ack)
{
    g_session_id++;
    g_speech_started = false;
    g_silence_frames = 0;
    g_capture_frames = 0;
    g_wait_speech_frames = 0;

    char json[128];
    if (need_ack && g_cfg.ack_enable) {
        g_state = APP_ACK_WAIT;
        g_ack_deadline = now_ms() + ACK_TIMEOUT_MS;
        snprintf(json, sizeof(json),
                 "{\"cmd\":\"session.start\",\"params\":{\"session_id\":%lu,\"need_ack\":true}}",
                 (unsigned long)g_session_id);
        ESP_LOGI(TAG, "session %lu start (wait ack phrase)", (unsigned long)g_session_id);
    } else {
        g_state = APP_CAPTURE;
        snprintf(json, sizeof(json),
                 "{\"cmd\":\"session.start\",\"params\":{\"session_id\":%lu}}",
                 (unsigned long)g_session_id);
        ESP_LOGI(TAG, "session %lu start", (unsigned long)g_session_id);
    }
    net_client_send_control(json);
}

/* 立即开始采集（不需要回复词的场景，如打断后重新开口） */
static void start_session(void)
{
    begin_session(false);
}

/* 回复词播完 / 等待超时 → 进入采集 */
static void begin_capture_after_ack(const char *why)
{
    g_speech_started = false;
    g_silence_frames = 0;
    g_capture_frames = 0;
    g_wait_speech_frames = 0;
    g_state = APP_CAPTURE;
    ESP_LOGI(TAG, "session %lu capture start (%s)", (unsigned long)g_session_id, why);
}

static void end_session(const char *reason)
{
    net_client_send_audio(g_session_id, AUDIO_FLAG_END, NULL, 0);  // 末帧仅 END 标志

    char json[128];
    snprintf(json, sizeof(json),
             "{\"cmd\":\"session.end\",\"params\":{\"session_id\":%lu,\"reason\":\"%s\"}}",
             (unsigned long)g_session_id, reason);
    net_client_send_control(json);

    ESP_LOGI(TAG, "session %lu end (%s)", (unsigned long)g_session_id, reason);
    g_state = APP_PLAYBACK;           // 等待下行 TTS
    g_last_play_ms = now_ms();
}

static void on_wake(void *arg)
{
    (void)arg;
    // 仅日志提示；状态切换在 audio_task 中判定后执行
    ESP_LOGI(TAG, "wake word detected");
}

/* ================= 下行处理 ================= */

static void on_audio_down(uint32_t session_id, uint8_t flags,
                          const int16_t *pcm, int samples)
{
    if (session_id != g_session_id) {
        return;  // 丢弃过期会话（如打断后残留帧）
    }
    play_item_t item = { .flags = flags };
    int copy = (samples > FRAME_SAMPLES) ? FRAME_SAMPLES : samples;
    if (pcm && copy > 0) {
        memcpy(item.pcm, pcm, copy * sizeof(int16_t));
    }
    /* 关键：入队不能等 0。
     * 上位机一次性把整段 TTS（可能 200+ 帧）推过来时，队列满就静默丢弃，
     * 表现为"10 秒的话 1 秒就念完"。这里给 10ms 让网络任务轻微背压，
     * 真丢帧也会打日志（正常情况不应出现，因为上位机已按实时节奏发送）。 */
    if (xQueueSend(g_play_q, &item, pdMS_TO_TICKS(10)) != pdTRUE) {
        static int64_t s_drop_log_ms = 0;
        if (now_ms() - s_drop_log_ms > 2000) {
            s_drop_log_ms = now_ms();
            VW_LOG_W(TAG, "播放队列满，丢弃一帧（上位机发送过快？）");
        }
    }
    g_last_play_ms = now_ms();
}

static void playback_task(void *arg)
{
    (void)arg;
    play_item_t item;
    for (;;) {
        if (xQueueReceive(g_play_q, &item, pdMS_TO_TICKS(50)) != pdTRUE) {
            if (g_state == APP_PLAYBACK &&
                now_ms() - g_last_play_ms > PLAYBACK_IDLE_TIMEOUT_MS) {
                g_state = APP_IDLE;
                ESP_LOGI(TAG, "playback wait timeout -> idle");
            }
            continue;
        }

        g_last_play_ms = now_ms();
        for (int i = 0; i < FRAME_SAMPLES; i++) {
            item.pcm[i] = apply_volume(item.pcm[i]);
        }
        set_play_ref(item.pcm);                    // 供 AEC 作回声参考
        audio_i2s_write(item.pcm, FRAME_SAMPLES);

        if (item.flags & AUDIO_FLAG_END) {
            ESP_LOGI(TAG, "playback done");
            if (g_state == APP_ACK_WAIT) {
                // 回复词播完 → 现在才开始采集，避免把自己的回声录进去
                begin_capture_after_ack("ack played");
            } else if (g_state == APP_PLAYBACK) {
                g_state = APP_IDLE;
            }
        }
    }
}

/* ================= 控制命令处理 ================= */

static void reply_control(const char *id, const char *cmd, int code,
                          cJSON *result, const char *error)
{
    cJSON *resp = cJSON_CreateObject();
    cJSON_AddStringToObject(resp, "id", id ? id : "");
    cJSON_AddStringToObject(resp, "cmd", cmd);
    cJSON_AddNumberToObject(resp, "code", code);
    if (result) {
        cJSON_AddItemToObject(resp, "result", result);
    }
    if (error) {
        cJSON_AddStringToObject(resp, "error", error);
    }
    char *out = cJSON_PrintUnformatted(resp);
    net_client_send_control(out);
    cJSON_free(out);
    cJSON_Delete(resp);
}

static void handle_control(const char *json, int len)
{
    cJSON *root = cJSON_ParseWithLength(json, len);
    if (!root) {
        VW_LOG_W(TAG, "bad control json");
        return;
    }
    const char *cmd = cJSON_GetStringValue(cJSON_GetObjectItem(root, "cmd"));
    const char *id = cJSON_GetStringValue(cJSON_GetObjectItem(root, "id"));
    cJSON *params = cJSON_GetObjectItem(root, "params");
    ESP_LOGI(TAG, "control: %s", cmd ? cmd : "(null)");

    int code = 0;
    const char *error = NULL;
    cJSON *result = cJSON_CreateObject();
    bool need_reboot = false;

    if (cmd && !strcmp(cmd, "config.get")) {
        cJSON_AddNumberToObject(result, "volume", g_cfg.volume);
        cJSON_AddBoolToObject(result, "wake_enable", g_cfg.wake_enable);
        cJSON_AddBoolToObject(result, "ack_enable", g_cfg.ack_enable);
        cJSON_AddStringToObject(result, "wifi_ssid", g_cfg.wifi_ssid);
        cJSON_AddStringToObject(result, "host_ip", g_cfg.host_ip);
        cJSON_AddNumberToObject(result, "host_port", g_cfg.host_port);
    } else if (cmd && !strcmp(cmd, "config.set")) {
        cJSON *vol = cJSON_GetObjectItem(params, "volume");
        if (cJSON_IsNumber(vol) && vol->valuedouble >= 0 && vol->valuedouble <= 100) {
            g_cfg.volume = (uint8_t)vol->valuedouble;
            config_store_set_volume(g_cfg.volume);
            cJSON_AddNumberToObject(result, "volume", g_cfg.volume);
        }
        cJSON *wake = cJSON_GetObjectItem(params, "wake_enable");
        if (cJSON_IsBool(wake)) {
            g_cfg.wake_enable = cJSON_IsTrue(wake);
            wakeword_set_enabled(g_cfg.wake_enable);
        }
        cJSON *ack = cJSON_GetObjectItem(params, "ack_enable");
        if (cJSON_IsBool(ack)) {
            g_cfg.ack_enable = cJSON_IsTrue(ack);
            config_store_save(&g_cfg);
            cJSON_AddBoolToObject(result, "ack_enable", g_cfg.ack_enable);
        }
        cJSON *host = cJSON_GetObjectItem(params, "host");
        if (cJSON_IsObject(host)) {
            const char *ip = cJSON_GetStringValue(cJSON_GetObjectItem(host, "ip"));
            cJSON *port = cJSON_GetObjectItem(host, "port");
            if (ip && ip[0]) {
                strlcpy(g_cfg.host_ip, ip, sizeof(g_cfg.host_ip));
            }
            if (cJSON_IsNumber(port) && port->valuedouble > 0 && port->valuedouble <= 65535) {
                g_cfg.host_port = (uint16_t)port->valuedouble;
            }
            config_store_save(&g_cfg);
            cJSON_AddStringToObject(result, "host_ip", g_cfg.host_ip);
            cJSON_AddNumberToObject(result, "host_port", g_cfg.host_port);
        }
    } else if (cmd && !strcmp(cmd, "volume.set")) {
        cJSON *vol = cJSON_GetObjectItem(params, "volume");
        if (cJSON_IsNumber(vol) && vol->valuedouble >= 0 && vol->valuedouble <= 100) {
            g_cfg.volume = (uint8_t)vol->valuedouble;
            config_store_set_volume(g_cfg.volume);
            cJSON_AddNumberToObject(result, "volume", g_cfg.volume);
            ESP_LOGI(TAG, "volume -> %d", g_cfg.volume);
        } else {
            code = 2002;
            error = "invalid volume";
        }
    } else if (cmd && !strcmp(cmd, "prov.start")) {
        /* 上位机主动要求重新配网（已连上时想换 WiFi）：清空凭证语义上不安全，
         * 这里只"打开配网窗口"，凭证在配网成功后才覆盖。 */
        ESP_LOGW(TAG, "收到 prov.start，进入蓝牙配网");
        if (ble_prov_active()) {
            cJSON_AddBoolToObject(result, "prov", true);
        } else {
            prov_post(PROV_EV_ENTER);
            cJSON_AddBoolToObject(result, "prov", true);
        }
    } else if (cmd && !strcmp(cmd, "wifi.set")) {
        cJSON *ssid = cJSON_GetObjectItem(params, "ssid");
        cJSON *pass = cJSON_GetObjectItem(params, "password");
        if (cJSON_IsString(ssid) && ssid->valuestring[0]) {
            strlcpy(g_cfg.wifi_ssid, ssid->valuestring, sizeof(g_cfg.wifi_ssid));
            if (cJSON_IsString(pass)) {
                strlcpy(g_cfg.wifi_pass, pass->valuestring, sizeof(g_cfg.wifi_pass));
            }
            config_store_save(&g_cfg);
            need_reboot = true;
        } else {
            code = 2002;
            error = "ssid required";
        }
    } else if (cmd && !strcmp(cmd, "host.set")) {
        cJSON *ip = cJSON_GetObjectItem(params, "ip");
        cJSON *port = cJSON_GetObjectItem(params, "port");
        if (cJSON_IsString(ip) && ip->valuestring[0]) {
            strlcpy(g_cfg.host_ip, ip->valuestring, sizeof(g_cfg.host_ip));
            if (cJSON_IsNumber(port)) {
                g_cfg.host_port = (uint16_t)port->valuedouble;
            }
            config_store_save(&g_cfg);
            need_reboot = true;
        } else {
            code = 2002;
            error = "ip required";
        }
    } else if (cmd && !strcmp(cmd, "wakeword.set")) {
        cJSON *en = cJSON_GetObjectItem(params, "enable");
        if (cJSON_IsBool(en)) {
            g_cfg.wake_enable = cJSON_IsTrue(en);
            wakeword_set_enabled(g_cfg.wake_enable);
            config_store_save(&g_cfg);
        }
    } else if (cmd && !strcmp(cmd, "status.get")) {
        cJSON_AddStringToObject(result, "fw", VW_FW_VERSION);
        cJSON_AddNumberToObject(result, "volume", g_cfg.volume);
        cJSON_AddBoolToObject(result, "wifi", wifi_client_sta_connected());
        cJSON_AddBoolToObject(result, "prov", ble_prov_active());   /* 蓝牙配网中 */
        cJSON_AddBoolToObject(result, "wake_enable", g_cfg.wake_enable);
        cJSON_AddBoolToObject(result, "ack_enable", g_cfg.ack_enable);
        cJSON_AddNumberToObject(result, "state", (int)g_state);
    } else if (cmd && !strcmp(cmd, "reboot")) {
        need_reboot = true;
    } else {
        code = 2001;
        error = "unknown cmd";
        cJSON_Delete(result);
        result = NULL;
    }

    /* 参数被改动后补发一次 hello：上位机设备页靠它刷新，不用再点「读取当前配置」 */
    bool params_changed = (code == 0) &&
                          cmd && (!strcmp(cmd, "config.set") ||
                                  !strcmp(cmd, "volume.set") ||
                                  !strcmp(cmd, "wakeword.set"));
    if (params_changed) {
        send_hello();
    }

    reply_control(id, cmd ? cmd : "", code, result, error);
    if (need_reboot) {
        vTaskDelay(pdMS_TO_TICKS(500));  // 给 ACK 留出发送时间
        esp_restart();
    }
}

/**
 * 上报设备信息（hello）。
 *
 * 连接建立时发一次；**参数变更后（音量/唤醒开关/回复词开关）再发一次**，
 * 让上位机设备页立即反映实际值，而不是等用户点「读取当前配置」。
 *
 * 字段：device / fw / audio / mac / rssi / wakeword / volume / wake_enable / ack_enable
 */
static void send_hello(void)
{
    if (!net_client_connected()) {
        return;
    }
    char mac[18] = {0};
    wifi_client_mac(mac, sizeof(mac));
    const char *wn = wakeword_model_name();
    char json[384];
    snprintf(json, sizeof(json),
             "{\"cmd\":\"hello\",\"params\":{\"device\":\"esp32s3\",\"fw\":\"" VW_FW_VERSION "\","
             "\"audio\":{\"rate\":%d,\"bits\":16,\"ch\":1},"
             "\"mac\":\"%s\",\"rssi\":%d,\"wakeword\":\"%s\",\"volume\":%d,"
             "\"wake_enable\":%s,\"ack_enable\":%s}}",
             CONFIG_VW_SAMPLE_RATE, mac, wifi_client_rssi(),
             wn ? wn : "", (int)g_cfg.volume,
             g_cfg.wake_enable ? "true" : "false",
             g_cfg.ack_enable ? "true" : "false");
    net_client_send_control(json);
}

static void on_conn_change(bool connected)
{
    ESP_LOGI(TAG, "host %s", connected ? "connected" : "disconnected");
    if (connected) {
        send_hello();
        if (g_state != APP_IDLE) {
            g_state = APP_IDLE;  // 断线重连后复位状态
        }
    }
}

/* ================= 音频主任务 ================= */

static void audio_task(void *arg)
{
    (void)arg;
    int16_t mic[FRAME_SAMPLES];
    int16_t ref[FRAME_SAMPLES];
    vw_sr_result_t sr = {0};

    for (;;) {
        if (audio_i2s_read(mic, FRAME_SAMPLES) != ESP_OK) {
            continue;
        }
        get_play_ref(ref);
        wakeword_feed(mic, ref, FRAME_SAMPLES, &sr);

        /* 麦克风通路自检。
         *
         * 先判数据是否可信：麦克风没驱动时，线上是静止电平（全 0xFFFF）或 BCLK 串扰
         * （0x4000~0x7FFF 的 0101 图案），此时峰值看着很大其实不是声音，
         * 直接打印 peak 会误导排查方向，所以这里分开处理。 */
        audio_line_state_t line = audio_i2s_check_frame(mic, FRAME_SAMPLES);
        if (line != AUDIO_LINE_OK) {
            if ((now_ms() - g_last_mic_bad_ms) > 5000) {
                g_last_mic_bad_ms = now_ms();
                VW_LOG_W(TAG, "麦克风数据无效[%s]：%s，详见 firmware/docs/03_故障排查.md §7/§8",
                         line == AUDIO_LINE_STUCK ? "静止线" : "时钟串扰",
                         line == AUDIO_LINE_STUCK
                             ? "SD 线上没有数据（麦克风未驱动），请检查供电/时钟/接线"
                             : "读到的是 BCLK 串扰，请检查 L/R 槽位与 SD 接线");
            }
        } else {
            int peak = 0;
            for (int i = 0; i < FRAME_SAMPLES; i++) {
                int v = mic[i] < 0 ? -mic[i] : mic[i];
                if (v > peak) {
                    peak = v;
                }
            }
            /* 有真实声音（约 > -30dBFS）时限频打印峰值。
             * 说话却没有这条日志 → 麦克风/时钟/声道配置有问题；
             * 有这条日志但唤醒不触发 → 问题在唤醒词模型或阈值。 */
            if (peak > 1500 && (now_ms() - g_last_level_log_ms) > 2000) {
                g_last_level_log_ms = now_ms();
                ESP_LOGI(TAG, "mic peak=%d (state=%d)", peak, (int)g_state);
            }
        }

        switch (g_state) {
        case APP_IDLE:
            if (sr.wake_detected && g_cfg.wake_enable) {
                if (net_client_connected()) {
                    // 唤醒 → 若开启了回复词，先等上位机下发"在的"并播完再采集
                    begin_session(true);
                } else if ((now_ms() - g_last_offline_warn_ms) > 5000) {
                    /* 未连接时不能开会话（否则上行音频会丢），但要让人知道“唤醒其实是通的” */
                    g_last_offline_warn_ms = now_ms();
                    VW_LOG_W(TAG, "唤醒已命中，但上位机未连接，跳过本次会话");
                }
            }
            break;

        case APP_ACK_WAIT:
            // 等回复词音频；播完由 playback_task 切到采集，这里只做超时兜底
            if (now_ms() > g_ack_deadline) {
                begin_capture_after_ack("ack timeout");
            }
            break;

        case APP_CAPTURE: {
            /*
             * 端点判据 = AFE 的 VAD + 帧能量兜底。
             * 只用 VAD 的实际问题（现场日志可见）：噪声偏大或带直流偏置时，
             * VAD 可能一直判定为"语音"，会话就一路录到 15s 上限，
             * 端到端延迟和 STT 负担都会明显变差。
             */
            int ac_rms = frame_ac_rms(mic, FRAME_SAMPLES);
            bool voiced = (sr.vad == VW_VAD_SPEECH) || (ac_rms > SPEECH_RMS_TH);

            uint8_t flags = 0;
            if (g_capture_frames == 0) {
                flags |= AUDIO_FLAG_START;
            }
            if (voiced) {
                flags |= AUDIO_FLAG_HAS_SPEECH;
            }
            net_client_send_audio(g_session_id, flags, mic, FRAME_SAMPLES);

            // 端点检测：语音后连续静音即结束
            if (voiced) {
                g_speech_started = true;
                g_silence_frames = 0;
                g_wait_speech_frames = 0;
            } else if (g_speech_started) {
                if (++g_silence_frames >= SILENCE_END_FRAMES) {
                    end_session("vad");
                    break;
                }
            } else if (++g_wait_speech_frames >= WAIT_SPEECH_FRAMES) {
                // 唤醒后 3s 没人说话：直接结束，别把 15s 静音送去识别
                end_session("nospeech");
                break;
            }
            if (++g_capture_frames >= MAX_CAPTURE_FRAMES) {
                end_session("max");
            }
            break;
        }

        case APP_PLAYBACK:
            // AEC 使能下持续检测：播报中说唤醒词即打断
            if (sr.wake_detected) {
                ESP_LOGW(TAG, "barge-in!");
                xQueueReset(g_play_q);
                portENTER_CRITICAL(&g_ref_lock);
                g_ref_valid = false;
                portEXIT_CRITICAL(&g_ref_lock);

                char json[128];
                snprintf(json, sizeof(json),
                         "{\"cmd\":\"session.end\",\"params\":{\"session_id\":%lu,"
                         "\"reason\":\"interrupt\"}}", (unsigned long)g_session_id);
                net_client_send_control(json);

                start_session();  // 直接进入新一轮采集
            }
            break;
        }
    }
}

/* ================= 蓝牙配网 ================= */
/*
 * 触发条件（与需求一致）：
 *   1) 凭证为空            → 立即进入
 *   2) 有凭证但连续失败 5 次 → 进入（次数见 CONFIG_VW_STA_FAIL_THRESHOLD）
 * 退出条件：
 *   - 配网成功 → 关闭蓝牙 → 重启（让新的 host_ip/port 生效）
 *   - 超时（CONFIG_VW_PROV_TIMEOUT_S）→ 关闭蓝牙 → 回到 WiFi 重连
 */
#if CONFIG_VW_PROV_ENABLE

typedef struct {
    prov_ev_t ev;
    ble_prov_cfg_t cfg;
} prov_msg_t;

static QueueHandle_t g_prov_q;
static int64_t g_prov_deadline;     /* 配网截止时间(ms)，0 表示不在配网中 */

static void prov_post(prov_ev_t ev)
{
    if (g_prov_q == NULL) {
        return;
    }
    prov_msg_t m = {0};
    m.ev = ev;
    xQueueSend(g_prov_q, &m, 0);
}

/* WiFi 连续失败回调：运行在 WiFi 事件上下文，**只能入队** */
static void on_wifi_fail(int fails, void *arg)
{
    (void)arg;
    ESP_LOGW(TAG, "WiFi 连续失败 %d 次 → 请求进入蓝牙配网", fails);
    prov_post(PROV_EV_ENTER);
}

/* BLE 收到配置回调：运行在 BLE host 任务，**只能入队** */
static void on_prov_cfg(const ble_prov_cfg_t *cfg, void *arg)
{
    (void)arg;
    if (g_prov_q == NULL) {
        return;
    }
    prov_msg_t m = {0};
    m.ev = PROV_EV_CFG;
    m.cfg = *cfg;
    if (xQueueSend(g_prov_q, &m, 0) != pdTRUE) {
        VW_LOG_W(TAG, "配网队列已满，丢弃本次配置");
    }
}

static void prov_enter(void)
{
    if (ble_prov_active()) {
        return;
    }
    unsigned before = (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    if (ble_prov_start() != ESP_OK) {   /* 内部会按需初始化蓝牙 */
        VW_LOG_E(TAG, VW_ERR_BLE_INIT, "无法开启蓝牙配网（蓝牙未编译或初始化失败）");
        return;
    }
    if (g_state != APP_IDLE) {
        g_state = APP_IDLE;         /* 配网期间不采集不播放 */
    }
    g_prov_deadline = (CONFIG_VW_PROV_TIMEOUT_S > 0)
                      ? now_ms() + (int64_t)CONFIG_VW_PROV_TIMEOUT_S * 1000
                      : 0;
    ESP_LOGI(TAG, "蓝牙初始化占用内部 RAM %u KB（剩余 %u KB）",
             (before - (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL)) / 1024,
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024));
}

/* 返回 true 表示已连上 WiFi（随后会重启） */
static bool prov_apply(const ble_prov_cfg_t *c)
{
    ble_prov_notify_state("connecting", NULL, NULL);

    /* 先落盘：即使这次连接失败，用户重试时也不用重新输一遍 */
    strlcpy(g_cfg.wifi_ssid, c->ssid, sizeof(g_cfg.wifi_ssid));
    strlcpy(g_cfg.wifi_pass, c->password, sizeof(g_cfg.wifi_pass));
    if (c->host_ip[0]) {
        strlcpy(g_cfg.host_ip, c->host_ip, sizeof(g_cfg.host_ip));
    }
    if (c->host_port) {
        g_cfg.host_port = c->host_port;
    }
    config_store_save(&g_cfg);

    if (wifi_client_reconnect_with(c->ssid, c->password) != ESP_OK) {
        ble_prov_notify_state("fail", "wifi config rejected", NULL);
        return false;
    }
    if (!wifi_client_wait_ip(15000)) {
        VW_LOG_E(TAG, VW_ERR_BLE_PROV_FAIL, "配网后仍连不上 WiFi（ssid=%s）", c->ssid);
        ble_prov_notify_state("fail", "cannot join WiFi (password or signal)", NULL);
        return false;               /* 保持蓝牙，让用户改密码重试 */
    }

    char ip[16] = {0};
    wifi_client_ip(ip, sizeof(ip));
    ESP_LOGI(TAG, "配网成功：IP=%s，上位机 %s:%u", ip, g_cfg.host_ip,
             (unsigned)g_cfg.host_port);
    ble_prov_notify_state("ok", NULL, ip);
    return true;
}

static void prov_task(void *arg)
{
    (void)arg;
    prov_msg_t msg;

    for (;;) {
        TickType_t wait = portMAX_DELAY;
        if (ble_prov_active() && g_prov_deadline > 0) {
            int64_t left = g_prov_deadline - now_ms();
            wait = pdMS_TO_TICKS(left > 0 ? left : 1);
        }

        if (xQueueReceive(g_prov_q, &msg, wait) == pdTRUE) {
            if (msg.ev == PROV_EV_ENTER) {
                prov_enter();
            } else if (msg.ev == PROV_EV_CFG) {
                if (prov_apply(&msg.cfg)) {
                    /* 关键顺序：先关蓝牙（射频静默），留时间把 ok 发出去，再重启 */
                    ble_prov_stop();
                    g_prov_deadline = 0;
                    vTaskDelay(pdMS_TO_TICKS(800));
                    esp_restart();
                } else {
                    /* 失败：蓝牙继续开着，用户可重新写入 */
                    if (g_prov_deadline == 0) {
                        g_prov_deadline = now_ms() +
                                          (int64_t)CONFIG_VW_PROV_TIMEOUT_S * 1000;
                    }
                }
            }
        } else if (ble_prov_active()) {
            /* 超时：退出配网并归还蓝牙内存，回 WiFi 重连，避免永久卡在配网态 */
            ESP_LOGW(TAG, "配网超时（%d 秒无有效配置），退出配网并重试 WiFi",
                     CONFIG_VW_PROV_TIMEOUT_S);
            ble_prov_stop();
            ble_prov_deinit();      /* 把 ~50KB 内部 RAM 还给语音链路 */
            g_prov_deadline = 0;
            wifi_client_retry_now();
        }
    }
}

#else   /* 未启用蓝牙配网 */

static void prov_post(prov_ev_t ev) { (void)ev; }

#endif  /* CONFIG_VW_PROV_ENABLE */

/* ================= 服务初始化 ================= */

esp_err_t voice_service_init(void)
{
    config_store_load(&g_cfg);
    ESP_LOGI(TAG, "config: volume=%d host=%s:%d", g_cfg.volume, g_cfg.host_ip, g_cfg.host_port);

    g_play_q = xQueueCreate(PLAY_QUEUE_DEPTH, sizeof(play_item_t));  // 40 帧 ≈ 800ms 抖动缓冲
    if (g_play_q == NULL) {
        VW_LOG_E(TAG, VW_ERR_NOMEM, "create play queue failed");
        return ESP_FAIL;
    }

    esp_err_t err = wakeword_init(on_wake, NULL);
    if (err != ESP_OK) {
        return err;
    }
    wakeword_set_enabled(g_cfg.wake_enable);

    /* 配网：先建队列与任务，再启动 WiFi —— 无凭证时 WiFi 会立刻要求进配网。
     * 注意：**蓝牙在这里不初始化**（NimBLE 要占 40~60KB 内部 RAM，会把
     * playback/audio 的任务栈挤到创建失败，v0.8.0 实测过），
     * 改为进入配网时按需初始化，配网结束 ble_prov_deinit() 归还内存。 */
#if CONFIG_VW_PROV_ENABLE
    g_prov_q = xQueueCreate(4, sizeof(prov_msg_t));
    if (g_prov_q == NULL) {
        VW_LOG_E(TAG, VW_ERR_NOMEM, "create prov queue failed");
        return ESP_FAIL;
    }
    if (xTaskCreate(prov_task, "prov", 6144, NULL, 8, NULL) != pdPASS) {
        VW_LOG_E(TAG, VW_ERR_NOMEM, "create prov task failed");
        return ESP_FAIL;
    }
    ble_prov_set_apply_cb(on_prov_cfg, NULL);
    wifi_client_set_fail_cb(on_wifi_fail, NULL);
#endif

    esp_err_t werr = wifi_client_start(g_cfg.wifi_ssid, g_cfg.wifi_pass);
    if (werr == ESP_ERR_NOT_FOUND) {
        ESP_LOGW(TAG, "设备尚无 WiFi 凭证 → 立即进入蓝牙配网");
        prov_post(PROV_EV_ENTER);
    } else if (werr != ESP_OK) {
        VW_LOG_E(TAG, VW_ERR_WIFI_NOT_CONNECTED, "wifi start failed: %s",
                 esp_err_to_name(werr));
        return werr;
    }

    net_client_set_handlers(handle_control, on_audio_down, on_conn_change);
    ESP_RETURN_ON_ERROR(net_client_start(g_cfg.host_ip, g_cfg.host_port), TAG, "net start failed");

    /* 先创建 playback（内部 RAM 最紧张的时刻在最后），
     * 失败时的报错才有意义；audio 最后创建，创建成功即开始采集。 */
    if (xTaskCreate(playback_task, "playback", 4096, NULL, 12, NULL) != pdPASS) {
        VW_LOG_E(TAG, VW_ERR_NOMEM, "create playback task failed（内部 RAM 不足，"
                 "free_internal=%u）",
                 (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
        return ESP_FAIL;
    }
    if (xTaskCreate(audio_task, "audio", 8192, NULL, 11, NULL) != pdPASS) {
        VW_LOG_E(TAG, VW_ERR_NOMEM, "create audio task failed（free_internal=%u）",
                 (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "全部任务就绪，内部 RAM 剩余 %u KB",
             (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024));
    return ESP_OK;
}
