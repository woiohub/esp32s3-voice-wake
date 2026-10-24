#pragma once
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/**
 * 通信层：上下位机帧协议（见 firmware/docs/01_接口设计.md §3）。
 * 帧 = 头(16B) + 载荷(N) + CRC16(2B)，多字节字段大端。
 */

#define VW_MAGIC0 0xAA
#define VW_MAGIC1 0x55
#define VW_VER    0x01

typedef enum {
    VW_TYPE_CONTROL    = 0x01,  // JSON 控制
    VW_TYPE_AUDIO_UP   = 0x02,  // 上行语音
    VW_TYPE_AUDIO_DOWN = 0x03,  // 下行 TTS
    VW_TYPE_HEARTBEAT  = 0x04,
    VW_TYPE_ACK        = 0x05,
} vw_type_t;

#define VW_HDR_LEN 16
#define VW_CRC_LEN 2

/* 音频子帧标志位（见 firmware/docs/01_接口设计.md §5） */
#define AUDIO_FLAG_START      0x01
#define AUDIO_FLAG_END        0x02
#define AUDIO_FLAG_HAS_SPEECH 0x04

uint16_t vw_crc16(const uint8_t *data, size_t len);

/* 构造一帧，返回总字节数；容量不足返回 0 */
size_t vw_build_frame(uint8_t *out, size_t cap, uint8_t type, uint32_t seq,
                      uint32_t ts_ms, const uint8_t *payload, uint32_t payload_len);

typedef void (*vw_frame_cb_t)(uint8_t type, uint32_t seq, uint32_t ts_ms,
                              const uint8_t *payload, uint32_t len, void *arg);

/* 流式解析器：处理 TCP 粘包/半包 */
typedef struct {
    uint8_t buf[2048];
    size_t len;
    vw_frame_cb_t cb;
    void *arg;
} vw_parser_t;

void vw_parser_init(vw_parser_t *p, vw_frame_cb_t cb, void *arg);
size_t vw_parser_feed(vw_parser_t *p, const uint8_t *data, size_t len);
