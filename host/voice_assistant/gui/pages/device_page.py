"""设备页：连接状态、蓝牙配网、下位机参数读取与下发。"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ... import provision
from ...config import save_config
from ..tasks import run_task
from ..widgets.status_pill import StatusPill

_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def _valid_ip(text: str) -> bool:
    m = _IPV4.match(text.strip())
    return bool(m) and all(0 <= int(g) <= 255 for g in m.groups())


def _fit_spin(spin: QSpinBox, lo: int = 60, hi: int = 170) -> None:
    """按控件自身 sizeHint 定宽，保证"数字 + 上下按钮"都放得下。

    之前写死 64px，小于 QSS 下的自然宽度，数字区被两个按钮挤没、只剩箭头能看见。
    """
    need = max(spin.sizeHint().width(), spin.minimumSizeHint().width())
    spin.setFixedWidth(max(lo, min(need, hi)))


class _RssiBar(QWidget):
    """分段信号强度条。"""

    def __init__(self, t: dict, parent=None):
        super().__init__(parent)
        self.t = t
        self._level = 0
        self.setFixedSize(46, 14)

    def set_rssi(self, rssi: int | None) -> None:
        if rssi is None:
            self._level = 0
        elif rssi >= -55:
            self._level = 4
        elif rssi >= -65:
            self._level = 3
        elif rssi >= -75:
            self._level = 2
        else:
            self._level = 1
        self.update()

    def paintEvent(self, event):      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        color = QColor(self.t["success"] if self._level >= 3 else
                       (self.t["warn"] if self._level == 2 else self.t["error"]))
        gap, w = 2, 8
        for i in range(4):
            h = 4 + i * 3
            x = i * (w + gap)
            y = self.height() - h
            on = i < self._level
            c = QColor(color) if on else QColor(self.t["border"])
            if not on:
                c.setAlpha(120)
            p.fillRect(x, y, w, h, c)
        p.end()


class DevicePage(QWidget):
    command = Signal(str, dict)
    toast = Signal(str, str)

    def __init__(self, t: dict, cfg, parent=None):
        super().__init__(parent)
        self.t = t
        self.cfg = cfg
        self._connected = False
        self._countdown = 0
        self._prov_msgs: list[str] = []      # worker 线程只 append，主线程定时读取
        self._busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        root.addWidget(self._build_conn_card())
        root.addWidget(self._build_prov_card())
        root.addWidget(self._build_param_card(), 1)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)

    # ---------------- 连接卡片 ----------------

    def _build_conn_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("连接")
        title.setProperty("role", "title")
        head.addWidget(title)
        self.pill = StatusPill(self.t, "等待设备连接", "idle")
        head.addWidget(self.pill)
        head.addStretch(1)

        self.disconnect_btn = QPushButton("断开连接")
        self.disconnect_btn.setProperty("role", "danger")
        self.disconnect_btn.clicked.connect(lambda: self.toast.emit("已请求断开连接", "warn"))
        self.disconnect_btn.setEnabled(False)
        head.addWidget(self.disconnect_btn)

        self.relisten_btn = QPushButton("重新监听")
        self.relisten_btn.clicked.connect(
            lambda: self.toast.emit("服务端已在监听，等待设备主动连接", "info"))
        head.addWidget(self.relisten_btn)
        lay.addLayout(head)

        # 两列排布：7 行压到 4 行，窗口矮时更容易一屏看到参数
        grid = QGridLayout()
        grid.setHorizontalSpacing(22)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        def key(text: str) -> QLabel:
            lb = QLabel(text)
            lb.setProperty("role", "dim")
            return lb

        self.ip_label = QLabel("—")
        self.ip_label.setProperty("role", "mono")
        self.mac_label = QLabel("—")
        self.mac_label.setProperty("role", "mono")
        self.fw_label = QLabel("—")
        self.fw_label.setProperty("role", "mono")
        self.wake_label = QLabel("—")
        self.wake_label.setProperty("role", "mono")
        self.vol_label = QLabel("—")
        self.vol_label.setProperty("role", "mono")
        self.audio_label = QLabel("—")
        self.audio_label.setProperty("role", "mono")

        rssi_row = QWidget()
        h = QHBoxLayout(rssi_row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        self.rssi_bar = _RssiBar(self.t)
        h.addWidget(self.rssi_bar)
        self.rssi_label = QLabel("—")
        self.rssi_label.setProperty("role", "mono")
        h.addWidget(self.rssi_label)
        h.addStretch(1)

        grid.addWidget(key("设备 IP"), 0, 0)
        grid.addWidget(self.ip_label, 0, 1)
        grid.addWidget(key("MAC"), 0, 2)
        grid.addWidget(self.mac_label, 0, 3)

        grid.addWidget(key("WiFi 信号"), 1, 0)
        grid.addWidget(rssi_row, 1, 1)
        grid.addWidget(key("固件版本"), 1, 2)
        grid.addWidget(self.fw_label, 1, 3)

        grid.addWidget(key("唤醒词"), 2, 0)
        grid.addWidget(self.wake_label, 2, 1)
        grid.addWidget(key("当前音量"), 2, 2)
        grid.addWidget(self.vol_label, 2, 3)

        grid.addWidget(key("音频参数"), 3, 0)
        grid.addWidget(self.audio_label, 3, 1, 1, 3)

        lay.addLayout(grid)
        return card

    # ---------------- 蓝牙配网 ----------------

    def _build_prov_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        title = QLabel("蓝牙配网")
        title.setProperty("role", "title")
        lay.addWidget(title)

        desc = QLabel("设备连不上路由器时会开启蓝牙广播（VoiceWake-XXXX）。点「扫描」选中设备，"
                      "连接后把 WiFi 凭证与上位机地址直接写进去；设备连上 WiFi 后会关闭蓝牙。")
        desc.setProperty("role", "small")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        # 第一行：扫描 + 设备列表 + 蓝牙可用性提示
        scan_row = QHBoxLayout()
        scan_row.setSpacing(8)
        self.scan_btn = QPushButton("扫描")
        self.scan_btn.clicked.connect(self._scan_prov)
        scan_row.addWidget(self.scan_btn)

        self.prov_combo = QComboBox()
        self.prov_combo.setMinimumWidth(220)
        self.prov_combo.addItem("（未扫描）")
        scan_row.addWidget(self.prov_combo, 1)

        self.ble_hint = QLabel("")
        self.ble_hint.setProperty("role", "small")
        scan_row.addWidget(self.ble_hint)
        lay.addLayout(scan_row)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(6)

        self.ssid = QLineEdit(self.cfg.device.wifi_ssid)
        self.ssid.setPlaceholderText("2.4GHz SSID")
        form.addRow("WiFi 名称", self.ssid)

        self.password = QLineEdit(self.cfg.device.wifi_password)
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("WiFi 密码", self.password)

        # 配网时设备还没连上 TCP，"下位机参数"卡里的 IP/端口下发不了，
        # 所以这两项必须在这里再出现一次（默认取同一份配置）。
        prov_ip_row = QHBoxLayout()
        prov_ip_row.setSpacing(8)
        self.prov_host_ip = QLineEdit(self.cfg.device.host_ip or provision.local_ip())
        self.prov_host_ip.setPlaceholderText("本机 IP，如 192.168.1.10")
        prov_ip_row.addWidget(self.prov_host_ip, 1)
        port_lb = QLabel("端口")
        port_lb.setProperty("role", "dim")
        prov_ip_row.addWidget(port_lb)
        self.prov_host_port = QSpinBox()
        self.prov_host_port.setRange(1, 65535)
        self.prov_host_port.setValue(int(self.cfg.network.port))
        _fit_spin(self.prov_host_port, lo=80)
        prov_ip_row.addWidget(self.prov_host_port)
        form.addRow("上位机地址", prov_ip_row)

        lay.addLayout(form)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.prov_btn = QPushButton("写入设备并连接")
        self.prov_btn.setProperty("role", "primary")
        self.prov_btn.clicked.connect(self._do_provision)
        row.addWidget(self.prov_btn)

        self.prov_enter_btn = QPushButton("让设备进入配网")
        self.prov_enter_btn.setToolTip("已配过网的设备想换 WiFi：让设备重新开蓝牙广播")
        self.prov_enter_btn.clicked.connect(self._ask_enter_prov)
        row.addWidget(self.prov_enter_btn)

        self.wifi_state = QLabel("")
        self.wifi_state.setProperty("role", "small")
        self.wifi_state.setWordWrap(True)
        row.addWidget(self.wifi_state, 1)
        lay.addLayout(row)

        # 蓝牙不可用（没装 bleak / 没有适配器）时禁用相关按钮，避免点了没反应
        ok, why = provision.available()
        if not ok:
            self.ble_hint.setText(why)
            self.ble_hint.setProperty("role", "warn")
            self.scan_btn.setEnabled(False)
            self.prov_btn.setEnabled(False)
        return card

    # ---------------- 参数 ----------------

    def _build_param_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        title = QLabel("下位机参数")
        title.setProperty("role", "title")
        lay.addWidget(title)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)

        vol_row = QWidget()
        hv = QHBoxLayout(vol_row)
        hv.setContentsMargins(0, 0, 0, 0)
        hv.setSpacing(8)
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(self.cfg.device.volume))
        self.volume_spin = QSpinBox()
        self.volume_spin.setRange(0, 100)
        self.volume_spin.setValue(int(self.cfg.device.volume))
        _fit_spin(self.volume_spin)
        self.volume.valueChanged.connect(self.volume_spin.setValue)
        self.volume_spin.valueChanged.connect(self.volume.setValue)
        hv.addWidget(self.volume, 1)
        hv.addWidget(self.volume_spin)
        form.addRow("音量 (0-100)", vol_row)

        self.wake = QComboBox()
        self.wake.addItems(["开启", "关闭"])
        self.wake.setCurrentIndex(0 if self.cfg.device.wake_enable else 1)
        self.wake.setMaximumWidth(180)
        form.addRow("唤醒开关", self.wake)

        # 唤醒回复词：唤醒后先播报这句，播完设备才开始录音
        self.ack_enable = QComboBox()
        self.ack_enable.addItems(["开启", "关闭"])
        self.ack_enable.setCurrentIndex(
            0 if getattr(self.cfg.device, "ack_enable", True) else 1)
        self.ack_enable.setMaximumWidth(180)
        form.addRow("唤醒回复词", self.ack_enable)

        self.ack_text = QLineEdit(getattr(self.cfg.device, "ack_text", "在的"))
        self.ack_text.setPlaceholderText("如：在的 / 我在 / 你好呀")
        self.ack_text.setMaxLength(30)
        self.ack_text.setMaximumWidth(320)
        form.addRow("回复词文本", self.ack_text)

        ack_hint = QLabel("开启后流程：唤醒 → 立即播报该回复词 → 播完才开始采集你的语音")
        ack_hint.setProperty("role", "small")
        ack_hint.setWordWrap(True)
        form.addRow("", ack_hint)

        self.host_ip = QLineEdit(self.cfg.device.host_ip)
        self.host_ip.setPlaceholderText("上位机 IP，如 172.17.253.195")
        form.addRow("上位机 IP", self.host_ip)

        self.host_port = QSpinBox()
        self.host_port.setRange(1, 65535)
        self.host_port.setValue(int(self.cfg.device.host_port or self.cfg.network.port))
        _fit_spin(self.host_port, lo=80)
        form.addRow("上位机端口", self.host_port)
        lay.addLayout(form)

        hint = QLabel("下发后写入设备 NVS；WiFi 与 IP 变更需重启设备生效")
        hint.setProperty("role", "small")
        lay.addWidget(hint)

        row = QHBoxLayout()
        self.read_btn = QPushButton("读取当前配置")
        self.read_btn.clicked.connect(lambda: self.command.emit("config.get", {}))
        row.addWidget(self.read_btn)

        self.apply_btn = QPushButton("下发配置")
        self.apply_btn.setProperty("role", "primary")
        self.apply_btn.clicked.connect(self._send_params)
        row.addWidget(self.apply_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        return card

    # ---------------- 行为 ----------------

    def _scan_prov(self) -> None:
        """扫描附近的 VoiceWake-XXXX（蓝牙，不依赖网络）。"""
        if self._busy:
            return
        self._busy = True
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText("扫描中…")
        self.prov_combo.clear()
        self.prov_combo.addItem("扫描中…")
        self._prov_msgs.clear()
        self.wifi_state.setText("正在扫描蓝牙设备（约 6 秒）…")
        run_task(lambda: provision.scan(6.0), self._on_scan_done, tag="prov-scan")

    def _on_scan_done(self, tag: str, result, exc) -> None:
        self._busy = False
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText("扫描")
        self.prov_combo.clear()

        if exc is not None:
            self.prov_combo.addItem("（未扫描）")
            self.wifi_state.setText(f"扫描失败：{exc}")
            self.toast.emit(f"蓝牙扫描失败：{exc}", "error")
            return

        found = result or []
        if not found:
            self.prov_combo.addItem("（没找到设备）")
            self.wifi_state.setText("未发现设备。请确认设备已进入配网（日志出现"
                                    "「进入蓝牙配网」）且在本机蓝牙范围内。")
            return

        for d in found:
            self.prov_combo.addItem(f"{d['name']}   {d['rssi']} dBm", d["address"])
        self.wifi_state.setText(f"找到 {len(found)} 台设备，选中后填写凭证并写入。")

    def _do_provision(self) -> None:
        """把 SSID/密码 + 上位机 IP/端口通过蓝牙写入设备。"""
        if self._busy:
            return
        address = self.prov_combo.currentData()
        if not address:
            self.toast.emit("请先扫描并选中一台设备", "warn")
            return

        ssid = self.ssid.text().strip()
        if not ssid:
            self._invalid(self.ssid, True)
            self.toast.emit("WiFi 名称不能为空", "warn")
            return
        self._invalid(self.ssid, False)

        ip = self.prov_host_ip.text().strip()
        if not _valid_ip(ip):
            self._invalid(self.prov_host_ip, True)
            self.toast.emit("上位机 IP 格式不正确", "warn")
            return
        self._invalid(self.prov_host_ip, False)

        port = int(self.prov_host_port.value())
        password = self.password.text()

        self._busy = True
        self._prov_msgs.clear()
        self.prov_btn.setEnabled(False)
        self.prov_btn.setText("配网中…")
        self.scan_btn.setEnabled(False)
        self.wifi_state.setText("开始配网…")
        self._timer.start()

        def job():
            return provision.provision(
                address, ssid, password, ip, port,
                report=self._prov_msgs.append)

        run_task(job, self._on_prov_done, tag="prov-apply")

    def _on_prov_done(self, tag: str, result, exc) -> None:
        self._busy = False
        self._timer.stop()
        self.prov_btn.setEnabled(True)
        self.prov_btn.setText("写入设备并连接")
        self.scan_btn.setEnabled(True)

        if exc is not None:
            self.wifi_state.setText(f"配网失败：{exc}")
            self.toast.emit(f"配网失败：{exc}", "error")
            return

        info = result or {}
        if info.get("state") == "ok":
            # 记下这份凭证，下次打开界面能直接带出来
            self.cfg.device.wifi_ssid = self.ssid.text().strip()
            self.cfg.device.wifi_password = self.password.text()
            self.cfg.device.host_ip = self.prov_host_ip.text().strip()
            self.cfg.device.host_port = int(self.prov_host_port.value())
            save_config(self.cfg)

            ip = info.get("ip", "")
            self.wifi_state.setText(f"配网成功：设备已连上 WiFi（{ip}），蓝牙已关闭，"
                                    f"正在等待设备连接上位机…")
            self.toast.emit("配网成功，设备已连接 WiFi", "ok")
        else:
            err = info.get("error") or "未知原因"
            self.wifi_state.setText(f"配网失败：{err}（蓝牙仍开着，可改密码后重试）")
            self.toast.emit(f"配网失败：{err}", "error")

    def _ask_enter_prov(self) -> None:
        """让已连上的设备重新打开蓝牙配网（换 WiFi 用）。"""
        if not self._connected:
            self.toast.emit("设备未连接：请先断电重上电，设备会自行进入配网", "warn")
            return
        self.command.emit("prov.start", {})
        self.wifi_state.setText("已请求设备进入配网，几秒后点「扫描」…")

    def _invalid(self, w: QLineEdit, bad: bool) -> None:
        w.setProperty("invalid", "true" if bad else "false")
        w.style().unpolish(w)
        w.style().polish(w)

    def _tick(self) -> None:
        """轮询 worker 线程写的进度（线程间只共享 list，不碰控件）。"""
        if self._prov_msgs:
            self.wifi_state.setText(self._prov_msgs[-1][:90])
        if not self._busy:
            self._timer.stop()

    def _send_params(self) -> None:
        ip = self.host_ip.text().strip()
        if ip and not _valid_ip(ip):
            self._invalid(self.host_ip, True)
            self.toast.emit("上位机 IP 格式不正确", "warn")
            return
        self._invalid(self.host_ip, False)

        params = {
            "volume": int(self.volume.value()),
            "wake_enable": self.wake.currentIndex() == 0,
            "ack_enable": self.ack_enable.currentIndex() == 0,
            "host": {"ip": ip, "port": int(self.host_port.value())},
        }
        self.cfg.device.volume = params["volume"]
        self.cfg.device.wake_enable = params["wake_enable"]
        self.cfg.device.ack_enable = params["ack_enable"]
        self.cfg.device.ack_text = self.ack_text.text().strip() or "在的"
        self.cfg.device.host_ip = ip
        self.cfg.device.host_port = params["host"]["port"]
        save_config(self.cfg)
        self.command.emit("config.set", params)
        self.apply_btn.setEnabled(False)
        QTimer.singleShot(2500, lambda: self.apply_btn.setEnabled(True))

    # ---------------- 外部更新 ----------------

    def set_device_state(self, info: dict) -> None:
        connected = bool(info.get("connected"))
        self._connected = connected
        self.disconnect_btn.setEnabled(connected)

        if connected:
            ip = info.get("ip") or "—"
            self.pill.set_state("ok", f"已连接 {ip}")
        else:
            self.pill.set_state("error", "等待设备连接")

        self.ip_label.setText(str(info.get("ip") or "—"))
        self.mac_label.setText(str(info.get("mac") or "—"))
        self.fw_label.setText(str(info.get("fw") or "—"))
        self.wake_label.setText(str(info.get("wakeword") or "—"))

        rssi = info.get("rssi")
        try:
            rssi_val = int(rssi) if rssi is not None else None
        except (TypeError, ValueError):
            rssi_val = None
        self.rssi_bar.set_rssi(rssi_val)
        self.rssi_label.setText(f"{rssi_val} dBm" if rssi_val is not None else "—")

        audio = info.get("audio") or {}
        if audio:
            self.audio_label.setText(
                f"{audio.get('rate', '?')} Hz / {audio.get('bits', '?')} bit / "
                f"{audio.get('ch', '?')} ch")
        vol = info.get("volume")
        self.vol_label.setText(str(vol) if vol is not None else "—")

    def apply_device_config(self, cfg: dict) -> None:
        """config.get 的应答回填。

        注意：固件 `config.get` 返回的是**扁平字段** host_ip / host_port（不是嵌套 host 对象），
        历史上这里按 `host` 取导致 IP/端口永远读不回来。
        """
        if not cfg:
            return
        if "volume" in cfg:
            self.volume.setValue(int(cfg["volume"]))
        if "wake_enable" in cfg:
            self.wake.setCurrentIndex(0 if cfg["wake_enable"] else 1)
        if "ack_enable" in cfg:
            self.ack_enable.setCurrentIndex(0 if cfg["ack_enable"] else 1)
        if cfg.get("host_ip"):
            self.host_ip.setText(str(cfg["host_ip"]))
        if cfg.get("host_port"):
            self.host_port.setValue(int(cfg["host_port"]))
        self.toast.emit("已读取设备当前配置", "ok")

    def notify_ack(self, cmd: str, code: int, result) -> None:
        if cmd in ("config.set", "wifi.set", "volume.set"):
            if code == 0:
                self.toast.emit(f"{cmd} 下发成功", "ok")
            else:
                self.toast.emit(f"{cmd} 下发失败：code={code} {result}", "error")
                self.apply_btn.setEnabled(True)
