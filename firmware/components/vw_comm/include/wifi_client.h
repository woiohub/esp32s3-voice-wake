#pragma once
#include "esp_err.h"
#include <stdint.h>
#include <stdbool.h>

/**
 * 通信层：WiFi STA。
 *
 * 只做 STA，**不再提供 AP 热点兜底**（AP 方案由蓝牙配网取代，原因见
 * components/vw_prov/include/ble_prov.h 顶部说明）。
 *
 * 配网触发由上层（vw_voice）决定：
 *  - 凭证为空 → wifi_client_start() 返回 ESP_ERR_NOT_FOUND，上层立刻进配网；
 *  - 有凭证但连续失败达到阈值 → 通过 fail 回调通知上层进配网。
 */

/** WiFi 连续失败回调。**在 WiFi 事件上下文里被调用，禁止阻塞**（丢队列后立刻返回）。 */
typedef void (*wifi_fail_cb_t)(int fails, void *arg);

/**
 * 初始化并启动 STA。
 * @param ssid  为空表示尚无凭证：仍会初始化协议栈，但不发起连接。
 * @return ESP_OK 已开始连接；ESP_ERR_NOT_FOUND 无凭证；其它为初始化失败。
 */
esp_err_t wifi_client_start(const char *ssid, const char *password);

/** 是否已保存凭证（ssid 非空）。 */
bool wifi_client_has_cred(void);

/** STA 是否已拿到 IP。 */
bool wifi_client_sta_connected(void);

/** 用新凭证立即重连（配网写入后做验证，不重启）。 */
esp_err_t wifi_client_reconnect_with(const char *ssid, const char *password);

/** 阻塞等待拿到 IP，返回是否成功。 */
bool wifi_client_wait_ip(uint32_t timeout_ms);

/** 取当前 IP 字符串（未连接时为空串）。 */
esp_err_t wifi_client_ip(char *out, unsigned cap);

/** 重置失败计数并立刻重连（配网超时退出后调用）。 */
void wifi_client_retry_now(void);

void wifi_client_set_fail_cb(wifi_fail_cb_t cb, void *arg);

/** 信号强度（dBm）；未连接返回 0（界面显示 "—"）。 */
int wifi_client_rssi(void);

/** 写 WiFi STA MAC（"AA:BB:CC:DD:EE:FF"），cap 至少 18。 */
void wifi_client_mac(char *out, unsigned cap);
