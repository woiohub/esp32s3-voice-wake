#include "audio_i2s.h"
#include "driver/i2s_std.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include <math.h>
#include <stdlib.h>

#define TAG "drv:i2s"

/*
 * 关键时序依据（INMP441 数据手册 DS-INMP441-00 Rev1.1）：
 *   "There must be 64 SCK cycles in each WS stereo frame, or 32 SCK cycles per data-word"
 *   fSCK = 64 × fWS
 *
 * ESP-IDF 新版 I2S 驱动的 BCLK 计算为：bclk = fS × total_slot(恒为 2) × slot_bit_width
 *   - slot_bit_width = 16 → BCLK = 32×fS  ← 只有手册要求的一半，麦克风无法正确对齐
 *   - slot_bit_width = 32 → BCLK = 64×fS  ← 符合手册
 * 因此 RX 必须使用 32bit 槽宽（每声道 32 SCK），再把 32bit 样本右移 16 位取高 16bit
 * （24bit 有效数据在 32bit 槽内是高 24 位，MSB-first，左对齐）。
 *
 * 功放 MAX98357A 需要标准两声道 I2S 帧（LRC=fs、BCLK=32×fs@16bit），
 * 因此 TX 使用 16bit 立体声，把单声道样本复制到左右两个槽，
 * 这样无论 SD_MODE 配成 (L+R)/2、左声道还是右声道，都能得到正确幅度。
 *
 * 声道选择：INMP441 的 L/R 脚决定数据落在左槽还是右槽，另一个槽被器件三态。
 * 若读错槽，读到的是“悬空线拾取 BCLK 串扰”，表现为一串等幅反号的常数
 * （0x5555/0xAAAA → ±21845/±21846），此时 AFE 永远收不到有效语音、唤醒不触发。
 * 所以这里默认做一次上电自检：两个槽各采一段，选“不像串扰”的那个。
 */

#define MAX_FRAME_SAMPLES 1024   // 单次读写的样本上限（20ms@16k = 320）
#define PROBE_SAMPLES     512    // 声道自检每段采样点数（32ms）

#if CONFIG_VW_MIC_SLOT_RIGHT
#define MIC_SLOT_DEFAULT I2S_STD_SLOT_RIGHT
#define MIC_SLOT_NAME_DEFAULT "right"
#else
#define MIC_SLOT_DEFAULT I2S_STD_SLOT_LEFT
#define MIC_SLOT_NAME_DEFAULT "left"
#endif

static i2s_chan_handle_t s_rx_chan = NULL;  // 麦克风
static i2s_chan_handle_t s_tx_chan = NULL;  // 功放

static int32_t *s_rx_raw = NULL;     // 麦克风原始 32bit 样本缓冲
static int16_t *s_tx_stereo = NULL;  // 单声道 → 立体声（L/R 复制）缓冲

static i2s_std_slot_config_t s_rx_slot_cfg;  // 保存一份，自检时用于切换声道
static const char *s_mic_slot_name = MIC_SLOT_NAME_DEFAULT;

/* ---------- 内部：读一帧并转成 16bit ---------- */

