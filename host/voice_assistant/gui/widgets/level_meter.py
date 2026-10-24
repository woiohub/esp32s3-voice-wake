"""上行录音电平条：分段绘制，30fps 平滑跟随。"""
from __future__ import annotations

from PySide6.QtCore import QTimer, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QWidget

_SEGMENTS = 22


class LevelMeter(QWidget):
    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self._level = 0.0
        self._target = 0.0
        self.setFixedHeight(12)
        self.setMinimumWidth(80)

        self._timer = QTimer(self)
        self._timer.setInterval(33)          # ≈30fps
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def push(self, value: float) -> None:
        """由 Bridge.level 驱动（0..1）。"""
        self._target = max(0.0, min(1.0, float(value)))

    def _tick(self) -> None:
        if abs(self._target - self._level) < 0.005:
            return
        self._level += (self._target - self._level) * 0.35
        self.update()

    def paintEvent(self, event) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        h = self.height()
        gap = 2.0
        seg_w = max(2.0, (w - gap * (_SEGMENTS - 1)) / _SEGMENTS)
        lit = int(round(self._level * _SEGMENTS))

        for i in range(_SEGMENTS):
            ratio = i / max(1, _SEGMENTS - 1)
            if ratio < 0.66:
                color = QColor(self.t["success"])
            elif ratio < 0.86:
                color = QColor(self.t["warn"])
            else:
                color = QColor(self.t["error"])
            if i >= lit:
                color.setAlpha(45)
            x = i * (seg_w + gap)
            p.fillRect(QRectF(x, 1, seg_w, h - 2), color)
        p.end()
