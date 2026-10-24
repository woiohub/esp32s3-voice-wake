#pragma once
#include "esp_err.h"
#include <stdbool.h>

/**
 * 通信层：WiFi STA 客户端 + AP 配网兜底。
 * STA 连接失败重试超过阈值后，自动开启 AP 热点（SSID: VoiceWake-XXXX），
 * 供上位机直连后下发正确的 WiFi 凭证（wifi.set 控制帧，重启生效）。
 */

esp_err_t wifi_client_start(const char *ssid, const char *password);
bool wifi_client_sta_connected(void);
bool wifi_client_in_ap_mode(void);

/** 当前 STA 连接的路由器 RSSI(dBm)；未连接返回 0。 */
int wifi_client_rssi(void);

/** 当前 STA 的 MAC，格式 "AA:BB:CC:DD:EE:FF"；失败时写空串。 */
void wifi_client_mac(char *out, unsigned cap);
