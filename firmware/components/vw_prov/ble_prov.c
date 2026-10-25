#include "ble_prov.h"
#include "vw_log.h"
#include "vw_version.h"

#include "sdkconfig.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "cJSON.h"

#include <stdio.h>
#include <string.h>

#define TAG "srv:prov"

/* ------------------------------------------------------------------
 * 未启用 NimBLE 时提供空实现：工程仍可编译（只是配网不可用）。
 * 启用方式见 sdkconfig.defaults 里的 CONFIG_BT_NIMBLE_ENABLED。
 * ------------------------------------------------------------------ */
#if !defined(CONFIG_BT_NIMBLE_ENABLED) || !CONFIG_BT_NIMBLE_ENABLED

esp_err_t ble_prov_init(void)
{
    VW_LOG_W(TAG, "蓝牙未启用（menuconfig → Component config → Bluetooth → NimBLE），配网不可用");
    return ESP_ERR_NOT_SUPPORTED;
}
esp_err_t ble_prov_deinit(void) { return ESP_OK; }
esp_err_t ble_prov_start(void) { return ESP_ERR_NOT_SUPPORTED; }
esp_err_t ble_prov_stop(void) { return ESP_OK; }
bool ble_prov_active(void) { return false; }
void ble_prov_set_apply_cb(ble_prov_apply_cb_t cb, void *arg) { (void)cb; (void)arg; }
void ble_prov_notify_state(const char *s, const char *e, const char *ip)
{
    (void)s; (void)e; (void)ip;
}

#else   /* ===================== 正式实现 ===================== */

#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_att.h"
#include "host/util/util.h"
#include "services/gap/ble_svc_gap.h"
#include "services/gatt/ble_svc_gatt.h"
#include "esp_bt.h"

/* UUID（与上位机 host/voice_assistant/provision.py 必须一致）
 *   Service : a0e0f000-1e4d-4f8a-9b6c-1d2e3f4a5b60
 *   CFG (W) : a0e0f001-...    JSON 写入配置
 *   STATE   : a0e0f002-...    JSON 读取 / 通知状态
 * 注意：BLE_UUID128_INIT 的参数是**小端**字节序（与 UUID 字符串写法相反）。
 */
static const ble_uuid128_t UUID_SVC = BLE_UUID128_INIT(
    0x60, 0x5b, 0x4a, 0x3f, 0x2e, 0x1d, 0x6c, 0x9b,
    0x8a, 0x4f, 0x4d, 0x1e, 0x00, 0xf0, 0xe0, 0xa0);

static const ble_uuid128_t UUID_CFG = BLE_UUID128_INIT(
    0x60, 0x5b, 0x4a, 0x3f, 0x2e, 0x1d, 0x6c, 0x9b,
    0x8a, 0x4f, 0x4d, 0x1e, 0x01, 0xf0, 0xe0, 0xa0);

static const ble_uuid128_t UUID_STATE = BLE_UUID128_INIT(
    0x60, 0x5b, 0x4a, 0x3f, 0x2e, 0x1d, 0x6c, 0x9b,
    0x8a, 0x4f, 0x4d, 0x1e, 0x02, 0xf0, 0xe0, 0xa0);

static bool s_active;                       /* 是否处于配网窗口（广播中） */
static bool s_stack_inited;                 /* NimBLE 是否已初始化（按需，见 ble_prov.h） */
static uint8_t s_own_addr_type;
static uint16_t s_conn = BLE_HS_CONN_HANDLE_NONE;
static uint16_t s_state_handle;             /* STATE 特征值句柄，notify 时要用 */

static char s_name[28] = "VoiceWake";
static char s_mac[18];
static char s_state[16] = "idle";
static char s_error[72];
static char s_ip[16];

static ble_prov_apply_cb_t s_apply_cb;
static void *s_apply_arg;

static int ble_gap_event_cb(struct ble_gap_event *event, void *arg);

/* ---------------- 工具 ---------------- */

static void sanitize(char *dst, size_t cap, const char *src)
{
    size_t i = 0;
    if (dst == NULL || cap == 0) {
        return;
    }
    for (; src && *src && i + 1 < cap; src++) {
        char c = *src;
        dst[i++] = (c == '"' || c == '\\' || c == '\n' || c == '\r') ? ' ' : c;
    }
    dst[i] = '\0';
}

static int build_state_json(char *out, size_t cap)
{
    char err[80];
    sanitize(err, sizeof(err), s_error);
    return snprintf(out, cap,
                    "{\"state\":\"%s\",\"error\":\"%s\",\"ip\":\"%s\","
                    "\"fw\":\"%s\",\"mac\":\"%s\"}",
                    s_state, err, s_ip, VW_FW_VERSION, s_mac);
}

