"""轻量线程池任务：把阻塞调用挪出 UI 线程，结果经信号回到主线程。

用法（`slot` 必须是主线程 QObject 的绑定方法，Qt 会自动排队）：
    run_task(lambda: stt.preload(), self._on_stt_probe_done, tag="stt")
"""
from __future__ import annotations

from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


class _Signals(QObject):
    done = Signal(str, object, object)      # (tag, result, exception)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], signals: _Signals, tag: str):
        super().__init__()
        self.fn = fn
        self.signals = signals
        self.tag = tag
        self.setAutoDelete(True)

    def run(self) -> None:                  # noqa: D102
        try:
            result = self.fn()
            self.signals.done.emit(self.tag, result, None)
        except Exception as exc:            # noqa: BLE001 - 交给 UI 展示
            self.signals.done.emit(self.tag, None, exc)


def run_task(fn: Callable[[], Any], slot: Callable[[str, object, object], None],
             tag: str = "") -> None:
    # signals 挂在线程池上，避免任务结束（autoDelete）后对象被回收，
    # 而排队中的 done 信号还没投递到主线程。
    signals = _Signals(QThreadPool.globalInstance())
    signals.done.connect(slot)               # slot 在主线程 → 自动 Queued
    QThreadPool.globalInstance().start(_Task(fn, signals, tag))
