/**
 * amp_test —— MAX98357A 最小播放测试（独立工程，不依赖主项目组件）
 *
 * ============================ 它解决什么问题 ============================
 * 主项目的开机蜂鸣听不到，需要判断是「I2S TX 没输出」「功放没供电/没使能」
 * 还是「喇叭/接线」的问题。这个工程只做一件事：持续播放可分辨的提示音。
 *
 * 三种 I2S 帧格式轮流播放，用「响几声」区分是哪种模式出声：
 *   模式 1：16bit 立体声（与主项目完全一致）→ 响 1 声
 *   模式 2：16bit 单声道（只发一个槽）      → 响 2 声
 *   模式 3：32bit 立体声                    → 响 3 声
 * 每种模式之前会打印该模式的 BCLK / LRC 频率，方便用万用表到模块焊盘上对照。
 *
 * ============================== 怎么判断 ==============================
 *   听到 1 声 → 说明 16bit 立体声可用（主项目配置正确），问题在别处
 *   听到 2/3 声 → 主项目的帧格式需要换成对应模式
 *   完全没声   → 优先量：VIN 电压、SD_MODE 电平、BCLK/LRC 是否有时钟
 *                （公式：BCLK = fs × 2 × 位宽，16bit→512kHz，32bit→1024kHz）
 *
 * ============================== 怎么运行 ==============================
 *   cd firmware/test/amp_test
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

static const char *TAG = "AMP_TEST";

/* ---------------- 硬件参数（与主项目一致，接线改了只改这里） ---------------- */

#define PIN_AMP_BCLK   16     /* MAX98357A BCLK */
#define PIN_AMP_LRC    15     /* MAX98357A LRC  */
#define PIN_AMP_DIN    7      /* MAX98357A DIN  */

#define SAMPLE_RATE    16000
#define CHUNK_SAMPLES  (SAMPLE_RATE / 1000 * 20)   /* 20ms = 320 */

/* ---------------- 内部状态 ---------------- */

static i2s_chan_handle_t s_tx = NULL;

/* 20ms 立体声 32bit 的缓冲：320 × 2 × 4 = 2560 字节，用 int32 数组保证 4 字节对齐 */
static int32_t s_buf32[CHUNK_SAMPLES * 2];

/* ---------------- I2S 打开 / 关闭 ---------------- */

static esp_err_t tx_open(i2s_data_bit_width_t bits, i2s_slot_mode_t mode)
{
    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_1, I2S_ROLE_MASTER);
    chan.dma_frame_num = CHUNK_SAMPLES;
    chan.auto_clear    = true;

    esp_err_t err = i2s_new_channel(&chan, &s_tx, NULL);
    if (err != ESP_OK) {
        return err;
    }

    i2s_std_config_t cfg = {
        .clk_cfg  = I2S_STD_CLK_DEFAULT_CONFIG(SAMPLE_RATE),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(bits, mode),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = PIN_AMP_BCLK,
            .ws   = PIN_AMP_LRC,
            .din  = I2S_GPIO_UNUSED,
            .dout = PIN_AMP_DIN,
            .invert_flags = { .mclk_inv = false, .bclk_inv = false, .ws_inv = false },
        },
    };

    err = i2s_channel_init_std_mode(s_tx, &cfg);
    if (err == ESP_OK) {
        err = i2s_channel_enable(s_tx);
    }
    if (err != ESP_OK) {
        i2s_del_channel(s_tx);
        s_tx = NULL;
    }
    return err;
}

static void tx_close(void)
{
    if (s_tx != NULL) {
        i2s_channel_disable(s_tx);
        i2s_del_channel(s_tx);
        s_tx = NULL;
    }
}

/* ---------------- 播放一个正弦音 ---------------- */

/**
 * 播放 freq Hz 的正弦音，时长 ms 毫秒。
 * 首尾各 10ms 线性淡入淡出，避免"咔哒"爆音（爆音会让人误以为是噪声故障）。
 * 返回值通过日志打印，用来确认数据真的写进了 I2S 外设。
 */
