#pragma once
#include "esp_err.h"
#include <stdint.h>
#include <stdbool.h>

/**
 * 通信层：TCP 客户端。连接上位机（host:port），自动重连 + 心跳保活。
 * 上行：audio/control 由业务层调用发送；下行：帧解析后经回调分发。
 */

typedef void (*vw_ctrl_handler_t)(const char *json, int len);
typedef void (*vw_audio_down_handler_t)(uint32_t session_id, uint8_t flags,
                                        const int16_t *pcm, int samples);
typedef void (*vw_conn_cb_t)(bool connected);

esp_err_t net_client_start(const char *host, uint16_t port);
void net_client_set_handlers(vw_ctrl_handler_t ctrl, vw_audio_down_handler_t audio,
                             vw_conn_cb_t conn);

esp_err_t net_client_send_control(const char *json);
esp_err_t net_client_send_audio(uint32_t session_id, uint8_t flags,
                                const int16_t *pcm, int samples);
bool net_client_connected(void);
