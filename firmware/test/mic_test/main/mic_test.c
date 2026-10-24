/**
 * mic_test —— INMP441 最小采集测试（独立工程，不依赖主项目组件）
 *
 * ============================ 它解决什么问题 ============================
 * 主项目里「麦克风没数据」时，无法区分是「I2S 配置错」还是「麦克风/接线的硬件问题」。
 * 这个工程把两者分开：
 *   1) 初始化参数与主项目 components/vw_audio/audio_i2s.c **完全一致**
 *      （32bit 槽宽 + 单声道 + slot_mask → BCLK = 64×fs = 1.024MHz，符合 INMP441 手册）；
 *   2) 启动先做「配置扫描」：32/16bit × 左/右 共 4 种组合各采 1 秒并打分，
 *      如果只是槽位选错或位宽不对，扫描结果会直接指出来；
 *   3) 然后锁定最佳配置，每秒打印一次统计与前 8 个样本的十六进制，
 *      对着麦克风说话时 rms / max 应明显变化。
 *
 * ============================== 怎么判断 ==============================
 *   打印里 cls 有三种：
 *     [静止]  几乎所有样本都是 0xFFFF/0x0000 → 麦克风没驱动 SD（硬件问题）
 *     [串扰]  相邻样本等幅反号（0x5555 家族）  → 读错槽 / SD 悬空
 *     [正常]  样本逐个变化                      → 麦克风在工作
 *
 *   另外看 dup（成对重复比例）：
 *     dup≈1.0 → 每个样本出现两次，说明 BCLK 只有一半（16bit 槽宽 → 512kHz），
 *               麦克风实际只以 8kHz 出样本，音频被降速，**不能用于语音识别**。
 *     正确配置必须是 32bit 槽宽（BCLK=64×fs=1.024MHz），此时 dup 应 < 0.3。
 *
 * ============================== 怎么运行 ==============================
 *   cd firmware/test/mic_test
 *   idf.py set-target esp32s3
 *   idf.py -p COM6 build flash monitor
 *
 * 引脚与主项目 main/Kconfig.projbuild 的默认值一致，改接线时只改下面的宏。
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/i2s_std.h"
#include "esp_log.h"

static const char *TAG = "MIC_TEST";

/* ---------------- 硬件参数（与主项目一致，接线改了只改这里） ---------------- */

#define PIN_MIC_BCLK   5      /* INMP441 SCK  */
#define PIN_MIC_WS     4      /* INMP441 WS   */
#define PIN_MIC_SD     6      /* INMP441 SD   */

#define SAMPLE_RATE    16000
#define FRAME_MS       20
#define FRAME_SAMPLES  (SAMPLE_RATE / 1000 * FRAME_MS)   /* 320 */

/* ---------------- 内部状态 ---------------- */

static i2s_chan_handle_t s_rx = NULL;
static int32_t          *s_raw32 = NULL;   /* 32bit 读取缓冲 */

/* ---------------- 数据质量统计 ---------------- */

typedef struct {
    double rms;     /* AC 有效值（已扣直流） */
    double dc;      /* 直流均值 */
    double flat;    /* 恰好等于 -1/0 的样本占比 */
    double alt;     /* 等幅反号比例 */
    double dup;     /* 相邻成对重复的比例：≈1 说明采样率只有一半 */
    int    min_v;
    int    max_v;
    int    spikes;
    int    cls;     /* 0=静止 1=串扰 2=正常 */
} stats_t;

static const char *cls_name(int cls)
{
    return cls == 0 ? "静止" : (cls == 1 ? "串扰" : "正常");
}

