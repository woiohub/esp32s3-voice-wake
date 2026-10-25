#pragma once
#include "esp_err.h"
#include <stdint.h>
#include <stdbool.h>

/**
 * 服务层：蓝牙（BLE）配网。
 *
 * 替代原 AP 热点配网：设备连不上路由器时开 BLE 广播，上位机用蓝牙把
 * WiFi 名称/密码 + 上位机 IP/端口写进来；连上 WiFi 后关闭蓝牙。
 *
 * 为什么用 BLE 而不是 AP：
 *  - AP 模式下设备是热点，PC 必须在同一网段才能配置，而设备又要去连另一个
 *    网段的上位机，TCP 天然通不了 —— 原方案实际上发不出配置；
 *  - BLE 是点对点链路，不依赖 IP，PC 只要蓝牙开着即可。
 *
 * 硬件影响：BLE 用片内射频，不占用任何 GPIO；但 WiFi/BLE 共用 2.4G 天线，
 * 因此**只在配网期间开启**，配网结束立即停广播（成功则重启彻底关闭）。
 *
 * GATT 契约（与上位机 host/voice_assistant/provision.py 一致）：
 *   Service   a0e0f000-1e4d-4f8a-9b6c-1d2e3f4a5b60
 *   CFG  (W)  a0e0f001-...  UTF-8 JSON:
 *             {"ssid":"..","password":"..","host_ip":"..","host_port":8888}
 *   STATE(R/N) a0e0f002-...  UTF-8 JSON:
 *             {"state":"idle|connecting|ok|fail","error":"..","ip":"..",
 *              "fw":"0.5.0","mac":"..","wakeword":".."}
 */

typedef struct {
    char ssid[33];
    char password[65];
    char host_ip[16];
    uint16_t host_port;
} ble_prov_cfg_t;

/** 收到配置回调。**在 BLE host 任务里被调用，禁止阻塞**（丢进队列后立刻返回）。 */
typedef void (*ble_prov_apply_cb_t)(const ble_prov_cfg_t *cfg, void *arg);

/**
 * 初始化控制器与 NimBLE 协议栈（不广播）。可重复调用（幂等）。
 *
 * **按需调用，不要在开机时调**：NimBLE 运行要占约 40~60KB 内部 RAM，
 * 而任务栈/播放队列都要内部 RAM。开机就初始化会把 WiFi/音频挤到创建失败
 * （v0.8.0 实测 playback task NO_MEM）。正确时机是进入配网的那一刻，
 * 配网结束后用 ble_prov_deinit() 把内存还回去。
 */
esp_err_t ble_prov_init(void);

/** 完全卸载蓝牙并把内存还给堆。没初始化过则直接返回 OK。 */
esp_err_t ble_prov_deinit(void);

/** 开始广播（设备名 VoiceWake-XXXX，XXXX = WiFi MAC 后三字节）。内部会先确保已初始化。 */
esp_err_t ble_prov_start(void);

/** 停止广播并断开当前连接 → 射频静默。可反复 start/stop。 */
esp_err_t ble_prov_stop(void);

/** 是否正在广播（配网模式是否激活）。 */
bool ble_prov_active(void);

void ble_prov_set_apply_cb(ble_prov_apply_cb_t cb, void *arg);

/** 向上位机推送状态（未连接时静默丢弃）；ip/error 可为 NULL。 */
void ble_prov_notify_state(const char *state, const char *error, const char *ip);
