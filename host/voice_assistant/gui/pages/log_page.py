"""日志页：级别/模块/关键字过滤、暂停、清空、导出、错误码说明。"""
from __future__ import annotations

import os
import re
from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPlainTextEdit, QPushButton, QTextEdit, QToolTip, QVBoxLayout, QWidget,
)

MAX_LINES = 5000
_CAT = {"DEBUG": "DEBUG", "INFO": "INFO", "WARNING": "WARN", "WARN": "WARN",
        "ERROR": "ERROR", "CRITICAL": "ERROR"}
_CODE_RE = re.compile(r"(?:code=|\[0x)([0-9A-Fa-f]{2,6})")


def _load_code_doc() -> dict[str, str]:
    """解析错误码表：优先 host/docs/01_接口设计.md，回退到仓库根 docs/01_接口设计.md。"""
    here = os.path.dirname(os.path.abspath(__file__))
    host_dir = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))   # host/
    candidates = [
        os.path.join(host_dir, "docs", "01_接口设计.md"),
        os.path.join(host_dir, "..", "docs", "01_接口设计.md"),
    ]
    table: dict[str, str] = {}
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    m = re.match(r"\|\s*(\d{1,5})\s*\|\s*([^|]+)\|", line)
                    if m:
                        table[m.group(1)] = m.group(2).strip()
        except OSError:
            continue
        if table:
            break
    return table


