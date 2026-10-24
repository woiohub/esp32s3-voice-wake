#include "wakeword.h"
#include "esp_afe_sr_iface.h"
#include "esp_afe_sr_models.h"
#include "esp_afe_config.h"
#include "esp_wn_iface.h"
#include "esp_wn_models.h"
#include "esp_vad.h"
#include "model_path.h"   /* esp-srm 模型管理：esp_srmodel_init / filter / exists */
#include "vw_log.h"
#include <stdlib.h>
#include <string.h>

#define TAG "srv:wake"

/*
 * ESP-SR 版本说明（esp-sr 1.9.5 / ESP-IDF v5.3.5 / ESP32-S3）：
 *   - S3/P4 上 WakeNet 模型数据 **不编译进 libwakenet.a**，而是打包成 srmodels.bin，
 *     由 `idf.py flash` 写入分区表里名为 model 的分区；
 *   - 运行期必须先调用 esp_srmodel_init(分区名)，把模型 mmap 并注册成静态模型表，
 *     否则底层 esp_wn_handle_from_name() 找不到模型，afe_create_from_config()
 *     内部对空模型指针做 memcpy → LoadProhibited(EXCVADDR=0) → panic → 无限重启。
 *   - 因此「先 init 模型 → 校验名字 → 再建 AFE」是本模块的强制顺序。
 */

static const esp_afe_sr_iface_t *s_afe = &ESP_AFE_SR_HANDLE;
static esp_afe_sr_data_t *s_data = NULL;

static srmodel_list_t *s_models = NULL;        // 模型分区解析结果，deinit 时释放
static char s_wn_name[MODEL_NAME_MAX_LENGTH];  // 实际生效的唤醒模型名

static int s_feed_samples = 0;   // 每次 feed 的每通道样本数（由引擎决定）
static int s_total_ch = 0;       // 总通道数 = mic + ref

/* 累积缓冲：上层按 20ms 喂入，内部累积到 s_feed_samples 再整块处理 */
static int16_t *s_mic_acc = NULL;
static int16_t *s_ref_acc = NULL;
static int16_t *s_feed_buf = NULL;   // 交错 [mic, ref, mic, ref, ...]
static int s_fill = 0;

static vw_wake_cb_t s_cb = NULL;
static void *s_arg = NULL;
static bool s_enabled = true;
static bool s_feed_logged = false;   // 首次 feed 时打印一次返回长度，便于现场确认

/**
 * 从 model 分区加载模型表，并把可用模型全部打印出来（便于现场核对名字）。
 */
static esp_err_t sr_models_load(void)
{
    s_models = esp_srmodel_init(CONFIG_VW_SR_MODEL_PARTITION);
    if (s_models == NULL || s_models->num <= 0) {
        VW_LOG_E(TAG, VW_ERR_SR_MODEL_NOT_FOUND,
                 "model 分区 \"%s\" 里没有可用模型：请用 `idf.py flash` 整片烧录"
                 "（会自动写入 build/srmodels/srmodels.bin），并确认分区体积 >= 该文件大小",
                 CONFIG_VW_SR_MODEL_PARTITION);
        return ESP_ERR_NOT_FOUND;
    }

    ESP_LOGI(TAG, "model partition \"%s\": %d model(s)",
             CONFIG_VW_SR_MODEL_PARTITION, s_models->num);
    for (int i = 0; i < s_models->num; i++) {
        ESP_LOGI(TAG, "  model[%d] = %s", i, s_models->model_name[i]);
    }
    return ESP_OK;
}

/**
 * 解析要使用的 wakenet 模型名。
 * 优先级：Kconfig 指定名 → 编译期 WAKENET_MODEL_NAME → 分区内第一个 wn* 模型。
 * 这样即使 menuconfig 里选的模型与代码写死的名字不一致，也不会崩，只会回退。
 */
