#pragma once
#include "esp_log.h"
#include <stdint.h>

/**
 * 统一日志框架
 *
 * 约定：
 *  - 每个模块使用独立 TAG，格式 "<层>:<模块>"，例如：
 *      "main"       入口
 *      "drv:i2s"    驱动层
 *      "srv:wake"   服务层
 *      "comm:net"   通信层
 *      "app:voice"  业务层
 *  - 错误日志统一用 VW_LOG_E 携带错误码（见 vw_err_t），便于按码定位问题
 *  - 时间戳由 ESP_LOG 底层（esp_log_set_vprintf / 默认格式）提供，无需重复实现
 */

/* 统一错误码：高字节标识层级 */
typedef enum {
    VW_OK = 0,
    /* 通用 0x01xx */
    VW_ERR_NOMEM        = 0x0101,
    VW_ERR_INVALID_ARG  = 0x0102,
    VW_ERR_BOOT_FAIL    = 0x0103,   /* 启动阶段初始化失败（非致命，保持运行便于排查） */
    /* 驱动层 0x10xx */
    VW_ERR_I2S_INIT     = 0x1001,
    VW_ERR_I2S_READ     = 0x1002,
    VW_ERR_I2S_WRITE    = 0x1003,
    /* 服务层 0x20xx */
    VW_ERR_AFE_INIT     = 0x2001,
    VW_ERR_AFE_FEED     = 0x2002,
    VW_ERR_SR_MODEL_NOT_FOUND = 0x2003, /* model 分区缺失/未烧录/名字不匹配 */
    VW_ERR_NVS_OPEN     = 0x2101,
    /* 通信层 0x30xx */
    VW_ERR_WIFI_NOT_CONNECTED = 0x3001,
    VW_ERR_NET_NOT_CONNECTED  = 0x3002,
    VW_ERR_NET_SEND           = 0x3003,
    /* 业务层 0x40xx */
    VW_ERR_SESSION_BUSY       = 0x4001,
} vw_err_t;

/* 错误码 → 字符串 */
const char *vw_err_name(int code);

/* 统一错误日志：自动附带 [错误码:名称] 前缀 */
#define VW_LOG_E(tag, code, fmt, ...) \
    ESP_LOGE(tag, "[0x%04X:%s] " fmt, (unsigned)(code), vw_err_name(code), ##__VA_ARGS__)

#define VW_LOG_W(tag, fmt, ...) ESP_LOGW(tag, fmt, ##__VA_ARGS__)
#define VW_LOG_I(tag, fmt, ...) ESP_LOGI(tag, fmt, ##__VA_ARGS__)
#define VW_LOG_D(tag, fmt, ...) ESP_LOGD(tag, fmt, ##__VA_ARGS__)
