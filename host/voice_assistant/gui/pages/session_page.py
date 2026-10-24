"""会话页：阶段条 + 对话流 + 文本调试输入 + 空状态引导。"""
from __future__ import annotations

import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

from ..widgets.chat_bubble import ChatBubble
from ..widgets.setup_checklist import SetupChecklist
from ..widgets.stage_timeline import StageTimeline


class _Input(QPlainTextEdit):
    """单行自适应高度，Enter 发送 / Shift+Enter 换行。"""

    submitted = Signal()

    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self.setPlaceholderText("文本调试：直接输入一句话，Enter 发送 / Shift+Enter 换行")
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedHeight(36)
        self.textChanged.connect(self._autosize)

    def keyPressEvent(self, event):      # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if event.modifiers() & Qt.ShiftModifier:
                super().keyPressEvent(event)
                self._autosize()
            else:
                self.submitted.emit()
            return
        super().keyPressEvent(event)

    def _autosize(self) -> None:
        h = int(self.document().size().height()) + 14
        self.setFixedHeight(max(36, min(h, 84)))


class SessionPage(QWidget):
    ask_text = Signal(str)
    replay_text = Signal(str)
    regenerate_text = Signal(str)
    navigate = Signal(str)
    toast = Signal(str, str)

    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self._autoscroll = True
        self._pending: ChatBubble | None = None
        self._last_assistant = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        # 阶段条
        self.timeline = StageTimeline(t, compact=True)
        root.addWidget(self.timeline)

        # 对话区 / 空状态
        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_chat())
        root.addWidget(self.stack, 1)

        # 输入区
        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = _Input(t)
        self.input.submitted.connect(self._submit)
        row.addWidget(self.input, 1)
        self.send_btn = QPushButton("发送")
        self.send_btn.setProperty("role", "primary")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setEnabled(False)
        self.send_btn.clicked.connect(self._submit)
        row.addWidget(self.send_btn)
        self.input.textChanged.connect(
            lambda: self.send_btn.setEnabled(bool(self.input.toPlainText().strip())))
        root.addLayout(row)

    # ---------------- 构建 ----------------

    def _build_empty(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)

        title = QLabel("对着设备说「小美同学」开始对话")
        title.setProperty("role", "h1")
        title.setAlignment(Qt.AlignCenter)
        lay.addWidget(title)

        hint = QLabel("也可以直接在下方输入文字调试整条流水线")
        hint.setProperty("role", "dim")
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

        lay.addSpacing(14)
        self.checklist = SetupChecklist(self.t)
        self.checklist.navigate.connect(self.navigate.emit)
        lay.addWidget(self.checklist, 0, Qt.AlignHCenter)
        lay.addStretch(1)
        return page

    def _build_chat(self) -> QWidget:
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("transparent")
        self.chat_lay = QVBoxLayout(inner)
        self.chat_lay.setContentsMargins(2, 2, 8, 2)
        self.chat_lay.setSpacing(10)
        self.chat_lay.addStretch(1)
        self.scroll.setWidget(inner)
        self.scroll.verticalScrollBar().valueChanged.connect(self._on_scroll)
        return self.scroll

    # ---------------- 滚动 ----------------

    def _on_scroll(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        self._autoscroll = value >= bar.maximum() - 48

    def _to_bottom(self) -> None:
        if self._autoscroll:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())

    # ---------------- 气泡 ----------------

    def _add(self, bubble: ChatBubble, right: bool) -> None:
        row = QWidget()
        row.setObjectName("transparent")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        if right:
            lay.addStretch(1)
            lay.addWidget(bubble)
        else:
            lay.addWidget(bubble)
            lay.addStretch(1)
        # 插到末尾的 stretch 之前
        self.chat_lay.insertWidget(self.chat_lay.count() - 1, row)
        self.stack.setCurrentIndex(1)
        self._to_bottom()

    def add_user(self, text: str, note: str = "语音") -> ChatBubble:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        bubble = ChatBubble(self.t, "user", text, ts, note=note)
        bubble.copy_requested.connect(self._copy)
        self._add(bubble, right=True)
        return bubble

    def add_assistant(self, text: str = "") -> ChatBubble:
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        bubble = ChatBubble(self.t, "assistant", text, ts)
        bubble.copy_requested.connect(self._copy)
        bubble.replay_requested.connect(lambda b: self.replay_text.emit(b.text()))
        bubble.regenerate_requested.connect(lambda b: self.regenerate_text.emit(b.text()))
        self._add(bubble, right=False)
        return bubble

    def _copy(self, bubble: ChatBubble) -> None:
        QGuiApplication.clipboard().setText(bubble.text())
        self.toast.emit("已复制到剪贴板", "ok")

    # ---------------- 输入 ----------------

    def _submit(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.input.clear()
        self.add_user(text, note="文本")
        self.ask_text.emit(text)

    # ---------------- 信号处理 ----------------

    def on_stage(self, stage: str, state: str) -> None:
        self.timeline.set_stage(stage, state)
        # 占位气泡必须晚于用户气泡创建，否则会出现「助手在上、用户在下」
        if stage == "llm" and state == "active" and self._pending is None:
            self._pending = self.add_assistant()
            self._pending.set_generating()

    def on_stage_timing(self, stage: str, ms: float) -> None:
        self.timeline.set_timing(stage, ms)

    def on_transcript(self, text: str, conf: float) -> None:
        if text:
            self.add_user(text, note="语音")

    def on_reply(self, text: str) -> None:
        if not text:
            return
        self._last_assistant = text
        if self._pending is None:
            self._pending = self.add_assistant(text)
        else:
            self._pending.set_text(text)

    def on_tts_started(self) -> None:
        if self._pending is not None:
            self._pending.set_speaking()

    def on_tts_finished(self) -> None:
        if self._pending is not None:
            self._pending.set_done()
            self._pending = None

    def on_interrupted(self) -> None:
        if self._pending is not None:
            self._pending.set_interrupted()
            self._pending = None

    def on_failed(self, msg: str) -> None:
        if self._pending is not None:
            self._pending.set_failed("失败")
            self._pending = None

    def on_level(self, value: float) -> None:
        # 会话页不画电平表（在右侧状态栏），这里留作扩展点
        pass

    # ---------------- 状态 ----------------

    def set_device_ok(self, ok: bool) -> None:
        self.checklist.set_item("device", ok)

    def set_models_ready(self, stt_ok: bool, tts_ok: bool) -> None:
        self.checklist.set_item("models", stt_ok and tts_ok,
                                "模型已就绪" if stt_ok and tts_ok else "模型未就绪（STT/TTS）")

    def set_apikey_ok(self, ok: bool) -> None:
        self.checklist.set_item("apikey", ok,
                                "已配置大模型 API Key" if ok else "未配置大模型 API Key")

    def clear_chat(self) -> None:
        while self.chat_lay.count() > 1:
            item = self.chat_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._pending = None
        self.stack.setCurrentIndex(0)
        self.timeline.reset()

    def last_assistant(self) -> str:
        return self._last_assistant
