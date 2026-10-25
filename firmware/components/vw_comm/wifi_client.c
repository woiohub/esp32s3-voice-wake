#include "wifi_client.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_check.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <stdio.h>
#include <string.h>

#define TAG "comm:wifi"

static volatile bool s_sta_up = false;
static volatile int s_retry = 0;
static volatile bool s_inited = false;
static volatile bool s_fail_notified = false;   /* 每轮只通知一次，避免反复进配网 */
static bool s_has_cred = false;

static char s_ip[16];

static wifi_fail_cb_t s_fail_cb;
static void *s_fail_arg;

static void notify_fail(void)
{
    if (s_fail_cb && !s_fail_notified) {
        s_fail_notified = true;
        s_fail_cb(s_retry, s_fail_arg);
    }
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;

    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        if (s_has_cred) {
            esp_wifi_connect();
        } else {
            ESP_LOGW(TAG, "无 WiFi 凭证，等待蓝牙配网");
        }
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_sta_up = false;
        s_ip[0] = '\0';
        s_retry++;

        if (s_retry < CONFIG_VW_STA_FAIL_THRESHOLD) {
            ESP_LOGI(TAG, "STA 断开，重连中 (%d/%d)",
                     s_retry, CONFIG_VW_STA_FAIL_THRESHOLD);
            esp_wifi_connect();
        } else {
            /* 达到阈值：只通知上层，不在事件上下文里做任何耗时动作 */
            ESP_LOGW(TAG, "WiFi 连续失败 %d 次，请求进入配网", s_retry);
            notify_fail();
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *evt = (ip_event_got_ip_t *)data;
        s_sta_up = true;
        s_retry = 0;
        s_fail_notified = false;
        snprintf(s_ip, sizeof(s_ip), IPSTR, IP2STR(&evt->ip_info.ip));
        ESP_LOGI(TAG, "STA 已连接 IP=%s", s_ip);

        /* 语音链路对时延/抖动敏感：关闭 modem sleep（默认 WIFI_PS_MIN_MODEM 会在
         * 每个 DTIM 周期休眠，增加 20–100ms 抖动，并频繁触发 ADDBA/DELBA 重建）。
         * 本设备为市电供电，优先保证实时性。 */
        esp_wifi_set_ps(WIFI_PS_NONE);
        ESP_LOGI(TAG, "省电模式已关闭 (WIFI_PS_NONE)，降低音频链路抖动");
    }
}

static esp_err_t wifi_hw_init(void)
{
    if (s_inited) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif init failed");
    esp_err_t e = esp_event_loop_create_default();
    if (e != ESP_OK && e != ESP_ERR_INVALID_STATE) {
        ESP_LOGE(TAG, "event loop create failed: %s", esp_err_to_name(e));
        return e;
    }
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_RETURN_ON_ERROR(esp_wifi_init(&cfg), TAG, "wifi init failed");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID,
                                                   wifi_event_handler, NULL),
                        TAG, "reg wifi event failed");
    ESP_RETURN_ON_ERROR(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP,
                                                   wifi_event_handler, NULL),
                        TAG, "reg ip event failed");
    s_inited = true;
    return ESP_OK;
}

esp_err_t wifi_client_start(const char *ssid, const char *password)
{
    esp_err_t err = wifi_hw_init();
    if (err != ESP_OK) {
        return err;
    }

    s_has_cred = (ssid != NULL && ssid[0] != '\0');

    wifi_config_t sta_cfg = {0};
    if (s_has_cred) {
        strlcpy((char *)sta_cfg.sta.ssid, ssid, sizeof(sta_cfg.sta.ssid));
        if (password != NULL) {
            strlcpy((char *)sta_cfg.sta.password, password, sizeof(sta_cfg.sta.password));
        }
    }
    sta_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "set mode failed");
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg), TAG, "set cfg failed");
    ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "wifi start failed");

    if (!s_has_cred) {
        ESP_LOGW(TAG, "未配置 WiFi 凭证，交由上层进入蓝牙配网");
        return ESP_ERR_NOT_FOUND;
    }
    return ESP_OK;
}

bool wifi_client_has_cred(void)
{
    return s_has_cred;
}

bool wifi_client_sta_connected(void)
{
    return s_sta_up;
}

esp_err_t wifi_client_reconnect_with(const char *ssid, const char *password)
{
    if (ssid == NULL || ssid[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    ESP_RETURN_ON_ERROR(wifi_hw_init(), TAG, "hw init failed");

    wifi_config_t sta_cfg = {0};
    strlcpy((char *)sta_cfg.sta.ssid, ssid, sizeof(sta_cfg.sta.ssid));
    if (password != NULL) {
        strlcpy((char *)sta_cfg.sta.password, password, sizeof(sta_cfg.sta.password));
    }
    sta_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    s_retry = 0;
    s_fail_notified = false;
    s_sta_up = false;
    s_ip[0] = '\0';
    s_has_cred = true;

    esp_wifi_disconnect();      /* 未连接时会返回错误，忽略即可 */
    ESP_RETURN_ON_ERROR(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg), TAG, "set cfg failed");
    return esp_wifi_connect();
}

bool wifi_client_wait_ip(uint32_t timeout_ms)
{
    uint32_t waited = 0;
    while (waited < timeout_ms) {
        if (s_sta_up) {
            return true;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
        waited += 100;
    }
    return s_sta_up;
}

esp_err_t wifi_client_ip(char *out, unsigned cap)
{
    if (out == NULL || cap == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    strlcpy(out, s_ip, cap);
    return s_ip[0] ? ESP_OK : ESP_ERR_INVALID_STATE;
}

void wifi_client_retry_now(void)
{
    s_retry = 0;
    s_fail_notified = false;
    if (s_has_cred && s_inited) {
        ESP_LOGI(TAG, "重置失败计数，重新尝试连接");
        esp_wifi_connect();
    }
}

void wifi_client_set_fail_cb(wifi_fail_cb_t cb, void *arg)
{
    s_fail_cb = cb;
    s_fail_arg = arg;
}

int wifi_client_rssi(void)
{
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        return ap.rssi;
    }
    return 0;   /* 未连接或正在切换，上层显示 "—" */
}

void wifi_client_mac(char *out, unsigned cap)
{
    uint8_t mac[6] = {0};
    if (out == NULL || cap < 18) {
        return;
    }
    /* 用 efuse 出厂 MAC：与 WiFi STA MAC 相同，且不依赖 esp_wifi_init 是否已执行 */
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK) {
        out[0] = '\0';
        return;
    }
    snprintf(out, cap, "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}
