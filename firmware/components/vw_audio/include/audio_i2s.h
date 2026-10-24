#pragma once
#include "esp_err.h"
#include <stdint.h>

/**
 * 驱动层：I2S 音频
 *
 * 麦克风 INMP441（I2S0-RX）
 *  - 器件侧要求：每个 WS 立体声帧 64 个 SCK（fSCK = 64 × fWS），24bit 数据、MSB-first；
 *    L/R 脚决定数据落在左槽（接地）还是右槽（接 VDD）。
 *  - 实现：32bit 槽宽 + 单声道 + 对应 slot_mask，使 BCLK = 64×fS；
 *    读到的 32bit 样本右移 16 位转成 16bit PCM（24bit 有效数据位于高位）。
 *
 * 功放 MAX98357A（I2S1-TX）
 *  - 器件侧要求：标准两声道 I2S 帧（LRC=fs、BCLK=32×fs@16bit），无需 MCLK；
 *  - 实现：16bit 立体声，单声道样本复制到 L/R 两个槽，适配任意 SD_MODE 接法。
 */
esp_err_t audio_i2s_init(void);

/** 阻塞读麦克风：把 INMP441 的 32bit 帧转成 int16 PCM，samples 为样本数（非字节） */
esp_err_t audio_i2s_read(int16_t *buf, int samples);

/** 阻塞写功放：输入单声道 int16 PCM，内部复制成立体声后送出 */
esp_err_t audio_i2s_write(const int16_t *buf, int samples);

/** 一帧麦克风数据的质量分类 */
typedef enum {
    AUDIO_LINE_OK = 0,      // 有真实信号（含安静时的本底噪声）
    AUDIO_LINE_STUCK,       // 数据线静止：几乎全是 0xFFFF/0x0000 → 麦克风没驱动
    AUDIO_LINE_CROSSTALK,   // 逐点等幅反号 → 读到的是 BCLK 串扰（读错槽/线悬空）
} audio_line_state_t;

/**
 * 判断一帧麦克风数据是否可信（运行期持续自检，开销极小）。
 *
 * 目的：麦克风不工作时，`mic peak` 会打印 16384~32767 这类数值，看着像“声音很大”，
 * 实际是 `0101` 串扰图案；这里把它识别出来，避免误判。
 *
 * @param buf  16bit 单声道 PCM
 * @param n    样本数
 */
audio_line_state_t audio_i2s_check_frame(const int16_t *buf, int n);
