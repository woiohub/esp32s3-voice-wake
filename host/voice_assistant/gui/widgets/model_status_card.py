"""模型状态卡：状态灯 + 标题 + 状态/错误摘要 + 重试按钮 + 可放置字段区域。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

_STATE = {
    "ready":   ("success", "就绪"),
    "loading": ("warn", "加载中…"),
    "error":   ("error", "失败"),
    "unknown": ("text_dim", "未检测"),
}


class ModelStatusCard(QFrame):
    retry_requested = Signal(str)

    def __init__(self, t: dict, key: str, title: str, parent=None):
        super().__init__(parent)
        self.t = t
        self.key = key
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        head.addWidget(self._dot)

        name = QLabel(title)
        name.setStyleSheet(f"font-size:{t['fs_title']}px;font-weight:600;")
        head.addWidget(name)
        head.addStretch(1)

        self._retry = QPushButton("重试")
        self._retry.setProperty("role", "ghost")
        self._retry.setCursor(Qt.PointingHandCursor)
        self._retry.setVisible(False)
        self._retry.clicked.connect(lambda: self.retry_requested.emit(self.key))
        head.addWidget(self._retry)
        root.addLayout(head)

        self._state = QLabel("未检测")
        self._state.setStyleSheet(
            f"color:{t['text_dim']};font-size:{t['fs_small']}px;")
        root.addWidget(self._state)

        self._detail = QLabel("")
        self._detail.setWordWrap(True)
        self._detail.setStyleSheet(
            f"color:{t['text_dim']};font-size:11px;font-family:{t['mono']};")
        self._detail.setVisible(False)
        root.addWidget(self._detail)

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 4, 0, 0)
        self.body_layout.setSpacing(8)
        root.addWidget(self.body)
        root.addStretch(1)

        self.set_state("unknown")

    def set_state(self, state: str, detail: str = "") -> None:
        key, label = _STATE.get(state, _STATE["unknown"])
        color = self.t[key]
        self._dot.setStyleSheet(f"background:{color};border-radius:4px;")
        self._state.setStyleSheet(f"color:{color};font-size:{self.t['fs_small']}px;")
        self._state.setText(label + ("：" + detail.splitlines()[0] if detail and state != "ready" else ""))
        self._detail.setVisible(bool(detail) and state == "error")
        self._detail.setText(detail)
        self._retry.setVisible(state == "error")