static esp_err_t play_tone(i2s_data_bit_width_t bits, i2s_slot_mode_t mode,
                           double freq, int ms, int amp)
{
    const int total = SAMPLE_RATE * ms / 1000;
    const int ch    = (mode == I2S_SLOT_MODE_STEREO) ? 2 : 1;
    const int bps   = (bits == I2S_DATA_BIT_WIDTH_32BIT) ? 4 : 2;
    const int fade  = SAMPLE_RATE / 100;          /* 10ms */
    const double two_pi = 6.283185307179586;
    esp_err_t last_err = ESP_OK;

    for (int done = 0; done < total; done += CHUNK_SAMPLES) {
        int n = (total - done) < CHUNK_SAMPLES ? (total - done) : CHUNK_SAMPLES;

        for (int i = 0; i < n; i++) {
            int k = done + i;
            double env = 1.0;
            if (k < fade) {
                env = (double)k / (double)fade;
            } else if (k > total - fade) {
                env = (double)(total - k) / (double)fade;
            }
            double v = (double)amp * env * sin(two_pi * freq * (double)k / (double)SAMPLE_RATE);
            int16_t s16 = (int16_t)v;

            if (bps == 4) {
                int32_t s32 = (int32_t)s16 << 16;      /* 24bit 左对齐到 32bit 槽 */
                for (int c = 0; c < ch; c++) {
                    s_buf32[i * ch + c] = s32;
                }
            } else {
                int16_t *p = (int16_t *)s_buf32;
                for (int c = 0; c < ch; c++) {
                    p[i * ch + c] = s16;
                }
            }
        }

        size_t bytes = (size_t)n * ch * bps;
        size_t wrote = 0;
        last_err = i2s_channel_write(s_tx, s_buf32, bytes, &wrote, pdMS_TO_TICKS(1000));
        if (last_err != ESP_OK) {
            ESP_LOGE(TAG, "  write 失败: %s", esp_err_to_name(last_err));
            return last_err;
        }
    }
    return last_err;
}

/* ---------------- 模式表 ---------------- */

typedef struct {
    i2s_data_bit_width_t bits;
    i2s_slot_mode_t      mode;
    const char          *name;
    int                  beeps;     /* 响几声，用来区分模式 */
} amp_mode_t;

static const amp_mode_t MODES[] = {
    { I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO, "16bit/立体声（与主项目一致）", 1 },
    { I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO,   "16bit/单声道",                 2 },
    { I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO, "32bit/立体声",                 3 },
};

/* ---------------- 入口 ---------------- */

void app_main(void)
{
    const int n_modes = sizeof(MODES) / sizeof(MODES[0]);

    ESP_LOGI(TAG, "===== MAX98357A 最小播放测试 =====");
    ESP_LOGI(TAG, "引脚: BCLK=GPIO%d LRC=GPIO%d DIN=GPIO%d",
             PIN_AMP_BCLK, PIN_AMP_LRC, PIN_AMP_DIN);
    ESP_LOGI(TAG, "听声音计数：响 1 声 = 16bit 立体声，2 声 = 16bit 单声道，3 声 = 32bit 立体声");
    ESP_LOGI(TAG, "若完全没声，请量模块焊盘：VIN(建议5V)、SD_MODE(3.3V)、BCLK/LRC 是否有时钟");
    ESP_LOGI(TAG, "引脚电平参考：BCLK 直流平均值≈1.6V，LRC≈1.6V");

    for (;;) {
        for (int m = 0; m < n_modes; m++) {
            const amp_mode_t *mo = &MODES[m];
            int bclk_khz = SAMPLE_RATE * 2 * ((mo->bits == I2S_DATA_BIT_WIDTH_32BIT) ? 32 : 16) / 1000;

            esp_err_t err = tx_open(mo->bits, mo->mode);
            if (err != ESP_OK) {
                ESP_LOGE(TAG, "[模式 %d] %s 打开失败: %s", m + 1, mo->name, esp_err_to_name(err));
                continue;
            }

            ESP_LOGI(TAG, "----- [模式 %d/%d] %s：即将响 %d 声（BCLK≈%dkHz, LRC=%dkHz）-----",
                     m + 1, n_modes, mo->name, mo->beeps, bclk_khz, SAMPLE_RATE / 1000);

            for (int b = 0; b < mo->beeps; b++) {
                ESP_LOGI(TAG, "  ♪ 第 %d 声", b + 1);
                play_tone(mo->bits, mo->mode, 1000.0, 300, 10000);
                vTaskDelay(pdMS_TO_TICKS(200));
            }

            ESP_LOGI(TAG, "  模式 %d 播放结束，静音 3 秒", m + 1);
            tx_close();
            vTaskDelay(pdMS_TO_TICKS(3000));
        }
        ESP_LOGI(TAG, "===== 一轮结束，5 秒后重复 =====");
        vTaskDelay(pdMS_TO_TICKS(5000));
    }
}
