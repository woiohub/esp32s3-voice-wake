#include "net_proto.h"
#include <string.h>

uint16_t vw_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; j++) {
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);
        }
    }
    return crc;
}

static void put_u32be(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

static uint32_t get_u32be(const uint8_t *p)
{
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

size_t vw_build_frame(uint8_t *out, size_t cap, uint8_t type, uint32_t seq,
                      uint32_t ts_ms, const uint8_t *payload, uint32_t payload_len)
{
    size_t total = VW_HDR_LEN + payload_len + VW_CRC_LEN;
    if (cap < total) {
        return 0;
    }
    out[0] = VW_MAGIC0;
    out[1] = VW_MAGIC1;
    out[2] = VW_VER;
    out[3] = type;
    put_u32be(out + 4, seq);
    put_u32be(out + 8, ts_ms);
    put_u32be(out + 12, payload_len);
    if (payload_len) {
        memcpy(out + VW_HDR_LEN, payload, payload_len);
    }
    uint16_t crc = vw_crc16(out, VW_HDR_LEN + payload_len);
    out[VW_HDR_LEN + payload_len] = (uint8_t)(crc >> 8);
    out[VW_HDR_LEN + payload_len + 1] = (uint8_t)(crc & 0xFF);
    return total;
}

void vw_parser_init(vw_parser_t *p, vw_frame_cb_t cb, void *arg)
{
    memset(p, 0, sizeof(*p));
    p->cb = cb;
    p->arg = arg;
}

size_t vw_parser_feed(vw_parser_t *p, const uint8_t *data, size_t len)
{
    if (len + p->len > sizeof(p->buf)) {
        p->len = 0;  // 溢出保护：整体丢弃重新对齐
        return len;
    }
    memcpy(p->buf + p->len, data, len);
    p->len += len;

    while (p->len >= VW_HDR_LEN) {
        if (p->buf[0] != VW_MAGIC0 || p->buf[1] != VW_MAGIC1) {
            memmove(p->buf, p->buf + 1, p->len - 1);
            p->len--;
            continue;
        }
        uint32_t plen = get_u32be(p->buf + 12);
        size_t total = VW_HDR_LEN + plen + VW_CRC_LEN;
        if (total > sizeof(p->buf)) {
            p->len = 0;
            break;
        }
        if (p->len < total) {
            break;  // 等待更多数据
        }
        uint16_t crc = vw_crc16(p->buf, VW_HDR_LEN + plen);
        uint16_t crc_rx = (uint16_t)((p->buf[VW_HDR_LEN + plen] << 8) | p->buf[VW_HDR_LEN + plen + 1]);
        if (crc == crc_rx && p->cb) {
            p->cb(p->buf[3], get_u32be(p->buf + 4), get_u32be(p->buf + 8),
                  p->buf + VW_HDR_LEN, plen, p->arg);
        }
        memmove(p->buf, p->buf + total, p->len - total);
        p->len -= total;
    }
    return len;
}
