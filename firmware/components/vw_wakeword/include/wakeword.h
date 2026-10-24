#pragma once
#include "esp_err.h"
#include <stdint.h>
#include <stdbool.h>

/**
 * 服务层：ESP-SR AFE 语音前端封装（WakeNet 唤醒 + AEC 回声消除 + VAD 端点检测）。
 *
 * 基于 esp-sr 1.9.x 的 AFE 接口（esp_afe_sr_iface.h / esp_afe_sr_models.h / esp_afe_config.h）：
 *  - S3/P4 上唤醒模型不编译进库，而是打包为 srmodels.bin 烧到 model 分区；
 *    因此 wakeword_init() 内部会先 esp_srmodel_init(分区名) 再创建 AFE，
 *    调用方无需（也不应）自行加载模型。
 *  - AEC：pcm_config = 1 麦克风 + 1 播放回环参考，feed 输入按 [mic, ref] 交错；
 *    播放期间 WakeNet 仍可检测 → 支持播报中打断（barge-in）
 */

typedef enum {
    VW_VAD_SILENCE = 0,
    VW_VAD_SPEECH  = 1,
} vw_vad_t;

/* 单次 feed 的识别结果 */
typedef struct {
    bool wake_detected;   // 本次 feed 命中唤醒词
    vw_vad_t vad;         // 最新 VAD 状态
} vw_sr_result_t;

typedef void (*vw_wake_cb_t)(void *arg);

/**
 * 初始化唤醒引擎：加载 model 分区 → 校验唤醒模型 → 创建 AFE。
 * 失败时返回 ESP_ERR_NOT_FOUND（模型缺失）/ ESP_FAIL（AFE 创建失败）等，
 * 调用方应记录日志并降级，**不要**直接 abort（否则会形成重启循环）。
 */
esp_err_t wakeword_init(vw_wake_cb_t cb, void *arg);

/* 返回实际生效的唤醒模型名（如 "wn9_hilexin"），未初始化时为空串 */
const char *wakeword_model_name(void);

/**
 * 喂入一帧数据（内部自动累积到引擎所需 chunk 再处理）。
 * @param mic 麦克风 PCM（16k/16bit/mono）
 * @param ref 播放回环 PCM（无播放时传全 0），长度与 mic 相同
 */
esp_err_t wakeword_feed(const int16_t *mic, const int16_t *ref, int samples,
                        vw_sr_result_t *out);

void wakeword_set_enabled(bool enable);
esp_err_t wakeword_deinit(void);
