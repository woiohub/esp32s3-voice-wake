"""状态胶囊：小圆点 + 文本，用于顶栏连接状态、卡片状态等。"""
from __future__ import annotations

from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

_STATE_COLOR = {"ok": "success", "warn": "warn", "error": "error", "idle": "text_dim"}


class StatusPill(QFrame):
    def __init__(self, t: dict, text: str = "", state: str = "idle", parent=None):
        super().__init__(parent)
        self.t = t
        self.setObjectName("pill")
        self.setStyleSheet(
            f"QFrame#pill{{background:{t['panel']};border:1px solid {t['border']};"
            f"border-radius:11px;}}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(9, 3, 11, 3)
        lay.setSpacing(6)

        self._dot = QLabel()
        self._dot.setFixedSize(8, 8)
        lay.addWidget(self._dot)

        self._text = QLabel(text)
        self._text.setStyleSheet(f"color:{t['text']};")
        lay.addWidget(self._text)

        self.set_state(state, text)

    def set_state(self, state: str, text: str | None = None) -> None:
        color = self.t[_STATE_COLOR.get(state, "text_dim")]
        self._dot.setStyleSheet(f"background:{color};border-radius:4px;")
        if text is not None:
            self._text.setText(text)

    def set_text(self, text: str) -> None:
        self._text.setText(text)