/* ---------------- 广播 ---------------- */

static void ble_advertise(void)
{
    struct ble_hs_adv_fields fields;
    memset(&fields, 0, sizeof(fields));
    fields.flags = BLE_HS_ADV_F_DISC_GEN | BLE_HS_ADV_F_BREDR_UNSUP;
    fields.name = (uint8_t *)s_name;
    fields.name_len = (uint8_t)strlen(s_name);
    fields.name_is_complete = 1;

    int rc = ble_gap_adv_set_fields(&fields);
    if (rc != 0) {
        VW_LOG_E(TAG, VW_ERR_BLE_GAP, "adv fields rc=%d", rc);
        return;
    }

    struct ble_gap_adv_params params;
    memset(&params, 0, sizeof(params));
    params.conn_mode = BLE_GAP_CONN_MODE_UND;   /* 可被连接 */
    params.disc_mode = BLE_GAP_DISC_MODE_GEN;   /* 可被发现 */

    rc = ble_gap_adv_start(s_own_addr_type, NULL, BLE_HS_FOREVER,
                           &params, ble_gap_event_cb, NULL);
    if (rc != 0 && rc != BLE_HS_EALREADY) {
        VW_LOG_E(TAG, VW_ERR_BLE_GAP, "adv start rc=%d", rc);
    }
}

/* ---------------- GAP 事件 ---------------- */

static int ble_gap_event_cb(struct ble_gap_event *event, void *arg)
{
    (void)arg;
    switch (event->type) {
    case BLE_GAP_EVENT_CONNECT:
        if (event->connect.status == 0) {
            s_conn = event->connect.conn_handle;
            ESP_LOGI(TAG, "上位机已连接（conn=%d）", s_conn);
        } else if (s_active) {
            ble_advertise();
        }
        break;

    case BLE_GAP_EVENT_DISCONNECT:
        ESP_LOGI(TAG, "上位机断开（reason=0x%x）", event->disconnect.reason);
        s_conn = BLE_HS_CONN_HANDLE_NONE;
        if (s_active) {
            ble_advertise();        /* 配网窗口内继续可被连接 */
        }
        break;

    case BLE_GAP_EVENT_ADV_COMPLETE:
        if (s_active) {
            ble_advertise();
        }
        break;

    case BLE_GAP_EVENT_MTU:
        ESP_LOGI(TAG, "MTU 协商为 %d", event->mtu.value);
        break;

    default:
        break;
    }
    return 0;
}

/* ---------------- GATT 访问 ---------------- */

static int parse_cfg(const char *json, ble_prov_cfg_t *out)
{
    cJSON *root = cJSON_Parse(json);
    if (root == NULL) {
        return -1;
    }
    memset(out, 0, sizeof(*out));

    const char *ssid = cJSON_GetStringValue(cJSON_GetObjectItem(root, "ssid"));
    const char *pass = cJSON_GetStringValue(cJSON_GetObjectItem(root, "password"));
    const char *ip = cJSON_GetStringValue(cJSON_GetObjectItem(root, "host_ip"));
    cJSON *port = cJSON_GetObjectItem(root, "host_port");

    if (ssid == NULL || ssid[0] == '\0') {
        cJSON_Delete(root);
        return -2;
    }
    strlcpy(out->ssid, ssid, sizeof(out->ssid));
    if (pass != NULL) {
        strlcpy(out->password, pass, sizeof(out->password));
    }
    if (ip != NULL) {
        strlcpy(out->host_ip, ip, sizeof(out->host_ip));
    }
    out->host_port = (cJSON_IsNumber(port) && port->valuedouble > 0 &&
                      port->valuedouble <= 65535)
                     ? (uint16_t)port->valuedouble
                     : (uint16_t)CONFIG_VW_TCP_PORT;

    cJSON_Delete(root);
    return 0;
}

static int gatt_access_cb(uint16_t conn, uint16_t attr,
                          struct ble_gatt_access_ctxt *ctxt, void *arg)
{
    (void)conn;
    (void)attr;
    (void)arg;