class LogPage(QWidget):
    unread = Signal(int)

    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self._buf: deque[tuple[str, str, str, str]] = deque(maxlen=MAX_LINES)
        self._paused = False
        self._unread = 0
        self._codes = _load_code_doc()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        root.addLayout(self._build_toolbar())

        self.view = QPlainTextEdit()
        self.view.setObjectName("log")
        self.view.setReadOnly(True)
        self.view.setMaximumBlockCount(MAX_LINES)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.mouseDoubleClickEvent = self._on_double_click        # type: ignore[assignment]
        root.addWidget(self.view, 1)

    # ---------------- 工具条 ----------------

    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.setSpacing(6)

        self.level_btns: dict[str, QPushButton] = {}
        for key in ("DEBUG", "INFO", "WARN", "ERROR"):
            btn = QPushButton(key)
            btn.setCheckable(True)
            btn.setChecked(key in ("INFO", "WARN", "ERROR"))
            btn.setProperty("role", "ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(self._render)
            self.level_btns[key] = btn
            bar.addWidget(btn)

        sep = QLabel("|")
        sep.setProperty("role", "dim")
        bar.addWidget(sep)

        self.module_box = QComboBox()
        self.module_box.addItem("全部模块")
        self.module_box.setMinimumWidth(150)
        self.module_box.currentIndexChanged.connect(self._render)
        bar.addWidget(self.module_box)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索关键字（Ctrl+F）")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._render)
        bar.addWidget(self.search, 1)

        self.autoscroll = QCheckBox("自动滚动")
        self.autoscroll.setChecked(True)
        bar.addWidget(self.autoscroll)

        self.pause_btn = QPushButton("暂停")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._on_pause)
        bar.addWidget(self.pause_btn)

        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.clear_btn)

        self.export_btn = QPushButton("导出")
        self.export_btn.clicked.connect(self.export)
        bar.addWidget(self.export_btn)
        return bar

    # ---------------- 数据 ----------------

    def append_log(self, level: str, module: str, message: str) -> None:
        cat = _CAT.get(level.upper(), "INFO")
        ts = ""
        # 用消息里的时间戳更好，但 logging 记录里没有；这里用到达顺序即可
        self._buf.append((ts, cat, module, message))

        if cat == "ERROR" and not self.isVisible():
            self._unread += 1
            self.unread.emit(self._unread)

        if not self._paused:
            self._render()

    def _passes(self, rec) -> bool:
        _, cat, module, msg = rec
        btn = self.level_btns.get(cat)
        if btn is not None and not btn.isChecked():
            return False
        want = self.module_box.currentData()
        if want and want != module:
            return False
        key = self.search.text().strip()
        if key and key.lower() not in msg.lower():
            return False
        return True

    def _render(self) -> None:
        if self._paused:
            return
        lines = []
        for rec in self._buf:
            if self._passes(rec):
                _, cat, module, msg = rec
                lines.append(f"{cat:<5} [{module}] {msg}")

        self.view.setPlainText("\n".join(lines))
        self._paint()
        if self.autoscroll.isChecked():
            bar = self.view.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _paint(self) -> None:
        sels = []
        err_fmt = QTextCharFormat()
        err_fmt.setBackground(QColor(self.t["error"]))
        err_bg = QColor(self.t["error"])
        err_bg.setAlpha(28)
        err_fmt.setBackground(err_bg)

        hit_fmt = QTextCharFormat()
        hit_bg = QColor(self.t["primary"])
        hit_bg.setAlpha(60)
        hit_fmt.setBackground(hit_bg)

        key = self.search.text().strip().lower()
        block = self.view.document().firstBlock()
        while block.isValid():
            text = block.text()
            if text.startswith("ERROR") or text.startswith("CRITICAL"):
                sel = QTextCursor(block)
                sel.clearSelection()
                sel.movePosition(QTextCursor.StartOfBlock)
                sel.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                sels.append((sel, err_fmt))
            if key and key in text.lower():
                start = 0
                low = text.lower()
                while True:
                    idx = low.find(key, start)
                    if idx < 0:
                        break
                    cur = QTextCursor(block)
                    cur.setPosition(block.position() + idx)
                    cur.setPosition(block.position() + idx + len(key), QTextCursor.KeepAnchor)
                    sels.append((cur, hit_fmt))
                    start = idx + len(key)
            block = block.next()

        out = []
        for cur, fmt in sels:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cur
            sel.format = fmt
            out.append(sel)
        self.view.setExtraSelections(out)

    # ---------------- 交互 ----------------

    def _on_pause(self, paused: bool) -> None:
        self._paused = paused
        self.pause_btn.setText("继续" if paused else "暂停")
        if not paused:
            self._render()

    def clear(self) -> None:
        self._buf.clear()
        self.view.clear()
        self._unread = 0
        self.unread.emit(0)

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "voice_assistant.log",
                                              "日志文件 (*.log *.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                for _, cat, module, msg in self._buf:
                    f.write(f"{cat:<5} [{module}] {msg}\n")
        except OSError as exc:
            QToolTip.showText(self.mapToGlobal(self.rect().center()), f"导出失败：{exc}")
            return
        QToolTip.showText(self.mapToGlobal(self.rect().center()), f"已导出到 {path}")

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def mark_read(self) -> None:
        self._unread = 0
        self.unread.emit(0)

    def update_modules(self) -> None:
        modules = sorted({rec[2] for rec in self._buf})
        current = self.module_box.currentData()
        if self.module_box.count() - 1 == len(modules):
            return
        self.module_box.blockSignals(True)
        self.module_box.clear()
        self.module_box.addItem("全部模块", None)
        for m in modules:
            self.module_box.addItem(m, m)
        if current:
            idx = self.module_box.findData(current)
            if idx >= 0:
                self.module_box.setCurrentIndex(idx)
        self.module_box.blockSignals(False)

    def _on_double_click(self, event) -> None:      # noqa: N802
        cursor = self.view.cursorForPosition(event.pos())
        text = cursor.block().text()
        m = _CODE_RE.search(text)
        if not m:
            return
        raw = m.group(1)
        try:
            code = str(int(raw, 16)) if "0x" in text[max(0, m.start()):m.end()] else str(int(raw))
        except ValueError:
            code = raw
        desc = self._codes.get(code)
        msg = f"错误码 {code}：{desc}" if desc else f"错误码 {code}（接口设计文档未收录）"
        QToolTip.showText(event.globalPos(), msg, self.view)
        QPlainTextEdit.mouseDoubleClickEvent(self.view, event)
