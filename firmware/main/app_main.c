#include "audio_i2s.h"
#include "config_store.h"
#include "voice_service.h"
#include "wakeword.h"
#include "vw_log.h"
#include "vw_version.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define TAG "main"

/* 复位原因 → 可读字符串，便于现场判断是掉电、看门狗还是 panic */
static const char *reset_reason_str(esp_reset_reason_t r)
{
    switch (r) {
    case ESP_RST_POWERON:   return "POWERON";
    case ESP_RST_EXT:       return "EXT_PIN";
    case ESP_RST_SW:        return "SOFTWARE";
    case ESP_RST_PANIC:     return "PANIC";
    case ESP_RST_INT_WDT:   return "INT_WDT";
    case ESP_RST_TASK_WDT:  return "TASK_WDT";
    case ESP_RST_WDT:       return "OTHER_WDT";
    case ESP_RST_DEEPSLEEP: return "DEEPSLEEP";
    case ESP_RST_BROWNOUT:  return "BROWNOUT";
    case ESP_RST_SDIO:      return "SDIO";
    default:                return "UNKNOWN";
    }
}

/**
 * 初始化失败时的「故障保持」：只记录日志并周期提示，**不再 abort/restart**。
 *
 * 原实现用 ESP_ERROR_CHECK 包装，一旦初始化失败就 abort → panic → 芯片自动复位，
 * 表现为串口反复打印同一段启动日志、问题现场被冲掉（本项目 0.3.0 的无限重启即如此）。
 * 现在改为保持运行：设备停在可观测状态，日志可完整看到失败原因。
 */
static void hold_on_error(const char *stage, esp_err_t err)
{
    VW_LOG_E(TAG, VW_ERR_BOOT_FAIL, "初始化失败于 %s: %s(0x%X)，系统进入故障保持（不会重启）",
             stage, esp_err_to_name(err), err);
    ESP_LOGE(TAG, "排查顺序：1) model 分区是否已烧录 srmodels.bin "
                  "2) 分区体积是否 >= srmodels.bin 3) PSRAM 是否开启(Octal/80MHz) "
                  "4) data cache 是否 64KB/line 64B（详见 firmware/docs/03_故障排查.md）");

    while (true) {
        vTaskDelay(pdMS_TO_TICKS(30000));
        ESP_LOGE(TAG, "仍处于故障保持状态：%s 失败，free_heap=%u",
                 stage, (unsigned)esp_get_free_heap_size());
    }
}

/**
 * 程序入口：按分层顺序初始化。
 *  1) 服务层：NVS 配置持久化
 *  2) 驱动层：I2S 音频（麦克风 + 功放）
 *  3) 业务层：语音会话服务（内部初始化唤醒词/WiFi/网络并创建任务）
 */
void app_main(void)
{
    esp_reset_reason_t rr = esp_reset_reason();
    ESP_LOGI(TAG, "voice wake system boot (fw %s), reset=%s(%d), free_heap=%u",
             VW_FW_VERSION, reset_reason_str(rr), (int)rr,
             (unsigned)esp_get_free_heap_size());

    if (rr == ESP_RST_PANIC || rr == ESP_RST_INT_WDT ||
        rr == ESP_RST_TASK_WDT || rr == ESP_RST_WDT ||
        rr == ESP_RST_BROWNOUT) {
        ESP_LOGW(TAG, "上次启动是异常复位（%s），请结合上一轮 Backtrace 定位；"
                      "若反复出现，优先检查唤醒模型加载与 PSRAM 配置",
                 reset_reason_str(rr));
    }

    esp_err_t err = config_store_init();
    if (err != ESP_OK) {
        hold_on_error("config_store_init", err);
    }

    err = audio_i2s_init();
    if (err != ESP_OK) {
        hold_on_error("audio_i2s_init", err);
    }

    err = voice_service_init();
    if (err != ESP_OK) {
        hold_on_error("voice_service_init", err);
    }

    ESP_LOGI(TAG, "system ready, wakenet=%s", wakeword_model_name());
}