static esp_err_t resolve_wakenet_name(void)
{
    const char *candidates[] = { CONFIG_VW_SR_WAKENET_NAME, WAKENET_MODEL_NAME };

    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        if (candidates[i] != NULL && candidates[i][0] != '\0' &&
            esp_srmodel_exists(s_models, (char *)candidates[i]) >= 0) {
            strncpy(s_wn_name, candidates[i], sizeof(s_wn_name) - 1);
            ESP_LOGI(TAG, "wakenet model = %s", s_wn_name);
            return ESP_OK;
        }
    }

    /* 兜底：任取一个唤醒模型（前提是分区里确实有） */
    char *fallback = esp_srmodel_filter(s_models, ESP_WN_PREFIX, NULL);
    if (fallback == NULL) {
        VW_LOG_E(TAG, VW_ERR_SR_MODEL_NOT_FOUND,
                 "模型分区里没有 wn* 唤醒模型（当前选择 \"%s\"），请在 menuconfig 中"
                 "勾选至少一个 WakeNet 模型后重新 build + flash",
                 CONFIG_VW_SR_WAKENET_NAME);
        return ESP_ERR_NOT_FOUND;
    }

    strncpy(s_wn_name, fallback, sizeof(s_wn_name) - 1);
    VW_LOG_W(TAG, "指定模型 \"%s\" 不存在，回退为 \"%s\"",
             CONFIG_VW_SR_WAKENET_NAME, s_wn_name);
    return ESP_OK;
}

esp_err_t wakeword_init(vw_wake_cb_t cb, void *arg)
{
    s_cb = cb;
    s_arg = arg;

    /* ---------- 1) 必须先加载模型表，否则 AFE 内部拿不到模型数据 ---------- */
    esp_err_t err = sr_models_load();
    if (err != ESP_OK) {
        return err;
    }
    err = resolve_wakenet_name();
    if (err != ESP_OK) {
        return err;
    }

    /* ---------- 2) 配置并创建 AFE（1 麦克风 + 1 回环参考，AEC/VAD/WakeNet 全开）---------- */
    afe_config_t cfg = AFE_CONFIG_DEFAULT();
    cfg.wakenet_model_name = s_wn_name;
    cfg.aec_init = true;                           // 播报中打断（barge-in）依赖 AEC
    cfg.vad_init = true;                           // 上行语音端点检测
    cfg.wakenet_init = true;

#if CONFIG_VW_AFE_MODE_LOW_COST
    cfg.afe_mode = SR_MODE_LOW_COST;
#else
    cfg.afe_mode = SR_MODE_HIGH_PERF;
#endif

#if CONFIG_VW_AFE_MEM_MORE_INTERNAL
    cfg.memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_INTERNAL;
#elif CONFIG_VW_AFE_MEM_MORE_PSRAM
    cfg.memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_PSRAM;
#else
    cfg.memory_alloc_mode = AFE_MEMORY_ALLOC_INTERNAL_PSRAM_BALANCE;
#endif

    cfg.pcm_config.total_ch_num = 2;               // 1 麦克风 + 1 回环参考
    cfg.pcm_config.mic_num = 1;
    cfg.pcm_config.ref_num = 1;
    cfg.pcm_config.sample_rate = 16000;

    s_data = s_afe->create_from_config(&cfg);
    if (s_data == NULL) {
        VW_LOG_E(TAG, VW_ERR_AFE_INIT,
                 "create_from_config 失败（模型已加载，检查 PSRAM/cache 配置："
                 "SPIRAM=y、DATA_CACHE_64KB、CACHE_LINE_64B）");
        return ESP_FAIL;
    }

    s_feed_samples = s_afe->get_feed_chunksize(s_data);
    s_total_ch = s_afe->get_total_channel_num(s_data);
    if (s_feed_samples <= 0 || s_total_ch <= 0) {
        VW_LOG_E(TAG, VW_ERR_AFE_INIT, "AFE 参数异常: feed=%d ch=%d",
                 s_feed_samples, s_total_ch);
        return ESP_FAIL;
    }

    /* calloc：无播放时参考通道保持全 0，避免 AEC 被垃圾数据干扰 */
    s_mic_acc = (int16_t *)calloc(s_feed_samples, sizeof(int16_t));
    s_ref_acc = (int16_t *)calloc(s_feed_samples, sizeof(int16_t));
    s_feed_buf = (int16_t *)calloc((size_t)s_feed_samples * s_total_ch, sizeof(int16_t));
    if (s_mic_acc == NULL || s_ref_acc == NULL || s_feed_buf == NULL) {
        VW_LOG_E(TAG, VW_ERR_NOMEM, "alloc afe buffer failed");
        return ESP_ERR_NO_MEM;
    }
    s_fill = 0;

    ESP_LOGI(TAG, "afe ready: model=%s feed=%d ch=%d", s_wn_name, s_feed_samples, s_total_ch);
    return ESP_OK;
}