static void stats_of(const int16_t *b, int n, stats_t *s)
{
    int64_t sum = 0;
    int flat = 0, spikes = 0, alt = 0;

    s->min_v = 32767;
    s->max_v = -32768;
    for (int i = 0; i < n; i++) {
        sum += b[i];
        if (b[i] < s->min_v) s->min_v = b[i];
        if (b[i] > s->max_v) s->max_v = b[i];
        if (b[i] == -1 || b[i] == 0) flat++;
        if (abs((int)b[i]) > 1000) spikes++;
    }
    s->dc = (double)sum / (double)n;

    double energy = 0;
    for (int i = 0; i < n; i++) {
        double d = (double)b[i] - s->dc;
        energy += d * d;
    }
    for (int i = 1; i < n; i++) {
        int a = b[i - 1], c = b[i];
        int aa = a < 0 ? -a : a;
        int ac = c < 0 ? -c : c;
        if (((a > 0) != (c > 0)) && aa > 4000 && ac > 4000) {
            int d = aa > ac ? aa - ac : ac - aa;
            if (d < 1500) alt++;
        }
    }

    s->rms    = sqrt(energy / (double)n);
    s->flat   = (double)flat / (double)n;
    s->alt    = (double)alt / (double)(n - 1);
    s->spikes = spikes;

    /*
     * 成对重复检测：以 (0,1) (2,3) (4,5)… 为单位，统计后一个等于前一个的比例。
     * INMP441 每个 WS 帧要 64 个 SCK；若只给 32 个（16bit 槽宽 → BCLK=512kHz），
     * 麦克风实际只以 8kHz 出新样本，而 ESP32 仍按 16kHz 采样 → 每个样本出现两次，
     * 该比例会接近 1.0。此时音频被"降速"，频谱整体下移一个八度，唤醒必然失败。
     */
    int pair = 0, pairs = 0;
    for (int i = 0; i + 1 < n; i += 2) {
        pairs++;
        if (b[i + 1] == b[i]) pair++;
    }
    s->dup = (pairs > 0) ? (double)pair / (double)pairs : 0.0;

    if (s->flat >= 0.97) {
        s->cls = 0;
    } else if (s->alt >= 0.80) {
        s->cls = 1;
    } else {
        s->cls = 2;
    }
}

/* ---------------- I2S 打开 / 关闭 / 读取 ---------------- */

