"""上下位机帧协议（Python 侧），与固件 net_proto.c 严格对齐。

帧 = 头(16B) + 载荷(N) + CRC16(2B)，多字节字段大端。
"""
from __future__ import annotations

import struct
import time
from typing import Callable

MAGIC0, MAGIC1, VERSION = 0xAA, 0x55, 0x01
HDR_LEN = 16
CRC_LEN = 2
MAX_FRAME = 65536

TYPE_CONTROL = 0x01
TYPE_AUDIO_UP = 0x02
TYPE_AUDIO_DOWN = 0x03
TYPE_HEARTBEAT = 0x04
TYPE_ACK = 0x05

AUDIO_FLAG_START = 0x01
AUDIO_FLAG_END = 0x02
AUDIO_FLAG_HAS_SPEECH = 0x04


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def build_frame(type_: int, seq: int, ts_ms: int, payload: bytes) -> bytes:
    # 头里 seq / ts 都是 32 位无符号；越界会让 struct.pack 直接抛异常，
    # 因此这里做一次掩码，避免“发一帧就崩”的连锁问题。
    header = struct.pack(">BBBBIII", MAGIC0, MAGIC1, VERSION, type_,
                         seq & 0xFFFFFFFF, ts_ms & 0xFFFFFFFF, len(payload))
    return header + payload + struct.pack(">H", crc16(header + payload))


def pack_audio(session_id: int, flags: int, pcm: bytes) -> bytes:
    return struct.pack(">IB", session_id, flags) + pcm


def unpack_audio(payload: bytes):
    session_id, flags = struct.unpack(">IB", payload[:5])
    return session_id, flags, payload[5:]


class FrameParser:
    """流式解析器，处理粘包/半包。"""

    def __init__(self, on_frame: Callable[[int, int, int, bytes], None]):
        self.buf = bytearray()
        self.on_frame = on_frame

    def feed(self, data: bytes) -> None:
        self.buf.extend(data)
        while len(self.buf) >= HDR_LEN:
            if self.buf[0] != MAGIC0 or self.buf[1] != MAGIC1:
                del self.buf[0]
                continue
            (plen,) = struct.unpack(">I", self.buf[12:16])
            total = HDR_LEN + plen + CRC_LEN
            if total > MAX_FRAME:
                self.buf.clear()
                break
            if len(self.buf) < total:
                break
            header = bytes(self.buf[:HDR_LEN])
            payload = bytes(self.buf[HDR_LEN:HDR_LEN + plen])
            (crc_rx,) = struct.unpack(">H", self.buf[HDR_LEN + plen:total])
            if crc16(header + payload) == crc_rx:
                type_ = header[3]
                seq, ts_ms = struct.unpack(">II", header[4:12])
                self.on_frame(type_, seq, ts_ms, payload)
            del self.buf[:total]


_T0 = time.monotonic()


def now_ms() -> int:
    """帧内时间戳：自进程启动的毫秒数。

    协议头的时间戳是 32 位无符号（最大约 49.7 天），而 Unix 纪元毫秒约 1.7e12，
    直接塞进去会让 struct.pack("I") 抛异常（历史 bug：上位机一发包就崩）。
    这里改成与固件一致的语义：ms since boot/start。
    """
    return int((time.monotonic() - _T0) * 1000) & 0xFFFFFFFF
