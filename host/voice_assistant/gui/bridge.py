"""Qt 信号桥：把 pipeline / network / 模型加载的回调统一转成 Qt Signal。

线程模型：
- 这些回调可能在 asyncio 线程（网络收发）或 QThreadPool 线程（模型加载）里被调用；
- Qt 的自动连接会把跨线程 emit 排队到接收方所在线程（主线程），
  因此 UI 侧不需要加锁，也不允许在这里直接碰任何控件。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class Bridge(QObject):
    # 流水线
    stage_changed = Signal(str, str)        # (stage, state) state: pending/active/done/failed/skipped
    stage_timing = Signal(str, float)       # (stage, ms)
    transcript_ready = Signal(str, float)   # (text, confidence)
    reply_chunk = Signal(str)
    reply_done = Signal(str)
    tts_started = Signal()
    tts_finished = Signal()
    interrupted = Signal()
    level = Signal(float)                   # 0..1 上行录音电平
    device_state = Signal(dict)
    model_status = Signal(str, str, str)    # (name, state, detail)
    error = Signal(int, str)                # (code, message)
    log_line = Signal(str, str, str)        # (levelname, module, message)
    ack = Signal(str, int, object)          # (cmd, code, result)

    def __init__(self) -> None:
        super().__init__()
        self.pipeline = None
        self.server = None
        self.device: dict = {"connected": False}

    # ---------------- 装配 ----------------

    def attach(self, pipeline, server) -> None:
        """只挂回调，不改动业务逻辑。"""
        self.pipeline = pipeline
        self.server = server

        pipeline.on_stage = self._on_stage
        pipeline.on_transcript = lambda text, conf: self.transcript_ready.emit(text, conf)
        pipeline.on_reply = lambda text: self.reply_done.emit(text)
        pipeline.on_tts = self._on_tts
        pipeline.on_interrupt = self.interrupted.emit
        pipeline.on_level = self.level.emit
        pipeline.on_error = lambda code, msg: self.error.emit(int(code), msg)
        pipeline.on_device = self._on_device
        pipeline.on_ack = lambda cmd, code, result: self.ack.emit(cmd, int(code), result)

        server.on_connect = self._on_connect
        server.on_disconnect = self._on_disconnect

    # ---------------- 回调实现 ----------------

    def _on_stage(self, stage: str, state: str, ms: float = 0.0) -> None:
        self.stage_changed.emit(stage, state)
        if ms and ms > 0:
            self.stage_timing.emit(stage, float(ms))

    def _on_tts(self, what: str) -> None:
        if what == "start":
            self.tts_started.emit()
        else:
            self.tts_finished.emit()

    def _on_connect(self, conn) -> None:
        peer = getattr(conn, "peer", None)
        ip = peer[0] if isinstance(peer, (tuple, list)) and peer else ""
        self.device = {"connected": True, "ip": ip, "peer": str(peer)}
        self.device_state.emit(dict(self.device))

    def _on_disconnect(self, conn) -> None:
        self.device = {"connected": False, "ip": self.device.get("ip", "")}
        self.device_state.emit(dict(self.device))

    def _on_device(self, info: dict) -> None:
        self.device.update(info or {})
        self.device_state.emit(dict(self.device))

    # ---------------- 供页面调用 ----------------

    def report_model(self, name: str, state: str, detail: str = "") -> None:
        self.model_status.emit(name, state, detail)


class QtLogHandler(logging.Handler):
    """把 logging 记录转发到 GUI（由 Bridge.log_line 排队到主线程）。"""

    def __init__(self, bridge: Bridge):
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:      # noqa: D102
        try:
            msg = record.getMessage()
        except Exception:                                    # noqa: BLE001
            msg = str(record.msg)
        if record.exc_info:
            try:
                msg += "\n" + self.formatException(record.exc_info)
            except Exception:                                # noqa: BLE001
                pass
        self._bridge.log_line.emit(record.levelname, record.name, msg)


def install_log_handler(bridge: Bridge) -> QtLogHandler:
    handler = QtLogHandler(bridge)
    handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(handler)
    return handler
