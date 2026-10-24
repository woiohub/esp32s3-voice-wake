"""模型页：STT / LLM / TTS 三张卡片，字段校验 + 异步自检 + 保存。"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSlider, QSpinBox, QToolButton,
    QVBoxLayout, QWidget,
)

from ...config import DEFAULT_CONFIG_PATH, PROVIDER_PRESETS, save_config
from ...model_download import (
    STT_REPOS, download as download_models, stt_missing, stt_target,
    tts_missing, tts_target,
)
from ..icons import icon
from ..tasks import run_task
from ..widgets.model_status_card import ModelStatusCard


def _host_root() -> str:
    """host/ 目录（config.json 与 models/ 的基准路径）。"""
    return os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))


class ModelPage(QWidget):
    toast = Signal(str, str)
    status = Signal(str, str, str)          # (name, state, detail)
    saved = Signal()
    preview_requested = Signal(str)         # 试听文本

    def __init__(self, t: dict, cfg, stt, llm, tts, parent=None):
        super().__init__(parent)
        self.t = t
        self.cfg = cfg
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self._probing = set()

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.cards = {}

        stt_card = ModelStatusCard(t, "stt", "STT · 语音识别")
        self._build_stt(stt_card.body_layout)
        stt_card.retry_requested.connect(lambda key: self.probe(key))
        cards.addWidget(stt_card, 1)
        self.cards["stt"] = stt_card

        llm_card = ModelStatusCard(t, "llm", "LLM · 大模型")
        self._build_llm(llm_card.body_layout)
        llm_card.retry_requested.connect(lambda key: self.probe(key))
        cards.addWidget(llm_card, 1)
        self.cards["llm"] = llm_card

        tts_card = ModelStatusCard(t, "tts", "TTS · 语音合成")
        self._build_tts(tts_card.body_layout)
        tts_card.retry_requested.connect(lambda key: self.probe(key))
        cards.addWidget(tts_card, 1)
        self.cards["tts"] = tts_card

        root.addLayout(cards, 1)

        bottom = QHBoxLayout()
        for key in ("stt", "llm", "tts"):
            self._hint(key)
        bottom.addStretch(1)

        self.dl_label = QLabel("")
        self.dl_label.setProperty("role", "small")
        bottom.addWidget(self.dl_label)
        self.dl_btn = QPushButton("一键下载模型")
        self.dl_btn.setToolTip("按当前选择下载缺失的 STT / TTS 模型（默认关闭自动下载）")
        self.dl_btn.clicked.connect(self.download_models)
        bottom.addWidget(self.dl_btn)

        self.save_btn = QPushButton("保存")
        self.save_btn.setProperty("role", "primary")
        self.save_btn.clicked.connect(self.save)
        bottom.addWidget(self.save_btn)
        root.addLayout(bottom)

        self._dl_msgs: list[str] = []
        self._dl_timer = QTimer(self)
        self._dl_timer.setInterval(400)
        self._dl_timer.timeout.connect(self._poll_download)

    # ---------------- 字段构建 ----------------

    def _hint(self, key: str) -> QLabel:
        lbl = QLabel("")
        lbl.setProperty("role", "small")
        lbl.setObjectName(f"hint_{key}")
        setattr(self, f"hint_{key}", lbl)
        return lbl

    def _form(self, layout) -> QFormLayout:
        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(7)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addLayout(form)
        return form

    def _build_stt(self, layout) -> None:
        form = self._form(layout)

        self.stt_size = QComboBox()
        self.stt_size.addItems(["tiny", "base", "small", "medium"])
        self.stt_size.setCurrentText(self.cfg.stt.model_size)
        form.addRow("模型大小", self.stt_size)

        self.stt_lang = QComboBox()
        self.stt_lang.addItems(["zh", "en", "auto"])
        self.stt_lang.setCurrentText(self.cfg.stt.language)
        form.addRow("语言", self.stt_lang)

        self.stt_endpoint = QLineEdit(self.cfg.stt.hf_endpoint)
        self.stt_endpoint.setPlaceholderText("https://hf-mirror.com")
        form.addRow("HF 镜像", self.stt_endpoint)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.stt_dir = QLineEdit(self.cfg.stt.model_dir)
        self.stt_dir.setPlaceholderText("留空 = 自动下载")
        h.addWidget(self.stt_dir, 1)
        btn = QToolButton()
        btn.setIcon(icon("folder", self.t["text_dim"], 16))
        btn.setToolTip("选择本地模型目录")
        btn.clicked.connect(self._pick_stt_dir)
        h.addWidget(btn)
        form.addRow("本地模型目录", row)

        self.auto_dl = QCheckBox("允许程序自动下载（关闭时只用本地模型）")
        self.auto_dl.setChecked(bool(getattr(self.cfg.stt, "auto_download", False)))
        self.auto_dl.setToolTip("默认关闭：找不到模型时报错提示，不会静默联网下载")
        form.addRow("", self.auto_dl)

        layout.addWidget(self._hint("stt"))

    def _build_llm(self, layout) -> None:
        form = self._form(layout)

        self.provider = QComboBox()
        self.provider.addItems(list(PROVIDER_PRESETS.keys()))
        self.provider.setCurrentText(self.cfg.llm.provider)
        self.provider.currentTextChanged.connect(self._on_provider)
        form.addRow("提供方", self.provider)

        self.base_url = QLineEdit(self.cfg.llm.base_url)
        form.addRow("base_url", self.base_url)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.api_key = QLineEdit(self.cfg.llm.api_key)
        self.api_key.setEchoMode(QLineEdit.Password)
        self.api_key.setPlaceholderText("必填")
        h.addWidget(self.api_key, 1)
        self.eye = QToolButton()
        self.eye.setIcon(icon("eye", self.t["text_dim"], 16))
        self.eye.setCheckable(True)
        self.eye.toggled.connect(self._toggle_key)
        h.addWidget(self.eye)
        form.addRow("api_key", row)

        self.llm_model = QLineEdit(self.cfg.llm.model)
        form.addRow("模型名", self.llm_model)

        self.temperature = QDoubleSpinBox()
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.05)
        self.temperature.setValue(float(self.cfg.llm.temperature))
        form.addRow("temperature", self.temperature)

        self.max_tokens = QSpinBox()
        self.max_tokens.setRange(16, 4096)
        self.max_tokens.setValue(int(self.cfg.llm.max_tokens))
        form.addRow("max_tokens", self.max_tokens)

        self.system_prompt = QPlainTextEdit(self.cfg.llm.system_prompt)
        self.system_prompt.setFixedHeight(54)
        form.addRow("system", self.system_prompt)

        row2 = QWidget()
        h2 = QHBoxLayout(row2)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(6)
        self.test_btn = QPushButton("测试大模型")
        self.test_btn.clicked.connect(self.test_llm)
        h2.addWidget(self.test_btn)
        self.test_result = QLabel("")
        self.test_result.setProperty("role", "small")
        self.test_result.setWordWrap(True)
        h2.addWidget(self.test_result, 1)
        layout.addWidget(row2)

        layout.addWidget(self._hint("llm"))

    def _build_tts(self, layout) -> None:
        form = self._form(layout)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.tts_path = QLineEdit(self.cfg.tts.model_path)
        h.addWidget(self.tts_path, 1)
        btn = QToolButton()
        btn.setIcon(icon("folder", self.t["text_dim"], 16))
        btn.setToolTip("选择 .onnx 模型")
        btn.clicked.connect(self._pick_tts)
        h.addWidget(btn)
        form.addRow("模型文件", row)

        self.speaker = QSpinBox()
        self.speaker.setRange(0, 99)
        self.speaker.setValue(int(self.cfg.tts.speaker))
        form.addRow("音色 ID", self.speaker)

        self.length = QSlider(Qt.Horizontal)
        self.length.setRange(50, 200)
        self.length.setValue(int(float(self.cfg.tts.length_scale) * 100))
        self.length_label = QLabel(f"{self.length.value() / 100:.2f}")
        self.length.valueChanged.connect(
            lambda v: self.length_label.setText(f"{v / 100:.2f}"))
        row2 = QWidget()
        h2 = QHBoxLayout(row2)
        h2.setContentsMargins(0, 0, 0, 0)
        h2.setSpacing(8)
        h2.addWidget(self.length, 1)
        h2.addWidget(self.length_label)
        form.addRow("语速(时长×)", row2)

        self.preview_btn = QPushButton("试听")
        self.preview_btn.clicked.connect(self._preview)
        form.addRow("", self.preview_btn)

        layout.addWidget(self._hint("tts"))

    # ---------------- 交互 ----------------

    def _toggle_key(self, shown: bool) -> None:
        self.api_key.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.eye.setIcon(icon("eye_off" if shown else "eye", self.t["text_dim"], 16))

    def _on_provider(self, name: str) -> None:
        preset = PROVIDER_PRESETS.get(name)
        if not preset:
            return
        if self.base_url.text().strip() in {p["base_url"] for p in PROVIDER_PRESETS.values()} \
                or not self.base_url.text().strip():
            self.base_url.setText(preset["base_url"])
        if self.llm_model.text().strip() in {p["model"] for p in PROVIDER_PRESETS.values()} \
                or not self.llm_model.text().strip():
            self.llm_model.setText(preset["model"])

    def _pick_stt_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择 faster-whisper 模型目录")
        if path:
            self.stt_dir.setText(path)

    def _pick_tts(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 Piper 模型", "", "ONNX (*.onnx)")
        if path:
            self.tts_path.setText(path)

    # ---------------- 校验 ----------------

    def _mark(self, w: QWidget, bad: bool) -> None:
        w.setProperty("invalid", "true" if bad else "false")
        w.style().unpolish(w)
        w.style().polish(w)

    def validate(self) -> bool:
        ok = True

        # LLM
        err = ""
        if not self.api_key.text().strip():
            err = "api_key 必填"
        elif not self.base_url.text().strip().startswith("http"):
            err = "base_url 必须以 http(s):// 开头"
        elif not self.llm_model.text().strip():
            err = "模型名不能为空"
        self.hint_llm.setText(err)
        self.hint_llm.setProperty("role", "error" if err else "small")
        if err:
            ok = False
            self._mark(self.api_key, not self.api_key.text().strip())
            self._mark(self.base_url, not self.base_url.text().strip().startswith("http"))

        # TTS
        path = self.tts_path.text().strip()
        full = path if os.path.isabs(path) else os.path.join(_host_root(), path)
        tts_err = ""
        if not path:
            tts_err = "模型文件不能为空"
        elif not os.path.exists(full):
            tts_err = "模型文件不存在"
        elif not os.path.exists(full + ".json"):
            tts_err = "缺少同名 .onnx.json 配置文件"
        self.hint_tts.setText(tts_err)
        self.hint_tts.setProperty("role", "error" if tts_err else "small")
        if tts_err:
            ok = False
            self._mark(self.tts_path, True)

        # STT：本地目录必须存在且文件齐全；未指定时必须显式允许自动下载
        ep = self.stt_endpoint.text().strip()
        stt_err = "" if (not ep or ep.startswith("http")) else "HF 镜像必须是 URL"
        if not stt_err:
            md = self.stt_dir.text().strip()
            if md:
                full = md if os.path.isabs(md) else os.path.join(_host_root(), md)
                if not os.path.isdir(full):
                    stt_err = "模型目录不存在"
                else:
                    miss = [f for f in ("model.bin", "config.json", "tokenizer.json")
                            if not os.path.exists(os.path.join(full, f))]
                    if miss:
                        stt_err = "目录缺少：" + "、".join(miss)
            elif not self.auto_dl.isChecked():
                stt_err = "请指定本地模型目录，或勾选「允许程序自动下载」"
        self.hint_stt.setText(stt_err)
        self.hint_stt.setProperty("role", "error" if stt_err else "small")
        if stt_err:
            ok = False

        for lbl in (self.hint_llm, self.hint_tts, self.hint_stt):
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)
        return ok

    # ---------------- 保存 ----------------

    def _apply_fields(self) -> None:
        """把界面值写回 cfg 并落盘（不做校验，供「一键下载」等场景复用）。"""
        self.cfg.stt.model_size = self.stt_size.currentText()
        self.cfg.stt.language = self.stt_lang.currentText()
        self.cfg.stt.hf_endpoint = self.stt_endpoint.text().strip()
        self.cfg.stt.model_dir = self.stt_dir.text().strip()
        self.cfg.stt.auto_download = bool(self.auto_dl.isChecked())

        self.cfg.llm.provider = self.provider.currentText()
        self.cfg.llm.base_url = self.base_url.text().strip()
        self.cfg.llm.api_key = self.api_key.text().strip()
        self.cfg.llm.model = self.llm_model.text().strip()
        self.cfg.llm.temperature = float(self.temperature.value())
        self.cfg.llm.max_tokens = int(self.max_tokens.value())
        self.cfg.llm.system_prompt = self.system_prompt.toPlainText().strip()

        self.cfg.tts.model_path = self.tts_path.text().strip()
        self.cfg.tts.speaker = int(self.speaker.value())
        self.cfg.tts.length_scale = self.length.value() / 100.0

        save_config(self.cfg)
        try:
            self.llm.reset()
        except AttributeError:
            pass

    def save(self) -> None:
        if not self.validate():
            self.toast.emit("有字段未通过校验，请按提示修正", "warn")
            return

        self._apply_fields()
        self.toast.emit(f"配置已保存到 {DEFAULT_CONFIG_PATH}", "ok")
        self.saved.emit()
        # 路径/规格可能变了，三个模型都重新自检一次
        for key in ("llm", "stt", "tts"):
            self.probe(key)

    # ---------------- 一键下载 ----------------

    def download_models(self) -> None:
        """按当前选择下载缺失的模型；已存在的文件不会重复下载。"""
        self._apply_fields()

        size = self.stt_size.currentText()
        mb = STT_REPOS.get(size, ("", 0))[1]
        need_stt = stt_missing(stt_target(self.cfg, size))
        need_tts = tts_missing(tts_target(self.cfg))
        if not need_stt and not need_tts:
            self.toast.emit("模型文件已齐全，无需下载", "ok")
            return

        items = []
        if need_stt:
            items.append(f"STT faster-whisper-{size}（约 {mb} MB）")
        if need_tts:
            items.append("TTS zh_CN-huayan-medium（约 63 MB）")

        ok = QMessageBox.question(
            self, "下载轻量化模型",
            "将从 %s 下载：\n\n  · %s\n\n下载目录：host/models/\n是否继续？"
            % (self.cfg.stt.hf_endpoint or "https://hf-mirror.com", "\n  · ".join(items)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ok != QMessageBox.Yes:
            return

        kinds = tuple(k for k, need in (("stt", need_stt), ("tts", need_tts)) if need)
        self._dl_msgs.clear()
        self._dl_msgs.append("开始下载…")
        self.dl_btn.setEnabled(False)
        self.dl_btn.setText("下载中…")
        self.dl_label.setText("开始下载…")
        self._dl_timer.start()

        # report 在 worker 线程调用，只往列表里追加，由定时器在主线程读取
        run_task(
            lambda: download_models(self.cfg, kinds=kinds, size=size,
                                    report=self._dl_msgs.append),
            self._on_download_done, tag="download")

    def _poll_download(self) -> None:
        if self._dl_msgs:
            self.dl_label.setText(self._dl_msgs[-1][:64])

    def _on_download_done(self, tag: str, result, exc) -> None:
        self._dl_timer.stop()
        self.dl_btn.setEnabled(True)
        self.dl_btn.setText("一键下载模型")
        if exc is not None:
            self.dl_label.setText("下载失败")
            self.toast.emit(f"模型下载失败：{exc}", "error")
            return
        self.dl_label.setText("下载完成")
        self.toast.emit("模型下载完成", "ok")
        self.probe("stt")
        self.probe("tts")

    # ---------------- 自检 ----------------

    def probe(self, key: str) -> None:
        if key in self._probing:
            return
        self._probing.add(key)
        self.cards[key].set_state("loading")
        self.status.emit(key, "loading", "检测中…")

        if key == "stt":
            fn = self.stt.preload
        elif key == "tts":
            fn = self.tts.preload
        else:
            fn = self.llm.preload

        run_task(fn, self._on_probe_done, tag=key)

    def _on_probe_done(self, tag: str, result, exc) -> None:
        self._probing.discard(tag)
        card = self.cards.get(tag)
        if card is None:
            return
        if exc is not None:
            msg = str(exc)
            card.set_state("error", msg)
            self.status.emit(tag, "error", msg)
            return
        if result:
            card.set_state("error", str(result))
            self.status.emit(tag, "error", str(result))
        else:
            card.set_state("ready")
            self.status.emit(tag, "ready", "")

    def test_llm(self) -> None:
        if not self.validate():
            self.toast.emit("请先修正大模型配置", "warn")
            return
        self.save()
        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中…")
        self.test_result.setProperty("role", "small")
        self.test_result.setText("正在请求…")

        def _call():
            self.llm.reset()
            import time
            t0 = time.perf_counter()
            reply = self.llm.reply("你好")
            return (time.perf_counter() - t0) * 1000.0, reply

        run_task(_call, self._on_test_done, tag="llm-test")

    def _on_test_done(self, tag: str, result, exc) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("测试大模型")
        if exc is not None:
            self.test_result.setText(f"失败：{exc}")
            self.test_result.setProperty("role", "error")
            self.toast.emit(f"大模型测试失败：{exc}", "error")
        else:
            ms, reply = result
            self.test_result.setText(f"成功 {ms:.0f}ms · {reply[:36]}")
            self.test_result.setProperty("role", "ok")
            self.toast.emit(f"大模型可用（{ms:.0f}ms）", "ok")
        self.test_result.style().unpolish(self.test_result)
        self.test_result.style().polish(self.test_result)

    def _preview(self) -> None:
        if not self.validate():
            self.toast.emit("请先修正 TTS 配置", "warn")
            return
        self.save()
        self.preview_requested.emit("你好，我是语音助手，现在为你播报。")
