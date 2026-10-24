"""首启自检清单：设备连接 / 模型就绪 / API Key，未通过项可一键跳转配置。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from ..icons import pixmap

_ITEMS = [
    ("device", "设备已连接", "设备"),
    ("models", "模型已就绪", "模型"),
    ("apikey", "已配置大模型 API Key", "模型"),
]


class SetupChecklist(QFrame):
    navigate = Signal(str)          # 页面名："device" / "model"

    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMaximumWidth(520)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(6)

        self._rows: dict[str, tuple[QLabel, QLabel, QPushButton]] = {}
        for key, text, page in _ITEMS:
            row = QHBoxLayout()
            row.setSpacing(8)
            lamp = QLabel()
            lamp.setFixedSize(16, 16)
            row.addWidget(lamp, 0, Qt.AlignVCenter)
            label = QLabel(text)
            row.addWidget(label, 1)
            btn = QPushButton("去配置")
            btn.setProperty("role", "ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setVisible(False)
            btn.clicked.connect(lambda _=False, p=page: self.navigate.emit(p))
            row.addWidget(btn)
            root.addLayout(row)
            self._rows[key] = (lamp, label, btn)
            self.set_item(key, False)

    def set_item(self, key: str, ok: bool, text: str | None = None) -> None:
        if key not in self._rows:
            return
        lamp, label, btn = self._rows[key]
        t = self.t
        color = t["success"] if ok else t["warn"]
        lamp.setPixmap(pixmap("check" if ok else "alert", color, 16))
        if text:
            label.setText(text)
        label.setStyleSheet(
            f"color:{t['text'] if ok else t['text_dim']};font-size:{t['fs_body']}px;")
        btn.setVisible(not ok)
