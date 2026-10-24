#pragma once
#include "esp_err.h"
#include <stdint.h>
#include <stdbool.h>

/**
 * 服务层：配置持久化（NVS）。键见 firmware/docs/01_接口设计.md §9。
 */
typedef struct {
    uint8_t volume;         // 0..100
    bool wake_enable;
    bool ack_enable;        // 唤醒后是否先播报回复词（由上位机下发 TTS）
    char wifi_ssid[33];
    char wifi_pass[65];
    char host_ip[16];
    uint16_t host_port;
} vw_config_t;

esp_err_t config_store_init(void);
esp_err_t config_store_load(vw_config_t *cfg);
esp_err_t config_store_save(const vw_config_t *cfg);
esp_err_t config_store_set_volume(uint8_t vol);
uint8_t config_store_get_volume(void);
