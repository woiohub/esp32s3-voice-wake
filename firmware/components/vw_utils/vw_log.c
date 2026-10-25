#include "vw_log.h"

/* 错误码 → 可读字符串，用于日志输出 */
const char *vw_err_name(int code)
{
    switch (code) {
    case VW_OK:                    return "OK";
    case VW_ERR_NOMEM:             return "NO_MEM";
    case VW_ERR_INVALID_ARG:       return "INVALID_ARG";
    case VW_ERR_BOOT_FAIL:         return "BOOT_FAIL";
    case VW_ERR_I2S_INIT:          return "I2S_INIT";
    case VW_ERR_I2S_READ:          return "I2S_READ";
    case VW_ERR_I2S_WRITE:         return "I2S_WRITE";
    case VW_ERR_AFE_INIT:          return "AFE_INIT";
    case VW_ERR_AFE_FEED:          return "AFE_FEED";
    case VW_ERR_SR_MODEL_NOT_FOUND: return "SR_MODEL_NOT_FOUND";
    case VW_ERR_NVS_OPEN:          return "NVS_OPEN";
    case VW_ERR_WIFI_NOT_CONNECTED: return "WIFI_NOT_CONNECTED";
    case VW_ERR_NET_NOT_CONNECTED:  return "NET_NOT_CONNECTED";
    case VW_ERR_NET_SEND:           return "NET_SEND";
    case VW_ERR_BLE_INIT:           return "BLE_INIT";
    case VW_ERR_BLE_GAP:            return "BLE_GAP";
    case VW_ERR_BLE_PROV_FAIL:      return "BLE_PROV_FAIL";
    case VW_ERR_SESSION_BUSY:       return "SESSION_BUSY";
    default:                       return "UNKNOWN";
    }
}
