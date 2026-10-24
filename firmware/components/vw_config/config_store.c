#include "config_store.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "esp_check.h"
#include "esp_log.h"
#include <string.h>

#define TAG "srv:cfg"
#define NVS_NS "vw"

esp_err_t config_store_init(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    return err;
}

static esp_err_t get_str(nvs_handle_t h, const char *key, char *out, size_t cap)
{
    size_t len = cap;
    esp_err_t err = nvs_get_str(h, key, out, &len);
    if (err == ESP_ERR_NVS_NOT_FOUND) {
        out[0] = '\0';
        return ESP_OK;
    }
    return err;
}

esp_err_t config_store_load(vw_config_t *cfg)
{
    memset(cfg, 0, sizeof(*cfg));
    // 先填 menuconfig 出厂默认值，NVS 中已保存的值随后覆盖
    cfg->volume = CONFIG_VW_DEFAULT_VOLUME;
    cfg->wake_enable = true;
    cfg->ack_enable = true;     // 默认开启：唤醒后先播报回复词再采集
    cfg->host_port = CONFIG_VW_TCP_PORT;
    strlcpy(cfg->wifi_ssid, CONFIG_VW_DEFAULT_WIFI_SSID, sizeof(cfg->wifi_ssid));
    strlcpy(cfg->wifi_pass, CONFIG_VW_DEFAULT_WIFI_PASS, sizeof(cfg->wifi_pass));
    strlcpy(cfg->host_ip, CONFIG_VW_DEFAULT_HOST_IP, sizeof(cfg->host_ip));

    nvs_handle_t h;
    esp_err_t err = nvs_open(NVS_NS, NVS_READONLY, &h);
    if (err != ESP_OK) {
        return err;  // 首次开机无命名空间，走默认值
    }

    uint8_t vol = 0;
    size_t len = sizeof(vol);
    if (nvs_get_blob(h, "vol", &vol, &len) == ESP_OK) {
        cfg->volume = vol;
    }
    uint8_t wake = 0;
    len = sizeof(wake);
    if (nvs_get_blob(h, "wake_en", &wake, &len) == ESP_OK) {
        cfg->wake_enable = wake != 0;
    }
    uint8_t ack = 0;
    len = sizeof(ack);
    if (nvs_get_blob(h, "ack_en", &ack, &len) == ESP_OK) {
        cfg->ack_enable = ack != 0;
    }
    get_str(h, "wifi_ssid", cfg->wifi_ssid, sizeof(cfg->wifi_ssid));
    get_str(h, "wifi_pass", cfg->wifi_pass, sizeof(cfg->wifi_pass));
    get_str(h, "host_ip", cfg->host_ip, sizeof(cfg->host_ip));
    uint16_t port = 0;
    len = sizeof(port);
    if (nvs_get_blob(h, "host_port", &port, &len) == ESP_OK) {
        cfg->host_port = port;
    }

    nvs_close(h);
    return ESP_OK;
}

esp_err_t config_store_save(const vw_config_t *cfg)
{
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NVS_NS, NVS_READWRITE, &h), TAG, "open rw failed");

    ESP_ERROR_CHECK(nvs_set_blob(h, "vol", &cfg->volume, sizeof(cfg->volume)));
    uint8_t wake = cfg->wake_enable ? 1 : 0;
    ESP_ERROR_CHECK(nvs_set_blob(h, "wake_en", &wake, sizeof(wake)));
    uint8_t ack = cfg->ack_enable ? 1 : 0;
    ESP_ERROR_CHECK(nvs_set_blob(h, "ack_en", &ack, sizeof(ack)));
    ESP_ERROR_CHECK(nvs_set_str(h, "wifi_ssid", cfg->wifi_ssid));
    ESP_ERROR_CHECK(nvs_set_str(h, "wifi_pass", cfg->wifi_pass));
    ESP_ERROR_CHECK(nvs_set_str(h, "host_ip", cfg->host_ip));
    ESP_ERROR_CHECK(nvs_set_blob(h, "host_port", &cfg->host_port, sizeof(cfg->host_port)));

    ESP_ERROR_CHECK(nvs_commit(h));
    nvs_close(h);
    return ESP_OK;
}

esp_err_t config_store_set_volume(uint8_t vol)
{
    nvs_handle_t h;
    ESP_RETURN_ON_ERROR(nvs_open(NVS_NS, NVS_READWRITE, &h), TAG, "open rw failed");
    ESP_ERROR_CHECK(nvs_set_blob(h, "vol", &vol, sizeof(vol)));
    ESP_ERROR_CHECK(nvs_commit(h));
    nvs_close(h);
    return ESP_OK;
}

uint8_t config_store_get_volume(void)
{
    nvs_handle_t h;
    uint8_t vol = 0;
    size_t len = sizeof(vol);
    if (nvs_open(NVS_NS, NVS_READONLY, &h) == ESP_OK) {
        nvs_get_blob(h, "vol", &vol, &len);
        nvs_close(h);
    }
    return vol;
}