    switch (ctxt->op) {
    case BLE_GATT_ACCESS_OP_READ_CHR: {
        if (ble_uuid_cmp(ctxt->chr->uuid, &UUID_STATE.u) != 0) {
            return BLE_ATT_ERR_UNLIKELY;
        }
        char buf[224];
        int n = build_state_json(buf, sizeof(buf));
        if (n <= 0 || os_mbuf_append(ctxt->om, buf, (uint16_t)n) != 0) {
            return BLE_ATT_ERR_INSUFFICIENT_RES;
        }
        return 0;
    }

    case BLE_GATT_ACCESS_OP_WRITE_CHR: {
        if (ble_uuid_cmp(ctxt->chr->uuid, &UUID_CFG.u) != 0) {
            return BLE_ATT_ERR_UNLIKELY;
        }
        char buf[320];
        uint16_t len = 0;
        if (ble_hs_mbuf_to_flat(ctxt->om, buf, sizeof(buf) - 1, &len) != 0) {
            return BLE_ATT_ERR_UNLIKELY;
        }
        buf[len] = '\0';

        ble_prov_cfg_t cfg;
        int perr = parse_cfg(buf, &cfg);
        if (perr != 0) {
            strlcpy(s_state, "fail", sizeof(s_state));
            strlcpy(s_error, (perr == -2) ? "ssid required" : "bad json",
                    sizeof(s_error));
            ESP_LOGW(TAG, "配置写入非法：%s", s_error);
            return 0;       /* 回成功，让上位机从 STATE 读到失败原因 */
        }

        ESP_LOGI(TAG, "收到配网配置 ssid=%s host=%s:%u",
                 cfg.ssid, cfg.host_ip, (unsigned)cfg.host_port);

        /* 只做转交：真正的写 NVS / 连 WiFi 放到配网任务里（此处不能阻塞 host 任务） */
        if (s_apply_cb) {
            s_apply_cb(&cfg, s_apply_arg);
        } else {
            strlcpy(s_state, "fail", sizeof(s_state));
            strlcpy(s_error, "apply handler missing", sizeof(s_error));
        }
        return 0;
    }

    default:
        return BLE_ATT_ERR_UNLIKELY;
    }
}

/* ---------------- GATT 表 ---------------- */

static const struct ble_gatt_chr_def svc_chrs[] = {
    {
        .uuid = &UUID_CFG.u,
        .access_cb = gatt_access_cb,
        .flags = BLE_GATT_CHR_F_WRITE | BLE_GATT_CHR_F_WRITE_NO_RSP,
    },
    {
        .uuid = &UUID_STATE.u,
        .access_cb = gatt_access_cb,
        .flags = BLE_GATT_CHR_F_READ | BLE_GATT_CHR_F_NOTIFY,
        .val_handle = &s_state_handle,
    },
    { 0 },
};

static const struct ble_gatt_svc_def gatt_svcs[] = {
    {
        .type = BLE_GATT_SVC_TYPE_PRIMARY,
        .uuid = &UUID_SVC.u,
        .characteristics = svc_chrs,
    },
    { 0 },
};

/* ---------------- 协议栈生命周期 ---------------- */

static void on_sync(void)
{
    if (ble_hs_util_ensure_addr(0) != 0) {
        VW_LOG_E(TAG, VW_ERR_BLE_GAP, "no usable BLE address");
        return;
    }
    if (ble_hs_id_infer_auto(0, &s_own_addr_type) != 0) {
        VW_LOG_E(TAG, VW_ERR_BLE_GAP, "infer addr type failed");
        return;
    }
    ESP_LOGI(TAG, "BLE 就绪，设备名 %s", s_name);
    if (s_active) {
        ble_advertise();
    }
}

static void on_reset(int reason)
{
    VW_LOG_W(TAG, "BLE host reset, reason=%d", reason);
}

static void host_task(void *param)
{
    (void)param;
    nimble_port_run();
    nimble_port_freertos_deinit();
}

