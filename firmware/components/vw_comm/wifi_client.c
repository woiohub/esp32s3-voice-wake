#include "wifi_client.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_log.h"
#include "esp_mac.h"
#include <stdio.h>
#include <string.h>

#define TAG "comm:wifi"
#define STA_RETRY_MAX 5   // 重试次数超过后进入 AP 配网模式

static volatile bool s_sta_up = false;
static volatile bool s_ap_mode = false;
static int s_retry = 0;

static void start_ap(void)
{
    if (s_ap_mode) {
        return;
    }
    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    char ssid[32];
    snprintf(ssid, sizeof(ssid), "VoiceWake-%02X%02X%02X", mac[3], mac[4], mac[5]);

    wifi_config_t ap_cfg = {0};
    strlcpy((char *)ap_cfg.ap.ssid, ssid, sizeof(ap_cfg.ap.ssid));
    ap_cfg.ap.ssid_len = strlen(ssid);
    ap_cfg.ap.channel = 6;
    ap_cfg.ap.max_connection = 2;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_AP));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_AP, &ap_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());
    s_ap_mode = true;
    ESP_LOGW(TAG, "STA 连接失败，已开启配网热点 SSID=%s", ssid);
}

static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        s_sta_up = false;
        if (s_retry++ < STA_RETRY_MAX) {
            ESP_LOGI(TAG, "STA 断开，重连中 (%d/%d)", s_retry, STA_RETRY_MAX);
            esp_wifi_connect();
        } else {
            start_ap();
        }
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        s_sta_up = true;
        s_retry = 0;
        ip_event_got_ip_t *evt = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "STA 已连接 IP=" IPSTR, IP2STR(&evt->ip_info.ip));

        /* 语音链路对时延/抖动敏感：关闭 modem sleep（默认 WIFI_PS_MIN_MODEM 会在
         * 每个 DTIM 周期休眠，增加 20–100ms 抖动，并频繁触发 ADDBA/DELBA 重建）。
         * 本设备为市电供电，优先保证实时性。 */
        ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_NONE));
        ESP_LOGI(TAG, "省电模式已关闭 (WIFI_PS_NONE)，降低音频链路抖动");
    }
}

esp_err_t wifi_client_start(const char *ssid, const char *password)
{
    if (ssid == NULL || ssid[0] == '\0') {
        ESP_LOGW(TAG, "未配置 WiFi 凭证，直接进入 AP 配网模式");
        ESP_ERROR_CHECK(esp_netif_init());
        ESP_ERROR_CHECK(esp_event_loop_create_default());
        esp_netif_create_default_wifi_sta();
        esp_netif_create_default_wifi_ap();
        wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
        ESP_ERROR_CHECK(esp_wifi_init(&cfg));
        start_ap();
        return ESP_OK;
    }

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();
    esp_netif_create_default_wifi_ap();  // 预建 netif，供后续兜底切换

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));

    wifi_config_t sta_cfg = {0};
    strlcpy((char *)sta_cfg.sta.ssid, ssid, sizeof(sta_cfg.sta.ssid));
    strlcpy((char *)sta_cfg.sta.password, password, sizeof(sta_cfg.sta.password));
    sta_cfg.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &sta_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    return ESP_OK;
}

bool wifi_client_sta_connected(void)
{
    return s_sta_up;
}

bool wifi_client_in_ap_mode(void)
{
    return s_ap_mode;
}

int wifi_client_rssi(void)
{
    wifi_ap_record_t ap;
    if (esp_wifi_sta_get_ap_info(&ap) == ESP_OK) {
        return ap.rssi;
    }
    return 0;   // 未连接或正在切换，上层显示 "—"
}

void wifi_client_mac(char *out, unsigned cap)
{
    uint8_t mac[6] = {0};
    if (out == NULL || cap < 18) {
        return;
    }
    if (esp_wifi_get_mac(WIFI_IF_STA, mac) != ESP_OK) {
        out[0] = '\0';
        return;
    }
    snprintf(out, cap, "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}
