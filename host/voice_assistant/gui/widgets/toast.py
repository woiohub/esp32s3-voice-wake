"""右下角非阻塞 Toast：成功 / 警告 / 错误三色，3s 自动消失。"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QTimer, Qt
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QWidget,
)

from ..icons import pixmap

_KIND = {
    "info": ("primary", "alert"),
    "ok": ("success", "check"),
    "warn": ("warn", "alert"),
    "error": ("error", "alert"),
}


class Toast(QFrame):
    def __init__(self, t: dict, text: str, kind: str = "info", parent=None):
        super().__init__(parent)
        self.t = t
        key, icon_name = _KIND.get(kind, _KIND["info"])
        color = t[key]

        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            f"QFrame{{background:{t['panel']};border:1px solid {color};"
            f"border-left:3px solid {color};border-radius:{t['radius_sm']}px;}}"
            f"QLabel{{color:{t['text']};font-size:{t['fs_body']}px;}}")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 12, 8)
        lay.setSpacing(8)

        icon = QLabel()
        icon.setPixmap(pixmap(icon_name, color, 16))
        icon.setFixedSize(16, 16)
        lay.addWidget(icon, 0, Qt.AlignTop)

        msg = QLabel(text)
        msg.setWordWrap(True)
        msg.setMaximumWidth(320)
        lay.addWidget(msg, 1)

        self.adjustSize()
        self._effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._effect)
        self._effect.setOpacity(0.0)

        self._anim = QPropertyAnimation(self._effect, b"opacity", self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def fade_in(self) -> None:
        self.show()
        self._anim.stop()
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        QTimer.singleShot(3000, self.fade_out)

    def fade_out(self) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()


class ToastManager:
    """挂在主窗口上，负责堆叠与定位。"""

    def __init__(self, host: QWidget):
        self.host = host
        self.items: list[Toast] = []

    def show(self, text: str, kind: str = "info") -> None:
        t = self.host.t
        toast = Toast(t, text, kind, self.host)
        toast.setFixedWidth(min(380, max(220, toast.sizeHint().width())))
        self.items.append(toast)
        toast.destroyed.connect(lambda *_: self._forget(toast))
        self._relayout()
        toast.fade_in()

    def _forget(self, toast: Toast) -> None:
        if toast in self.items:
            self.items.remove(toast)

    def _relayout(self) -> None:
        margin = 16
        gap = 8
        y = self.host.height() - margin
        for toast in reversed(self.items):
            toast.adjustSize()
            h = toast.sizeHint().height()
            y -= h
            toast.move(self.host.width() - toast.width() - margin, y)
            y -= gap