static esp_err_t rx_open(i2s_data_bit_width_t bits, i2s_std_slot_mask_t mask)
{
    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan.dma_frame_num = FRAME_SAMPLES;
    chan.auto_clear    = true;

    esp_err_t err = i2s_new_channel(&chan, NULL, &s_rx);
    if (err != ESP_OK) {
        return err;
    }

    i2s_std_config_t cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(bits, I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_MIC_BCLK,
            .ws   = PIN_MIC_WS,
            .din  = PIN_MIC_SD,
            .dout = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    cfg.slot_cfg.slot_mask = mask;

    err = i2s_channel_init_std_mode(s_rx, &cfg);
    if (err == ESP_OK) {
        err = i2s_channel_enable(s_rx);
    }
    if (err != ESP_OK) {
        i2s_del_channel(s_rx);
        s_rx = NULL;
    }
    return err;
}

static void rx_close(void)
{
    if (s_rx != NULL) {
        i2s_channel_disable(s_rx);
        i2s_del_channel(s_rx);
        s_rx = NULL;
    }
}

/**
 * 读一帧并统一成 int16。
 * 32bit 槽宽：24bit 有效数据在槽内左对齐，右移 16 位即得 16bit PCM（与主项目一致）。
 */
static esp_err_t rx_read(i2s_data_bit_width_t bits, int16_t *out, int n)
{
    size_t got = 0;

    if (bits == I2S_DATA_BIT_WIDTH_32BIT) {
        esp_err_t err = i2s_channel_read(s_rx, s_raw32, n * sizeof(int32_t), &got,
                                         pdMS_TO_TICKS(1000));
        if (err != ESP_OK) {
            return err;
        }
        int m = (int)(got / sizeof(int32_t));
        for (int i = 0; i < m; i++) {
            out[i] = (int16_t)(s_raw32[i] >> 16);
        }
        return (m >= n) ? ESP_OK : ESP_ERR_INVALID_SIZE;
    }

    esp_err_t err = i2s_channel_read(s_rx, out, n * sizeof(int16_t), &got, pdMS_TO_TICKS(1000));
    if (err != ESP_OK) {
        return err;
    }
    return (got >= (size_t)n * sizeof(int16_t)) ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

/* ---------------- 配置扫描 ---------------- */

typedef struct {
    i2s_data_bit_width_t  bits;
    i2s_std_slot_mask_t   mask;
    const char           *name;
    int                   bclk_khz;
} rx_cfg_t;

typedef struct {
    stats_t st;
    bool    ok;         /* 是否成功采到数据 */
    int     score;      /* 越大越好：正常=2000 + rms，串扰=1000，静止=0 */
} scan_result_t;

static void scan_one(const rx_cfg_t *c, scan_result_t *out)
{
    memset(out, 0, sizeof(*out));

    esp_err_t err = rx_open(c->bits, c->mask);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "[SCAN] %-12s 打开失败: %s", c->name, esp_err_to_name(err));
        return;
    }

    /* 丢掉 DMA 预填充的块，再采 3 帧取“最像有信号”的那一帧 */
    int16_t *buf = (int16_t *)malloc(FRAME_SAMPLES * sizeof(int16_t));
    if (buf == NULL) {
        rx_close();
        return;
    }
    vTaskDelay(pdMS_TO_TICKS(120));
    rx_read(c->bits, buf, FRAME_SAMPLES);
    vTaskDelay(pdMS_TO_TICKS(30));

    stats_t best = { .rms = -1 };
    for (int i = 0; i < 3; i++) {
        if (rx_read(c->bits, buf, FRAME_SAMPLES) != ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(30));
            continue;
        }
        stats_t s;
        stats_of(buf, FRAME_SAMPLES, &s);
        if (s.cls > best.cls || (s.cls == best.cls && s.rms > best.rms)) {
            best = s;
        }
        vTaskDelay(pdMS_TO_TICKS(30));
    }

    out->st  = best;
    out->ok  = true;
    /*
     * 打分：先看"是不是有数据"，再看"采样是否被降速"。
     * 只按 rms 排会出错——16bit 槽宽虽然也有波形，但它是 8kHz 采出来的，
     * 样本成对重复、音调被拉低一个八度，绝不能当最佳配置。
     */
    out->score = (best.cls == 2) ? 2000 + (int)best.rms
               : (best.cls == 1) ? 1000 : 0;
    if (best.cls == 2 && best.dup > 0.80) {
        out->score -= 1500;          /* 采样被降速，严重扣分 */
    }

    ESP_LOGI(TAG, "[SCAN] %-12s BCLK=%dkHz cls=%-4s rms=%-6.0f dup=%.2f flat=%.2f alt=%.2f raw=%04x %04x %04x %04x",
             c->name, c->bclk_khz, cls_name(best.cls), best.rms, best.dup,
             best.flat, best.alt,
             (unsigned)(uint16_t)buf[0], (unsigned)(uint16_t)buf[1],
             (unsigned)(uint16_t)buf[2], (unsigned)(uint16_t)buf[3]);

    if (best.cls == 2 && best.dup > 0.80) {
        ESP_LOGW(TAG, "        ↑ 样本成对重复(dup≈1)：该配置的 BCLK 只有一半，"
                      "麦克风实际工作在 %d kHz，音频会被降速 — 不能用", c->bclk_khz / 64);
    }

    free(buf);
    rx_close();
}

/* ---------------- 入口 ---------------- */

