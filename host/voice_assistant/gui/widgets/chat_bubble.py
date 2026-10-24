"""对话气泡：角色 / 时间 / 正文（可选中）/ 状态角标 / 悬浮操作。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QEvent, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ..icons import icon


class ChatBubble(QFrame):
    copy_requested = Signal(object)
    replay_requested = Signal(object)
    regenerate_requested = Signal(object)

    def __init__(self, t: dict, role: str, text: str = "", ts: str = "",
                 note: str = "", parent=None):
        super().__init__(parent)
        self.t = t
        self.role = "user" if role == "user" else "assistant"
        self._dots = 0
        self._blink = True
        self._state = "done"

        self.setObjectName("card")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMaximumWidth(620)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Minimum)
        self._apply_frame()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 9, 12, 8)
        root.setSpacing(4)

        # 头部：角色 + 时间 + 备注
        head = QHBoxLayout()
        head.setSpacing(8)
        who = QLabel("你" if self.role == "user" else "助手")
        who.setStyleSheet(
            f"color:{t['text_dim']};font-size:{t['fs_small']}px;font-weight:600;")
        head.addWidget(who)
        if note:
            note_lbl = QLabel(note)
            note_lbl.setStyleSheet(
                f"color:{t['text_dim']};font-size:11px;border:1px solid {t['border']};"
                f"border-radius:6px;padding:0 5px;")
            head.addWidget(note_lbl)
        head.addStretch(1)
        self._time = QLabel(ts)
        self._time.setStyleSheet(
            f"color:{t['text_dim']};font-family:{t['mono']};font-size:11px;")
        head.addWidget(self._time)
        root.addLayout(head)

        # 正文
        self._body = QLabel(text)
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._body.setStyleSheet(f"color:{t['text']};font-size:{t['fs_body']}px;")
        self._body.setMinimumWidth(120)
        root.addWidget(self._body)

        # 状态 + 悬浮操作
        foot = QHBoxLayout()
        foot.setSpacing(6)
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"color:{t['text_dim']};font-size:11px;font-family:{t['mono']};")
        foot.addWidget(self._status)
        foot.addStretch(1)

        self._actions = QWidget(self)
        act = QHBoxLayout(self._actions)
        act.setContentsMargins(0, 0, 0, 0)
        act.setSpacing(2)
        for name, tip, sig in (
            ("copy", "复制", self.copy_requested),
            ("replay", "重新播报", self.replay_requested),
            ("refresh", "重新生成", self.regenerate_requested),
        ):
            btn = QToolButton(self._actions)
            btn.setIcon(icon(name, t["text_dim"], 15))
            btn.setToolTip(tip)
            btn.setAutoRaise(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, s=sig: s.emit(self))
            act.addWidget(btn)
        self._actions.setVisible(False)
        foot.addWidget(self._actions)
        root.addLayout(foot)

        self._timer = QTimer(self)
        self._timer.setInterval(380)
        self._timer.timeout.connect(self._tick)

    # ---------------- 样式 ----------------

    def _apply_frame(self, interrupted: bool = False) -> None:
        t = self.t
        if self.role == "user":
            border = f"1px solid {t['border']}"
            bg = t["hover"]
        else:
            border = f"1px solid {t['border']}"
            bg = t["panel"]
        if interrupted:
            border = f"1px solid {t['border']}"
        left = f"border-left:3px solid {t['error']};" if interrupted else ""
        self.setStyleSheet(
            f"QFrame#card{{background:{bg};border:{border};"
            f"border-radius:{t['radius']}px;{left}}}")

    # ---------------- 文本 ----------------

    def set_text(self, text: str) -> None:
        self._body.setText(text)

    def append_text(self, chunk: str) -> None:
        self._body.setText(self._body.text() + chunk)

    def text(self) -> str:
        return self._body.text()

    # ---------------- 状态 ----------------

    def set_generating(self) -> None:
        self._state = "generating"
        self._dots = 0
        self._status.setStyleSheet(
            f"color:{self.t['warn']};font-size:11px;font-family:{self.t['mono']};")
        self._timer.start()
        self._tick()

    def set_speaking(self) -> None:
        self._state = "speaking"
        self._blink = True
        self._status.setStyleSheet(
            f"color:{self.t['primary']};font-size:11px;font-family:{self.t['mono']};")
        self._timer.start()
        self._tick()

    def set_done(self) -> None:
        self._state = "done"
        self._timer.stop()
        self._status.setText("")
        self._apply_frame()

    def set_interrupted(self) -> None:
        self._state = "interrupted"
        self._timer.stop()
        self._status.setStyleSheet(
            f"color:{self.t['error']};font-size:11px;font-family:{self.t['mono']};")
        self._status.setText("已打断")
        self._apply_frame(interrupted=True)

    def set_failed(self, msg: str = "失败") -> None:
        self._state = "failed"
        self._timer.stop()
        self._status.setStyleSheet(
            f"color:{self.t['error']};font-size:11px;font-family:{self.t['mono']};")
        self._status.setText(msg)

    def _tick(self) -> None:
        if self._state == "generating":
            self._dots = (self._dots + 1) % 4
            self._status.setText("生成中" + "." * self._dots)
        elif self._state == "speaking":
            self._blink = not self._blink
            self._status.setText("播报中 ▍" if self._blink else "播报中")

    # ---------------- 悬浮操作 ----------------

    def enterEvent(self, event: QEvent) -> None:      # noqa: N802
        self._actions.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:      # noqa: N802
        # 鼠标移到子按钮上时父控件也会收到 leave，延迟一帧再判断
        QTimer.singleShot(60, self._maybe_hide_actions)
        super().leaveEvent(event)

    def _maybe_hide_actions(self) -> None:
        if not self.underMouse():
            self._actions.setVisible(False)
