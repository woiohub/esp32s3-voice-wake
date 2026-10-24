"""主窗口装配：顶栏 / 左侧导航 / 页面栈 / 右侧运行状态 / 底部状态栏 / 快捷键。

线程约定：本文件所有槽函数都在 GUI 主线程执行；耗时操作交给 tasks.run_task，
异步业务交给 pipeline 的 coroutine（通过 server.loop 投递）。
"""
from __future__ import annotations

import asyncio
import json
import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from ..config import save_config
from . import theme as theme_mod
from .bridge import Bridge, install_log_handler
from .icons import icon
from .pages.device_page import DevicePage
from .pages.log_page import LogPage
from .pages.model_page import ModelPage
from .pages.session_page import SessionPage
from .widgets.level_meter import LevelMeter
from .widgets.stage_timeline import StageTimeline
from .widgets.status_pill import StatusPill
from .widgets.toast import ToastManager

VERSION = "v0.7.2"
UI_STATE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "ui_state.json"))

_NAV = [
    ("session", "会话", "chat"),
    ("device", "设备", "device"),
    ("model", "模型", "model"),
    ("log", "日志", "log"),
]


def load_ui_state() -> dict:
    try:
        with open(UI_STATE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_ui_state(state: dict) -> None:
    try:
        with open(UI_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


class MainWindow(QMainWindow):
    def __init__(self, cfg, server, pipeline, stt, llm, tts):
        super().__init__()
        self.cfg = cfg
        self.server = server
        self.pipeline = pipeline
        self.stt, self.llm, self.tts = stt, llm, tts

        self.t = theme_mod.current()
        self.state = load_ui_state()
        self._model_state = {"stt": "unknown", "llm": "unknown", "tts": "unknown"}
        self._timings: dict[str, float] = {}
        self._log_seen = 0
        self._nav_buttons: dict[str, QToolButton] = {}

        self.setWindowTitle("语音助手 · 上位机")
        self.setMinimumSize(1100, 700)
        self.resize(int(self.state.get("w", 1280)), int(self.state.get("h", 820)))

        self.bridge = Bridge()
        self.bridge.attach(pipeline, server)
        self.toasts = ToastManager(self)

        self._build()
        self._connect()
        install_log_handler(self.bridge)

        self._restore_state()
        QTimer.singleShot(200, self._startup_probe)

    # ================= 构建 =================

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_topbar())

        body = QWidget()
        body.setObjectName("transparent")
        h = QHBoxLayout(body)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        h.addWidget(self._build_nav())

        self.pages = QStackedWidget()
        self.session_page = SessionPage(self.t)
        self.device_page = DevicePage(self.t, self.cfg)
        self.model_page = ModelPage(self.t, self.cfg, self.stt, self.llm, self.tts)
        self.log_page = LogPage(self.t)
        # 设备页/模型页内容较高：套一层滚动区，窗口变矮时滚动而不是把输入框压扁
        for page, scrollable in ((self.session_page, False),
                                 (self.device_page, True),
                                 (self.model_page, True),
                                 (self.log_page, False)):
            self.pages.addWidget(self._wrap_page(page, scrollable))
        h.addWidget(self.pages, 1)
        h.addWidget(self._build_right())

        outer.addWidget(body, 1)
        outer.addWidget(self._build_statusbar())

    def _wrap_page(self, page: QWidget, scrollable: bool) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("transparent")
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.addWidget(page)
        if not scrollable:
            return wrap

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(wrap)
        return area

    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("panel")
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(10)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{self.t['primary']};border-radius:4px;")
        lay.addWidget(dot)

        title = QLabel("语音助手")
        title.setProperty("role", "title")
        lay.addWidget(title)

        self.device_pill = StatusPill(self.t, "等待设备连接", "idle")
        lay.addWidget(self.device_pill)
        lay.addStretch(1)

        port = self.cfg.network.port
        self.listen_label = QLabel(f"监听 0.0.0.0:{port}")
        self.listen_label.setProperty("role", "mono")
        lay.addWidget(self.listen_label)

        copy_btn = QToolButton()
        copy_btn.setIcon(icon("copy", self.t["text_dim"], 16))
        copy_btn.setToolTip("复制监听地址")
        copy_btn.clicked.connect(self._copy_listen)
        lay.addWidget(copy_btn)

        gear = QToolButton()
        gear.setIcon(icon("gear", self.t["text_dim"], 18))
        gear.setToolTip("设置（Ctrl+,）")
        gear.clicked.connect(lambda: self.goto("model"))
        lay.addWidget(gear)
        return bar

    def _build_nav(self) -> QFrame:
        nav = QFrame()
        nav.setObjectName("panel")
        nav.setFixedWidth(72)
        lay = QVBoxLayout(nav)
        lay.setContentsMargins(4, 10, 4, 10)
        lay.setSpacing(6)

        from PySide6.QtWidgets import QButtonGroup
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)

        for idx, (key, label, icon_name) in enumerate(_NAV):
            btn = QToolButton()
            btn.setObjectName("nav")
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            btn.setIcon(icon(icon_name, self.t["text_dim"], 22))
            btn.setIconSize(QSize(22, 22))
            btn.setText(label)
            btn.setFixedSize(64, 56)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=idx: self.goto_index(i))
            lay.addWidget(btn, 0, Qt.AlignHCenter)
            self.nav_group.addButton(btn, idx)
            self._nav_buttons[key] = btn

        lay.addStretch(1)
        return nav

    def _build_right(self) -> QFrame:
        self.right = QFrame()
        self.right.setObjectName("panel")
        self.right.setFixedWidth(280)
        outer = QVBoxLayout(self.right)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)

        # 标题 + 折叠按钮单独放进 head 容器：折叠后只留一个 24x24 的按钮并居中，
        # 否则按钮会被标题挤成几像素宽的白色小块。
        head_w = QWidget()
        head_w.setObjectName("transparent")
        head_w.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        head = QHBoxLayout(head_w)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)

        self.right_title = QLabel("运行状态")
        self.right_title.setProperty("role", "title")
        head.addWidget(self.right_title)
        head.addStretch(1)

        self.collapse_btn = QToolButton()
        self.collapse_btn.setObjectName("rail")
        self.collapse_btn.setFixedSize(24, 24)
        self.collapse_btn.setIconSize(QSize(16, 16))
        self.collapse_btn.setIcon(icon("chevron_right", self.t["text_dim"], 16))
        self.collapse_btn.setToolTip("折叠/展开运行状态 (Ctrl+R)")
        self.collapse_btn.setCursor(Qt.PointingHandCursor)
        self.collapse_btn.clicked.connect(self.toggle_right)
        head.addWidget(self.collapse_btn)
        outer.addWidget(head_w)
        self.right_head = head_w

        self.right_body = QWidget()
        self.right_body.setObjectName("transparent")
        body = QVBoxLayout(self.right_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)

        conn_row = QHBoxLayout()
        lb = QLabel("连接")
        lb.setProperty("role", "dim")
        conn_row.addWidget(lb)
        conn_row.addStretch(1)
        self.right_pill = StatusPill(self.t, "未连接", "idle")
        conn_row.addWidget(self.right_pill)
        body.addLayout(conn_row)

        stage_lb = QLabel("阶段")
        stage_lb.setProperty("role", "dim")
        body.addWidget(stage_lb)

        self.right_stage = StageTimeline(self.t, compact=True)
        body.addWidget(self.right_stage)

        self.timing_label = QLabel("STT —   LLM —   TTS —")
        self.timing_label.setProperty("role", "mono")
        self.timing_label.setWordWrap(True)
        body.addWidget(self.timing_label)

        self.total_label = QLabel("本轮 —")
        self.total_label.setProperty("role", "mono")
        body.addWidget(self.total_label)

        lvl_lb = QLabel("电平")
        lvl_lb.setProperty("role", "dim")
        body.addWidget(lvl_lb)
        self.level = LevelMeter(self.t)
        body.addWidget(self.level)

        body.addStretch(1)
        outer.addWidget(self.right_body, 1)
        return self.right

    def _build_statusbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("panel")
        bar.setFixedHeight(36)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 4, 14, 4)
        lay.setSpacing(10)

        lb = QLabel("模型:")
        lb.setProperty("role", "dim")
        lay.addWidget(lb)

        self.lamps: dict[str, QLabel] = {}
        for key, name in (("stt", "STT"), ("llm", "LLM"), ("tts", "TTS")):
            lamp = QLabel(f"● {name}")
            lamp.setCursor(Qt.PointingHandCursor)
            lamp.setStyleSheet(f"color:{self.t['text_dim']};")
            lamp.mousePressEvent = lambda _e, k=key: self.goto("model")      # type: ignore[assignment]
            lay.addWidget(lamp)
            self.lamps[key] = lamp

        lay.addStretch(1)

        self.unread_btn = QPushButton("日志 0 条警告")
        self.unread_btn.setProperty("role", "ghost")
        self.unread_btn.clicked.connect(lambda: self.goto("log"))
        lay.addWidget(self.unread_btn)

        ver = QLabel(VERSION)
        ver.setProperty("role", "mono")
        lay.addWidget(ver)
        return bar

    # ================= 连接信号 =================

    def _connect(self) -> None:
        b = self.bridge
        b.device_state.connect(self._on_device_state)
        b.stage_changed.connect(self._on_stage)
        b.stage_timing.connect(self._on_timing)
        b.transcript_ready.connect(self.session_page.on_transcript)
        b.reply_done.connect(self.session_page.on_reply)
        b.tts_started.connect(self.session_page.on_tts_started)
        b.tts_finished.connect(self.session_page.on_tts_finished)
        b.interrupted.connect(self._on_interrupted)
        b.level.connect(self.level.push)
        b.model_status.connect(self._on_model_status)
        b.ack.connect(self._on_ack)
        b.error.connect(self._on_error)
        b.log_line.connect(self._on_log)

        sp = self.session_page
        sp.ask_text.connect(self._ask_text)
        sp.replay_text.connect(self._replay)
        sp.regenerate_text.connect(self._regenerate)
        sp.navigate.connect(self.goto)
        sp.toast.connect(self.toasts.show)

        dp = self.device_page
        dp.command.connect(self._send_command)
        dp.toast.connect(self.toasts.show)

        mp = self.model_page
        mp.toast.connect(self.toasts.show)
        mp.status.connect(self._on_model_status)
        mp.preview_requested.connect(self._preview)

        self.log_page.unread.connect(self._on_unread)

        self._install_shortcuts()

    def _install_shortcuts(self) -> None:
        for i in range(4):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self,
                      activated=lambda idx=i: self.goto_index(idx))
        QShortcut(QKeySequence("Ctrl+,"), self, activated=lambda: self.goto("model"))
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.session_page.clear_chat)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_log_search)
        QShortcut(QKeySequence("F5"), self, activated=self.model_page.test_llm)
        QShortcut(QKeySequence("Ctrl+Shift+C"), self, activated=self._copy_last_reply)
        QShortcut(QKeySequence("Ctrl+R"), self, activated=self.toggle_right)
        QShortcut(QKeySequence("Esc"), self, activated=self._on_escape)

    # ================= 导航 =================

    def goto_index(self, index: int) -> None:
        index = max(0, min(index, self.pages.count() - 1))
        self.pages.setCurrentIndex(index)
        self.nav_group.button(index).setChecked(True)
        if index == 3:
            self.log_page.mark_read()

    def goto(self, key: str) -> None:
        for idx, (k, _label, _icon) in enumerate(_NAV):
            if k == key:
                self.goto_index(idx)
                return

    def toggle_right(self) -> None:
        collapsed = self.right_body.isVisible()          # 当前可见 → 下一步折叠
        self._set_right_collapsed(collapsed)
        self.state["right_collapsed"] = collapsed
        save_ui_state(self.state)

    def _set_right_collapsed(self, collapsed: bool) -> None:
        """折叠只留 44px 竖栏 + 24x24 展开按钮；展开恢复 280px。

        44 - 左右 margin 20 = 24，正好放下按钮，不会被标题挤成几像素。
        """
        self.right_body.setVisible(not collapsed)
        self.right_title.setVisible(not collapsed)
        self.right.setFixedWidth(44 if collapsed else 280)
        # body 隐藏后 QVBoxLayout 会把剩下的唯一一项垂直居中，必须显式顶部对齐
        lay = self.right.layout()
        if lay is not None:
            lay.setAlignment(Qt.AlignTop if collapsed else Qt.Alignment())
        self.collapse_btn.setIcon(icon("chevron_left" if collapsed else "chevron_right",
                                       self.t["text_dim"], 16))
        self.collapse_btn.setToolTip("展开运行状态 (Ctrl+R)" if collapsed
                                     else "折叠运行状态 (Ctrl+R)")

    # ================= 状态处理 =================

    def _on_device_state(self, info: dict) -> None:
        connected = bool(info.get("connected"))
        ip = info.get("ip") or ""
        self.device_pill.set_state("ok" if connected else "error",
                                   f"已连接 {ip}" if connected else "等待设备连接")
        self.right_pill.set_state("ok" if connected else "idle",
                                  "已连接" if connected else "未连接")
        self.device_page.set_device_state(info)
        self.session_page.set_device_ok(connected)
        if not connected:
            self.toasts.show("设备已断开，等待重连", "warn")

    def _on_stage(self, stage: str, state: str) -> None:
        # 新会话（唤醒成功）先把两条阶段条与耗时清空，再画本次状态
        if stage == "wake" and state == "done":
            self.session_page.timeline.reset()
            self.right_stage.reset()
            self._timings.clear()
            self._update_timings()
            self.total_label.setText("本轮 —")
        self.session_page.on_stage(stage, state)
        self.right_stage.set_stage(stage, state)

    def _on_timing(self, stage: str, ms: float) -> None:
        self.session_page.on_stage_timing(stage, ms)
        self.right_stage.set_timing(stage, ms)
        self._timings[stage] = float(ms)
        if stage == "play":
            total = sum(self._timings.get(s, 0.0) for s in ("stt", "llm", "tts", "play"))
            self.total_label.setText(f"本轮 {total / 1000:.2f}s")
        self._update_timings()

    def _update_timings(self) -> None:
        def fmt(key: str) -> str:
            v = self._timings.get(key)
            return "—" if v is None else (f"{v:.0f}ms" if v < 1000 else f"{v / 1000:.2f}s")

        self.timing_label.setText(
            f"STT {fmt('stt')}   LLM {fmt('llm')}   TTS {fmt('tts')}")

    def _on_interrupted(self) -> None:
        self.session_page.on_interrupted()
        self.toasts.show("已打断播报", "warn")

    def _on_error(self, code: int, msg: str) -> None:
        self.session_page.on_failed(msg)
        self.toasts.show(msg, "error")

    def _on_model_status(self, name: str, state: str, detail: str) -> None:
        if name not in self._model_state:
            return
        self._model_state[name] = state
        color = {"ready": self.t["success"], "error": self.t["error"],
                 "loading": self.t["warn"]}.get(state, self.t["text_dim"])
        label = {"stt": "STT", "llm": "LLM", "tts": "TTS"}[name]
        self.lamps[name].setStyleSheet(f"color:{color};")
        self.lamps[name].setText(f"● {label}")
        self.session_page.set_models_ready(self._model_state["stt"] == "ready",
                                          self._model_state["tts"] == "ready")
        self.session_page.set_apikey_ok(self._model_state["llm"] == "ready")
        if state == "error" and detail:
            self.toasts.show(f"{label} 未就绪：{detail.splitlines()[0]}", "warn")

    def _on_ack(self, cmd: str, code: int, result) -> None:
        self.device_page.notify_ack(cmd, code, result)
        if cmd == "config.get" and code == 0 and isinstance(result, dict):
            self.device_page.apply_device_config(result)

    def _on_unread(self, count: int) -> None:
        self.unread_btn.setText(f"日志 {count} 条警告" if count else "日志")

    def _on_log(self, level: str, module: str, message: str) -> None:
        self.log_page.append_log(level, module, message)
        self._log_seen += 1
        if self._log_seen % 40 == 0:
            self.log_page.update_modules()

    # ================= 业务动作 =================

    def _conn(self):
        return getattr(self.server, "connection", None)

    def _run(self, coro) -> None:
        loop = getattr(self.server, "loop", None)
        if loop is None:
            self.toasts.show("asyncio 循环未就绪", "warn")
            return
        asyncio.run_coroutine_threadsafe(coro, loop)

    def _ask_text(self, text: str) -> None:
        conn = self._conn()
        if conn is None:
            self.session_page.on_failed("设备未连接")
            self.toasts.show("设备未连接，文字调试需要设备播放回复", "warn")
            return
        self.session_page.timeline.reset()
        self._run(self.pipeline.ask_text(text, conn))

    def _regenerate(self, text: str) -> None:
        if not text:
            return
        conn = self._conn()
        if conn is None:
            self.toasts.show("设备未连接，无法重新生成", "warn")
            return
        self.session_page.timeline.reset()
        self._run(self.pipeline.ask_text(text, conn))

    def _replay(self, text: str) -> None:
        if not text:
            return
        conn = self._conn()
        if conn is None:
            self.toasts.show("设备未连接，无法重新播报", "warn")
            return
        self._run(self.pipeline.speak_text(text, conn))

    def _preview(self, text: str) -> None:
        conn = self._conn()
        if conn is None:
            self.toasts.show("设备未连接，试听仅合成校验", "warn")
            return
        self._run(self.pipeline.speak_text(text, conn))

    def _send_command(self, cmd: str, params: dict) -> None:
        conn = self._conn()
        if conn is None:
            self.toasts.show("设备未连接，无法下发", "warn")
            return
        self.pipeline.submit_device_request(conn, cmd, params, getattr(self.server, "loop", None))

    # ================= 杂项 =================

    def _copy_listen(self) -> None:
        QGuiApplication.clipboard().setText(f"0.0.0.0:{self.cfg.network.port}")
        self.toasts.show("已复制监听地址", "ok")

    def _copy_last_reply(self) -> None:
        text = self.session_page.last_assistant()
        if text:
            QGuiApplication.clipboard().setText(text)
            self.toasts.show("已复制最后一条回复", "ok")
        else:
            self.toasts.show("还没有助手回复", "warn")

    def _focus_log_search(self) -> None:
        self.goto("log")
        self.log_page.focus_search()

    def _on_escape(self) -> None:
        self.log_page.search.clear()
        self.session_page.input.clear()

    def _startup_probe(self) -> None:
        for key in ("llm", "stt", "tts"):
            self.model_page.probe(key)

    def _restore_state(self) -> None:
        if self.state.get("right_collapsed"):
            self._set_right_collapsed(True)
        self.goto_index(int(self.state.get("page", 0)))

    def closeEvent(self, event) -> None:      # noqa: N802
        self.state.update({"w": self.width(), "h": self.height(),
                           "page": self.pages.currentIndex()})
        save_ui_state(self.state)
        save_config(self.cfg)
        super().closeEvent(event)


def run_gui(cfg, server, pipeline, stt, llm, tts) -> None:
    # 高 DPI：Qt6 默认按整数/分数缩放；这里统一取 PassThrough 避免 150%/200% 下取整错位
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    except AttributeError:
        pass
    for attr in ("AA_EnableHighDpiScaling", "AA_UseHighDpiPixmaps"):
        flag = getattr(Qt, attr, None)
        if flag is not None:
            QApplication.setAttribute(flag, True)

    app = QApplication.instance() or QApplication([])
    theme_mod.apply_theme(app, load_ui_state().get("theme", "dark"))

    # 后台线程运行 asyncio（TCP 服务端），loop 句柄供 GUI 线程投递命令
    loop = asyncio.new_event_loop()
    server.loop = loop

    import logging
    import threading

    def _serve():
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.start())
        except OSError as exc:
            logging.getLogger(__name__).error(
                "TCP 监听 %s:%d 失败：%s（端口被占用？改 config.json 的 network.port）",
                server.host, server.port, exc)
            return
        loop.run_forever()

    threading.Thread(target=_serve, daemon=True).start()

    win = MainWindow(cfg, server, pipeline, stt, llm, tts)
    win.show()
    app.exec()