esp_err_t wakeword_feed(const int16_t *mic, const int16_t *ref, int samples,
                        vw_sr_result_t *out)
{
    if (out) {
        out->wake_detected = false;
        out->vad = VW_VAD_SILENCE;
    }
    if (s_data == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    int pos = 0;
    while (pos < samples) {
        int take = s_feed_samples - s_fill;
        if (take > samples - pos) {
            take = samples - pos;
        }
        memcpy(s_mic_acc + s_fill, mic + pos, take * sizeof(int16_t));
        memcpy(s_ref_acc + s_fill, ref + pos, take * sizeof(int16_t));
        s_fill += take;
        pos += take;

        if (s_fill < s_feed_samples) {
            break;  // 数据不足一整块，等待下次 feed
        }
        s_fill = 0;

        // 组装交错输入 [mic, ref, mic, ref, ...]
        for (int i = 0; i < s_feed_samples; i++) {
            s_feed_buf[i * s_total_ch] = s_mic_acc[i];
            if (s_total_ch > 1) {
                s_feed_buf[i * s_total_ch + 1] = s_ref_acc[i];
            }
        }

        /* 注意：feed() 的返回值是“写入的输入长度”（>0 为正常），不是错误码。
         * 早期实现用 `!= 0` 判断，导致每帧都误报 AFE_FEED 错误。 */
        int fed = s_afe->feed(s_data, s_feed_buf);
        if (fed < 0) {
            VW_LOG_E(TAG, VW_ERR_AFE_FEED, "afe feed failed: ret=%d", fed);
            return ESP_FAIL;
        }
        if (!s_feed_logged) {
            s_feed_logged = true;
            ESP_LOGI(TAG, "afe feed ok: ret=%d (feed_chunk=%d, ch=%d)",
                     fed, s_feed_samples, s_total_ch);
        }
        afe_fetch_result_t *res = s_afe->fetch(s_data);
        if (res && out) {
            out->wake_detected = (res->wakeup_state == WAKENET_DETECTED);
            out->vad = (res->vad_state == AFE_VAD_SPEECH) ? VW_VAD_SPEECH : VW_VAD_SILENCE;
        }
        if (res && res->wakeup_state == WAKENET_DETECTED && s_enabled && s_cb) {
            s_cb(s_arg);
        }
    }
    return ESP_OK;
}

const char *wakeword_model_name(void)
{
    return s_wn_name;
}

void wakeword_set_enabled(bool enable)
{
    s_enabled = enable;
}

esp_err_t wakeword_deinit(void)
{
    if (s_data) {
        s_afe->destroy(s_data);
        s_data = NULL;
    }
    free(s_mic_acc);
    free(s_ref_acc);
    free(s_feed_buf);
    s_mic_acc = s_ref_acc = s_feed_buf = NULL;

    if (s_models) {
        esp_srmodel_deinit(s_models);
        s_models = NULL;
    }
    return ESP_OK;
}
