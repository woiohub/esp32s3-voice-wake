"""TCP 服务端：接收下位机连接，收发帧协议。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Optional

from . import protocol

log = logging.getLogger(__name__)


class DeviceConnection:
    """一条下位机连接。"""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.peer = writer.get_extra_info("peername")
        self.parser = protocol.FrameParser(self._on_frame)
        self.on_frame: Optional[Callable] = None  # (type, seq, ts_ms, payload, conn)
        self.seq = 0

    def _on_frame(self, type_, seq, ts_ms, payload):
        if self.on_frame:
            self.on_frame(type_, seq, ts_ms, payload, self)

    async def send(self, type_: int, payload: bytes) -> None:
        self.seq += 1
        frame = protocol.build_frame(type_, self.seq, protocol.now_ms(), payload)
        self.writer.write(frame)
        await self.writer.drain()

    async def send_json(self, cmd: str, params: dict, req_id: str) -> None:
        payload = json.dumps({"id": req_id, "cmd": cmd, "params": params},
                             ensure_ascii=False).encode("utf-8")
        await self.send(protocol.TYPE_CONTROL, payload)

    async def send_audio(self, session_id: int, flags: int, pcm: bytes) -> None:
        await self.send(protocol.TYPE_AUDIO_DOWN, protocol.pack_audio(session_id, flags, pcm))

    async def recv_loop(self) -> None:
        try:
            while True:
                data = await self.reader.read(4096)
                if not data:
                    break
                self.parser.feed(data)
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            pass


class TcpServer:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.on_connect: Optional[Callable] = None   # (conn)
        self.on_disconnect: Optional[Callable] = None  # (conn)
        self.on_frame: Optional[Callable] = None     # (type, seq, ts_ms, payload, conn)
        self.connection: Optional[DeviceConnection] = None
        self._server = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        log.info("tcp server listening on %s:%d", self.host, self.port)

    async def _handle(self, reader, writer):
        conn = DeviceConnection(reader, writer)
        conn.on_frame = self.on_frame
        self.connection = conn
        log.info("device connected: %s", conn.peer)
        if self.on_connect:
            self.on_connect(conn)
        await conn.recv_loop()
        log.info("device disconnected: %s", conn.peer)
        self.connection = None
        if self.on_disconnect:
            self.on_disconnect(conn)