void app_main(void)
{
    static const rx_cfg_t cfgs[] = {
        { I2S_DATA_BIT_WIDTH_32BIT, I2S_STD_SLOT_LEFT,  "32bit/left",  1024 },
        { I2S_DATA_BIT_WIDTH_32BIT, I2S_STD_SLOT_RIGHT, "32bit/right", 1024 },
        { I2S_DATA_BIT_WIDTH_16BIT, I2S_STD_SLOT_LEFT,  "16bit/left",   512 },
        { I2S_DATA_BIT_WIDTH_16BIT, I2S_STD_SLOT_RIGHT, "16bit/right",  512 },
    };
    const int n_cfg = sizeof(cfgs) / sizeof(cfgs[0]);

    s_raw32 = (int32_t *)malloc(FRAME_SAMPLES * sizeof(int32_t));
    if (s_raw32 == NULL) {
        ESP_LOGE(TAG, "内存不足");
        return;
    }

    ESP_LOGI(TAG, "===== INMP441 最小采集测试 =====");
    ESP_LOGI(TAG, "引脚: SCK=GPIO%d WS=GPIO%d SD=GPIO%d", PIN_MIC_BCLK, PIN_MIC_WS, PIN_MIC_SD);
    ESP_LOGI(TAG, "采样率 %d Hz，帧长 %d ms（%d 样本）", SAMPLE_RATE, FRAME_MS, FRAME_SAMPLES);
    ESP_LOGI(TAG, "说明: INMP441 要求 fSCK = 64×fWS，所以 32bit 槽宽(1.024MHz)才是正确配置");

    /* ---------- 第一步：配置扫描 ---------- */
    ESP_LOGI(TAG, "----- 配置扫描（每种采 1 秒）-----");
    scan_result_t results[4];
    int best = -1;
    for (int i = 0; i < n_cfg; i++) {
        scan_one(&cfgs[i], &results[i]);
        if (results[i].ok && (best < 0 || results[i].score > results[best].score)) {
            best = i;
        }
    }

    if (best < 0) {
        ESP_LOGE(TAG, "所有配置都打不开 I2S，检查引脚是否与其他外设冲突");
        while (1) {
            vTaskDelay(pdMS_TO_TICKS(2000));
        }
    }

    ESP_LOGI(TAG, "----- 扫描结论 -----");
    ESP_LOGI(TAG, "最佳配置: %s（cls=%s rms=%.0f）",
             cfgs[best].name, cls_name(results[best].st.cls), results[best].st.rms);

    if (results[best].st.cls != 2) {
        ESP_LOGE(TAG, "四种配置都拿不到有效音频 → 不是 I2S 配置问题，而是麦克风侧没有数据。");
        ESP_LOGE(TAG, "按顺序检查（详细见 docs/05_故障排查.md 第 14 章）：");
        ESP_LOGE(TAG, "  1) 模块焊盘顺序：这块圆形模块的丝印在背面，从正面看左右是反的，");
        ESP_LOGE(TAG, "     务必确认 VDD/GND 没有接反（接反会烧模块）");
        ESP_LOGE(TAG, "  2) 万用表量模块焊盘：VDD=3.30V，SCK≈1.6V，WS≈1.6V，SD≈1.5~1.7V");
        ESP_LOGE(TAG, "  3) 共地：模块 GND 与 ESP32 GND 必须直通（通断档量）");
        ESP_LOGE(TAG, "  4) SD 线是否真的通到 GPIO%d", PIN_MIC_SD);
        ESP_LOGE(TAG, "  5) 换一块模块交叉验证");
        ESP_LOGE(TAG, "下面仍会按最佳配置继续打印，方便你边量边看数值变化。");
    }

    /* ---------- 第二步：锁定最佳配置，持续打印 ---------- */
    if (rx_open(cfgs[best].bits, cfgs[best].mask) != ESP_OK) {
        ESP_LOGE(TAG, "按最佳配置重新打开 I2S 失败");
        return;
    }
    vTaskDelay(pdMS_TO_TICKS(150));

    ESP_LOGI(TAG, "===== 开始连续采集（每秒一行）=====");
    ESP_LOGI(TAG, "对着麦克风说话/拍手，rms 与 max 应明显变化；若长期不变即为硬件问题");

    int16_t *buf = (int16_t *)malloc(FRAME_SAMPLES * sizeof(int16_t));
    if (buf == NULL) {
        rx_close();
        return;
    }

    int frames = 0;
    int64_t t_last = 0;

    for (;;) {
        if (rx_read(cfgs[best].bits, buf, FRAME_SAMPLES) != ESP_OK) {
            ESP_LOGW(TAG, "读失败，重试");
            vTaskDelay(pdMS_TO_TICKS(50));
            continue;
        }

        stats_t s;
        stats_of(buf, FRAME_SAMPLES, &s);
        frames++;

        int64_t now = esp_log_timestamp();
        if (now - t_last >= 1000) {
            t_last = now;
            ESP_LOGI(TAG, "%5.1fs cls=%-4s rms=%-7.0f dc=%-6.0f dup=%.2f min=%-6d max=%-6d | raw=%04x %04x %04x %04x %04x %04x %04x %04x",
                     (double)now / 1000.0, cls_name(s.cls), s.rms, s.dc, s.dup, s.min_v, s.max_v,
                     (unsigned)(uint16_t)buf[0], (unsigned)(uint16_t)buf[1],
                     (unsigned)(uint16_t)buf[2], (unsigned)(uint16_t)buf[3],
                     (unsigned)(uint16_t)buf[4], (unsigned)(uint16_t)buf[5],
                     (unsigned)(uint16_t)buf[6], (unsigned)(uint16_t)buf[7]);
            frames = 0;
        }
    }
}