esp_err_t ble_prov_init(void)
{
    if (s_stack_inited) {
        return ESP_OK;                  /* 幂等 */
    }

    uint8_t mac[6] = {0};
    esp_read_mac(mac, ESP_MAC_WIFI_STA);
    snprintf(s_name, sizeof(s_name), "%s-%02X%02X%02X",
             CONFIG_VW_PROV_NAME_PREFIX, mac[3], mac[4], mac[5]);
    snprintf(s_mac, sizeof(s_mac), "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);

    esp_err_t err = nimble_port_init();
    if (err != ESP_OK) {
        VW_LOG_E(TAG, VW_ERR_BLE_INIT, "nimble_port_init: %s", esp_err_to_name(err));
        return err;
    }

    ble_hs_cfg.sync_cb = on_sync;
    ble_hs_cfg.reset_cb = on_reset;

    ble_svc_gap_init();
    ble_svc_gatt_init();

    int rc = ble_gatts_count_cfg(gatt_svcs);
    if (rc == 0) {
        rc = ble_gatts_add_svcs(gatt_svcs);
    }
    if (rc != 0) {
        VW_LOG_E(TAG, VW_ERR_BLE_INIT, "gatt register rc=%d", rc);
        return ESP_FAIL;
    }
    ble_svc_gap_device_name_set(s_name);

    /* 配置 JSON 约 140~200 字节，默认 MTU(23) 放不下，抬高协商上限 */
    ble_att_set_preferred_mtu(185);

    nimble_port_freertos_init(host_task);
    s_stack_inited = true;
    ESP_LOGI(TAG, "蓝牙已初始化（%s）", s_name);
    return ESP_OK;
}

esp_err_t ble_prov_deinit(void)
{
    if (!s_stack_inited) {
        return ESP_OK;
    }
    s_active = false;

    /* IDF 文档的标准卸载顺序：stop host → deinit host → 关控制器 → 还内存。
     * esp_bt_mem_release(ESP_BT_MODE_BLE) 才是把 ~50KB 内部 RAM 还回堆的关键一步。 */
    int rc = nimble_port_stop();
    if (rc == 0) {
        nimble_port_deinit();
    } else {
        VW_LOG_W(TAG, "nimble_port_stop rc=%d（继续卸载控制器）", rc);
    }
    if (esp_bt_controller_get_status() != ESP_BT_CONTROLLER_STATUS_IDLE) {
        esp_bt_controller_disable();
        esp_bt_controller_deinit();
    }
    esp_bt_mem_release(ESP_BT_MODE_BLE);
    s_stack_inited = false;
    s_conn = BLE_HS_CONN_HANDLE_NONE;
    ESP_LOGI(TAG, "蓝牙已卸载，内存已归还");
    return ESP_OK;
}

esp_err_t ble_prov_start(void)
{
    if (s_active) {
        return ESP_OK;
    }
    /* 按需初始化：内部 RAM 紧张，正常语音链路期间不应有蓝牙驻留 */
    esp_err_t err = ble_prov_init();
    if (err != ESP_OK) {
        return err;
    }
    s_active = true;
    strlcpy(s_state, "idle", sizeof(s_state));
    s_error[0] = '\0';
    s_ip[0] = '\0';

    ESP_LOGW(TAG, "进入蓝牙配网：广播 %s（%d 秒无操作则超时退出）",
             s_name, CONFIG_VW_PROV_TIMEOUT_S);
    ble_advertise();
    return ESP_OK;
}

esp_err_t ble_prov_stop(void)
{
    if (!s_active && s_conn == BLE_HS_CONN_HANDLE_NONE) {
        return ESP_OK;
    }
    s_active = false;
    ble_gap_adv_stop();
    if (s_conn != BLE_HS_CONN_HANDLE_NONE) {
        ble_gap_terminate(s_conn, 0x13 /* REM_USER_CONN_TERM */);
        s_conn = BLE_HS_CONN_HANDLE_NONE;
    }
    ESP_LOGI(TAG, "已停止蓝牙广播（射频静默）");
    return ESP_OK;
}

bool ble_prov_active(void)
{
    return s_active;
}

void ble_prov_set_apply_cb(ble_prov_apply_cb_t cb, void *arg)
{
    s_apply_cb = cb;
    s_apply_arg = arg;
}

void ble_prov_notify_state(const char *state, const char *error, const char *ip)
{
    strlcpy(s_state, (state && state[0]) ? state : "idle", sizeof(s_state));
    strlcpy(s_error, error ? error : "", sizeof(s_error));
    if (ip) {
        strlcpy(s_ip, ip, sizeof(s_ip));
    } else {
        s_ip[0] = '\0';
    }
    ESP_LOGI(TAG, "配网状态 → %s%s%s", s_state, s_error[0] ? " " : "", s_error);

    if (s_conn == BLE_HS_CONN_HANDLE_NONE) {
        return;             /* 上位机已断开，无须通知 */
    }
    char buf[224];
    int n = build_state_json(buf, sizeof(buf));
    if (n <= 0) {
        return;
    }
    struct os_mbuf *om = ble_hs_mbuf_from_flat(buf, (uint16_t)n);
    if (om == NULL) {
        return;
    }
    if (ble_gatts_notify_custom(s_conn, s_state_handle, om) != 0) {
        os_mbuf_free_chain(om);
    }
}

#endif  /* CONFIG_BT_NIMBLE_ENABLED */