static esp_err_t rx_read_i16(int16_t *buf, int samples)
{
    size_t got = 0;
    esp_err_t err = i2s_channel_read(s_rx_chan, s_rx_raw, samples * sizeof(int32_t),
                                     &got, portMAX_DELAY);
    if (err != ESP_OK) {
        return err;
    }
    int n = (int)(got / sizeof(int32_t));
    /* 32bit 槽内高 24bit 为 INMP441 有效数据 → 右移 16 位得到 16bit PCM */
    for (int i = 0; i < n; i++) {
        buf[i] = (int16_t)(s_rx_raw[i] >> 16);
    }
    return (n == samples) ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

/* ---------- 内部：声道质量评估 ---------- */

/*
 * 判据（两条同时满足才算“有效”）：
 *   has_data : RMS >= 2（真实麦克风静音时也有几~几十的本底噪声，正好 0 = 没数据）
 *   clean    : 不能是“等幅反号”的时钟串扰（alt_ratio > 0.8 且 RMS > 4000）
 * 真实音频在 16 kHz 下几乎不可能逐点等幅反号（那是 8 kHz 满幅方波），不会误判。
 */
typedef struct {
    const char *name;
    i2s_std_slot_mask_t mask;
    double rms;         // AC 有效值（已扣直流）
    double dc;          // 直流均值
    double alt_ratio;   // 等幅反号比例 → 时钟串扰特征
    double stuck_ratio; // 恰好等于 -1 或 0 的样本占比 → 数据线“静止”特征
    int    spikes;      // |v| > 1000 的孤立样本数
    bool has_data;
    bool clean;
} slot_probe_t;

/*
 * 判据说明（来自实测数据的反推）：
 *
 * 场景 A —— 麦克风正常工作：
 *   安静房间里 SD 线在持续输出 24bit 噪声（-87dBFS 量级，16bit 下约 ±1~3）
 *   与真实语音，样本**逐个变化**，stuck_ratio 通常 < 0.5。
 *
 * 场景 B —— 读错槽 / 线悬空：
 *   读到的是 BCLK 串扰，相邻样本等幅反号（0x5555 家族），alt_ratio → 1。
 *
 * 场景 C —— 麦克风完全没有驱动 SD（未上电 / CHIPEN 拉低 / SD 未接）：
 *   线上是静止电平，几乎**每个样本都精确等于 -1（0xFFFF）或 0**，
 *   stuck_ratio → 1，只有极个别相位滑动的孤立尖峰。
 *   实测：512 点里 510 个 -1 + 1~2 个尖峰 → rms 恰好等于 sqrt(2×21824²/512)≈1365。
 */

static esp_err_t rx_set_slot(i2s_std_slot_mask_t mask)
{
    i2s_channel_disable(s_rx_chan);
    s_rx_slot_cfg.slot_mask = mask;
    esp_err_t err = i2s_channel_reconfig_std_slot(s_rx_chan, &s_rx_slot_cfg);
    if (err == ESP_OK) {
        err = i2s_channel_enable(s_rx_chan);
    }
    return err;
}

/**
 * 采集一段数据并评估质量。
 *
 * 两个坑：
 *  1) i2s_channel_enable() 之后 DMA 缓冲里是全 0，必须丢弃第一块；
 *     否则读到 RMS=0 会被误判成“安静的正常数据”（实测就是这样漏掉了自检）。
 *  2) 真实麦克风静音时 RMS 仍有几~几十（本底噪声），正好为 0 说明根本没有数据。
 */
static void probe_slot(slot_probe_t *out, int16_t *buf)
{
    out->rms = 0;
    out->dc = 0;
    out->alt_ratio = 0;
    out->stuck_ratio = 0;
    out->spikes = 0;
    out->has_data = false;
    out->clean = false;

    if (rx_set_slot(out->mask) != ESP_OK) {
        return;
    }
    vTaskDelay(pdMS_TO_TICKS(60));       // 等麦克风上电建立 + DMA 填满
    rx_read_i16(buf, PROBE_SAMPLES);     // 丢弃第一块（切换残留/预填充）
    vTaskDelay(pdMS_TO_TICKS(40));
    if (rx_read_i16(buf, PROBE_SAMPLES) != ESP_OK) {
        return;
    }

    int64_t sum = 0;
    int alt = 0, n_neg1 = 0, n_zero = 0, spikes = 0;
    for (int i = 0; i < PROBE_SAMPLES; i++) {
        sum += buf[i];
        if (buf[i] == -1) {
            n_neg1++;
        } else if (buf[i] == 0) {
            n_zero++;
        }
    }
    out->dc = (double)sum / (double)PROBE_SAMPLES;

    /*
     * AC 能量（扣掉直流）：直流偏置本身不代表有语音。
     * spikes 也必须相对直流来判断 —— INMP441 存在几百~几千的固定偏置
     * （实测 dc≈-1911），若用 |v|>1000 判定，会让全部样本都被算成尖峰。
     */
    double energy = 0;
    for (int i = 0; i < PROBE_SAMPLES; i++) {
        double d = (double)buf[i] - out->dc;
        energy += d * d;
        if (d > 1000.0 || d < -1000.0) {
            spikes++;
        }
    }
    for (int i = 1; i < PROBE_SAMPLES; i++) {
        int32_t a = buf[i - 1], b = buf[i];
        int32_t aa = abs((int)a), ab = abs((int)b);
        if (((a > 0) != (b > 0)) && aa > 4000 && ab > 4000 && abs(aa - ab) < 1500) {
            alt++;
        }
    }

    out->rms = sqrt(energy / (double)PROBE_SAMPLES);
    out->alt_ratio = (double)alt / (double)(PROBE_SAMPLES - 1);
    /* 静止电平：绝大多数样本都精确等于同一个“卡住”的值 */
    out->stuck_ratio = (double)(n_neg1 > n_zero ? n_neg1 : n_zero) / (double)PROBE_SAMPLES;
    out->spikes = spikes;

    /*
     * 阈值 0.97 的依据：
     *   实测“麦克风没驱动”时 512 点里有 510 个精确等于 -1 → stuck≈0.996；
     *   而正常但安静的麦克风，本底噪声 σ≈2LSB，恰好等于 -1 的比例约 20%，
     *   两者相差一个数量级，0.97 既能抓到“静止线”，又不会误杀安静环境。
     */
    out->has_data = (out->stuck_ratio < 0.97);
    out->clean = out->has_data && !(out->alt_ratio > 0.80 && out->rms > 4000.0);

    ESP_LOGI(TAG, "声道自检[%s]: rms=%.0f dc=%.0f stuck=%.2f alt=%.2f spikes=%d clean=%d",
             out->name, out->rms, out->dc, out->stuck_ratio, out->alt_ratio,
             out->spikes, (int)out->clean);
    /* 原始 32bit 字（右移 16 位之前），用于判断是不是整字 0xFFFFFFFF */
    ESP_LOGI(TAG, "         raw32=%08x %08x %08x %08x",
             (unsigned)s_rx_raw[0], (unsigned)s_rx_raw[1],
             (unsigned)s_rx_raw[2], (unsigned)s_rx_raw[3]);
}

/* ---------- 内部：上电声道自检 ---------- */

static void rx_autodetect_slot(void)
{
#if CONFIG_VW_MIC_SLOT_AUTO
    int16_t *buf = (int16_t *)malloc(PROBE_SAMPLES * sizeof(int16_t));
    if (buf == NULL) {
        return;
    }

    /* 两个槽都测一遍，日志里能直接对比，便于判定是“读错槽”还是“麦克风没工作” */
    slot_probe_t left  = { .name = "left",  .mask = I2S_STD_SLOT_LEFT };
    slot_probe_t right = { .name = "right", .mask = I2S_STD_SLOT_RIGHT };
    probe_slot(&left, buf);
    probe_slot(&right, buf);

    if (!left.clean && !right.clean) {
        /*
         * 两个槽都不可用。先区分是“线静止”还是“时钟串扰”，这两种的硬件原因完全不同：
         *   静止 → 麦克风没有驱动 SD（没上电 / CHIPEN 拉低 / SD 没接 / 引脚顺序不符）
         *   串扰 → SD 悬空，拾取相邻的 1.024MHz BCLK
         */
        bool stuck = (left.stuck_ratio >= 0.97) || (right.stuck_ratio >= 0.97);
        if (stuck) {
            ESP_LOGE(TAG, "麦克风没有驱动 SD：GPIO%d 上采到的是**静止电平**"
                          "（几乎每个样本都精确等于 0xFFFF，只有 1~2 个孤立尖峰）。"
                          "这不是软件问题，请按顺序检查：", CONFIG_VW_MIC_DIN_GPIO);
            ESP_LOGE(TAG, "  1) CHIPEN(第 8 脚) 必须接 VDD；接 GND 或悬空 → 麦克风掉电，"
                          "SD 全程高阻（散装 INMP441 模块最常见的坑）");
            ESP_LOGE(TAG, "  2) VDD 是否真的 3.3V（万用表量，不是看丝印）");
            ESP_LOGE(TAG, "  3) SD 是否真的接到 GPIO%d：万用表通断档从模块焊盘量到排针",
                     CONFIG_VW_MIC_DIN_GPIO);
            ESP_LOGE(TAG, "  4) 模块丝印的引脚顺序（VDD/GND/SCK/WS/SD/L-R）是否与实际接线一致");
            ESP_LOGE(TAG, "  5) SD 到 GND 加 100kΩ 下拉（手册要求，悬空时更易串扰）");
            ESP_LOGE(TAG, "  6) 摄像头排线必须拔掉：ESP32-S3-CAM 的 GPIO6=VSYNC、GPIO4/5=SIOD/SIOC，"
                          "会与麦克风抢线");
            ESP_LOGE(TAG, "  万用表直流档快速判断：SD 正常应约 1.5~1.7V（数据在翻转）；"
                          "恒为 3.3V 或 0V = 没在驱动");
        } else {
            ESP_LOGE(TAG, "两个声道都像时钟串扰（alt=%.2f/%.2f）：SD 线悬空在拾取 BCLK。"
                          "请检查 1) SD 是否接到 GPIO%d 2) 加 100kΩ 下拉 3) CHIPEN 是否接 VDD",
                     left.alt_ratio, right.alt_ratio, CONFIG_VW_MIC_DIN_GPIO);
        }
        rx_set_slot(MIC_SLOT_DEFAULT);
        free(buf);
        return;
    }

    i2s_std_slot_mask_t pick = MIC_SLOT_DEFAULT;
    const char *pick_name = MIC_SLOT_NAME_DEFAULT;
    if (left.clean && !right.clean) {
        pick = I2S_STD_SLOT_LEFT;
        pick_name = "left";
    } else if (right.clean && !left.clean) {
        pick = I2S_STD_SLOT_RIGHT;
        pick_name = "right";
    } else {
        /* 两边都“看似正常”：优先用配置的默认槽，其次选串扰比例更低的 */
        if ((MIC_SLOT_DEFAULT == I2S_STD_SLOT_LEFT && left.clean) ||
            (MIC_SLOT_DEFAULT == I2S_STD_SLOT_RIGHT && right.clean)) {
            pick = MIC_SLOT_DEFAULT;
        } else {
            pick = (left.alt_ratio <= right.alt_ratio) ? I2S_STD_SLOT_LEFT : I2S_STD_SLOT_RIGHT;
        }
        pick_name = (pick == I2S_STD_SLOT_LEFT) ? "left" : "right";
    }

    rx_set_slot(pick);
    s_mic_slot_name = pick_name;
    ESP_LOGI(TAG, "采样声道确定为 %s（配置默认 %s）", pick_name, MIC_SLOT_NAME_DEFAULT);

    free(buf);
#endif
}

/* ---------- 对外接口 ---------- */

esp_err_t audio_i2s_init(void)
{
    int rate = CONFIG_VW_SAMPLE_RATE;

    /* ---------------- RX：INMP441（24bit I2S，32bit 槽宽 → BCLK=64×fs）---------------- */
    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    /*
     * auto_clear 必须显式打开（默认是 false）：
     * TX 没有数据可发时，DMA 会把"最后一段缓冲"循环重发，表现为喇叭持续蜂鸣/长鸣；
     * 打开后改为发 0，静音即真正静音。RX 侧同理，读空时拿到 0 而不是旧数据。
     */
    chan_cfg.auto_clear = true;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, NULL, &s_rx_chan), TAG, "new rx chan failed");

    i2s_std_config_t rx_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(rate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                       I2S_SLOT_MODE_MONO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = CONFIG_VW_MIC_BCLK_GPIO,
            .ws   = CONFIG_VW_MIC_WS_GPIO,
            .din  = CONFIG_VW_MIC_DIN_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    rx_cfg.slot_cfg.slot_mask = MIC_SLOT_DEFAULT;
    s_rx_slot_cfg = rx_cfg.slot_cfg;   // 保存供自检切换

    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_rx_chan, &rx_cfg), TAG, "init rx std failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_rx_chan), TAG, "enable rx failed");

    /* ---------------- TX：MAX98357A（16bit 标准立体声帧，无需 MCLK）---------------- */
    chan_cfg.id = I2S_NUM_1;
    ESP_RETURN_ON_ERROR(i2s_new_channel(&chan_cfg, &s_tx_chan, NULL), TAG, "new tx chan failed");

    i2s_std_config_t tx_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(rate),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT,
                                                       I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = CONFIG_VW_AMP_BCLK_GPIO,
            .ws   = CONFIG_VW_AMP_LRC_GPIO,
            .din  = I2S_GPIO_UNUSED,
            .dout = CONFIG_VW_AMP_DOUT_GPIO,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };
    ESP_RETURN_ON_ERROR(i2s_channel_init_std_mode(s_tx_chan, &tx_cfg), TAG, "init tx std failed");
    ESP_RETURN_ON_ERROR(i2s_channel_enable(s_tx_chan), TAG, "enable tx failed");

    s_rx_raw = (int32_t *)heap_caps_malloc(MAX_FRAME_SAMPLES * sizeof(int32_t),
                                           MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    s_tx_stereo = (int16_t *)heap_caps_malloc(MAX_FRAME_SAMPLES * 2 * sizeof(int16_t),
                                              MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (s_rx_raw == NULL || s_tx_stereo == NULL) {
        ESP_LOGE(TAG, "alloc i2s convert buffer failed");
        return ESP_ERR_NO_MEM;
    }

    ESP_LOGI(TAG, "i2s ready: rate=%d, mic=32bit slot/mono(%s) BCLK=%dHz, amp=16bit/stereo",
             rate, s_mic_slot_name, rate * 64);
    /* 把实际生效的引脚打出来，避免“代码/接线不一致”这种最难查的问题 */
    ESP_LOGI(TAG, "引脚: mic BCLK=GPIO%d WS=GPIO%d SD=GPIO%d | amp BCLK=GPIO%d LRC=GPIO%d DIN=GPIO%d",
             CONFIG_VW_MIC_BCLK_GPIO, CONFIG_VW_MIC_WS_GPIO, CONFIG_VW_MIC_DIN_GPIO,
             CONFIG_VW_AMP_BCLK_GPIO, CONFIG_VW_AMP_LRC_GPIO, CONFIG_VW_AMP_DOUT_GPIO);

    /* 上电自检：读错 L/R 槽时数据是时钟串扰，必须换槽，否则唤醒永远不会触发 */
    rx_autodetect_slot();

    return ESP_OK;
}

esp_err_t audio_i2s_read(int16_t *buf, int samples)
{
    if (buf == NULL || samples <= 0 || samples > MAX_FRAME_SAMPLES) {
        return ESP_ERR_INVALID_ARG;
    }
    return rx_read_i16(buf, samples);
}

esp_err_t audio_i2s_write(const int16_t *buf, int samples)
{
    if (buf == NULL || samples <= 0 || samples > MAX_FRAME_SAMPLES) {
        return ESP_ERR_INVALID_ARG;
    }

    /* 单声道 → 左右声道复制，保证 MAX98357A 任意 SD_MODE 配置都能正常发声 */
    for (int i = 0; i < samples; i++) {
        s_tx_stereo[2 * i]     = buf[i];
        s_tx_stereo[2 * i + 1] = buf[i];
    }

    size_t wrote = 0;
    return i2s_channel_write(s_tx_chan, s_tx_stereo, samples * 2 * sizeof(int16_t),
                             &wrote, portMAX_DELAY);
}

/**
 * 运行期麦克风数据质量判定。
 *
 * 三种形态的判别依据（都已用真实日志验证过）：
 *   静止线   ：几乎所有样本精确等于 -1（0xFFFF）或 0      → 麦克风没驱动 SD
 *   时钟串扰 ：相邻样本“等幅反号”（0x4000~0x7FFF 区间的 0101 图案）→ 读错槽或线悬空
 *   正常     ：样本逐个变化、无上述两种规律
 *
 * 正常但安静的麦克风，本底噪声 σ≈2LSB，恰好等于 -1 的比例约 20%，
 * 远低于 0.97 的阈值，不会被误判。
 */
audio_line_state_t audio_i2s_check_frame(const int16_t *buf, int n)
{
    if (buf == NULL || n < 2) {
        return AUDIO_LINE_OK;
    }

    int flat = 0, alt = 0;
    for (int i = 0; i < n; i++) {
        if (buf[i] == -1 || buf[i] == 0) {
            flat++;
        }
    }
    for (int i = 1; i < n; i++) {
        int a = buf[i - 1], b = buf[i];
        int aa = a < 0 ? -a : a;
        int ab = b < 0 ? -b : b;
        if (((a > 0) != (b > 0)) && aa > 4000 && ab > 4000) {
            int d = aa > ab ? aa - ab : ab - aa;
            if (d < 1500) {
                alt++;
            }
        }
    }

    if ((double)flat / (double)n >= 0.97) {
        return AUDIO_LINE_STUCK;
    }
    if ((double)alt / (double)(n - 1) >= 0.80) {
        return AUDIO_LINE_CROSSTALK;
    }
    return AUDIO_LINE_OK;
}
