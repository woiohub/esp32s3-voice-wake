"""流水线阶段条：唤醒 → 录音 → STT → LLM → TTS → 播放。

状态：
  pending  未达（暗）
  active   进行中（主色 + 呼吸脉冲）
  done     完成（绿，可附耗时）
  failed   失败（红）
  skipped  跳过（灰）
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel

STAGES = [
    ("wake", "唤醒"), ("capture", "录音"), ("stt", "STT"),
    ("llm", "LLM"), ("tts", "TTS"), ("play", "播放"),
]


class _Chip(QWidget):
    def __init__(self, t: dict, key: str, title: str, parent=None):
        super().__init__(parent)
        self.t = t
        self.key = key
        self.state = "pending"

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.dot = QLabel()
        self.dot.setFixedSize(18, 18)
        self.dot.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.dot, 0, Qt.AlignHCenter)

        self.name = QLabel(title)
        self.name.setAlignment(Qt.AlignCenter)
        self.name.setStyleSheet(f"color:{t['text_dim']};font-size:{t['fs_small']}px;")
        lay.addWidget(self.name)

        self.ms = QLabel("")
        self.ms.setAlignment(Qt.AlignCenter)
        self.ms.setStyleSheet(
            f"color:{t['text_dim']};font-family:{t['mono']};font-size:10px;")
        lay.addWidget(self.ms)

        self.apply()

    def apply(self, pulse_on: bool = True) -> None:
        t = self.t
        color = {
            "pending": t["border"],
            "active": t["primary"],
            "done": t["success"],
            "failed": t["error"],
            "skipped": t["text_dim"],
        }.get(self.state, t["border"])
        if self.state == "active" and not pulse_on:
            color = t["hover"]

        self.dot.setStyleSheet(
            f"background:{color};border-radius:9px;")
        text_color = t["text"] if self.state in ("active", "done", "failed") else t["text_dim"]
        self.name.setStyleSheet(
            f"color:{text_color};font-size:{t['fs_small']}px;")

    def set_state(self, state: str, ms: float | None = None) -> None:
        self.state = state
        if ms is not None and ms > 0:
            self.ms.setText(f"{ms:.0f}ms" if ms < 1000 else f"{ms / 1000:.2f}s")
        elif state in ("pending", "skipped"):
            self.ms.setText("")
        self.apply()


class StageTimeline(QWidget):
    """横向 6 步阶段条，可在会话页与右侧状态栏复用。"""

    def __init__(self, t: dict, compact: bool = False, parent=None):
        super().__init__(parent)
        self.t = t
        self.compact = compact
        self._chips: dict[str, _Chip] = {}
        self._pulse = True

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2 if compact else 6)
        for key, title in STAGES:
            chip = _Chip(t, key, title)
            self._chips[key] = chip
            lay.addWidget(chip, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

    # ---------------- 对外 ----------------

    def reset(self) -> None:
        self._timer.stop()
        for chip in self._chips.values():
            chip.set_state("pending")

    def set_stage(self, stage: str, state: str, ms: float | None = None) -> None:
        chip = self._chips.get(stage)
        if chip is None:
            return
        chip.set_state(state, ms)
        if any(c.state == "active" for c in self._chips.values()):
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
            for c in self._chips.values():
                c.apply(True)

    def set_timing(self, stage: str, ms: float) -> None:
        chip = self._chips.get(stage)
        if chip is not None:
            chip.set_state("done" if chip.state == "pending" else chip.state, ms)

    def _tick(self) -> None:
        self._pulse = not self._pulse
        for chip in self._chips.values():
            if chip.state == "active":
                chip.apply(self._pulse)
