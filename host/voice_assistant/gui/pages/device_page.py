"""设备页：连接状态、AP 配网兜底、下位机参数读取与下发。"""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from ...config import save_config
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

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        root.addWidget(self._build_conn_card())
        root.addWidget(self._build_ap_card())
        root.addWidget(self._build_param_card(), 1)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
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

    # ---------------- AP 配网 ----------------

    def _build_ap_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        title = QLabel("AP 配网兜底")
        title.setProperty("role", "title")
        lay.addWidget(title)

        desc = QLabel("设备连不上路由器时会开热点 VoiceWake-XXXX：连上后在此下发 WiFi 凭证，"
                      "设备写入 NVS 并重启重连。")
        desc.setProperty("role", "small")
        desc.setWordWrap(True)
        lay.addWidget(desc)

        form = QFormLayout()
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(6)
        self.ssid = QLineEdit(self.cfg.device.wifi_ssid)
        self.ssid.setPlaceholderText("2.4GHz SSID")
        form.addRow("WiFi 名称", self.ssid)
        self.password = QLineEdit(self.cfg.device.wifi_password)
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("WiFi 密码", self.password)
        lay.addLayout(form)

        row = QHBoxLayout()
        self.wifi_btn = QPushButton("下发 WiFi 配置")
        self.wifi_btn.setProperty("role", "primary")
        self.wifi_btn.clicked.connect(self._send_wifi)
        row.addWidget(self.wifi_btn)
        self.wifi_state = QLabel("")
        self.wifi_state.setProperty("role", "small")
        row.addWidget(self.wifi_state)
        row.addStretch(1)
        lay.addLayout(row)
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

    def _send_wifi(self) -> None:
        ssid = self.ssid.text().strip()
        if not ssid:
            self._invalid(self.ssid, True)
            self.toast.emit("WiFi 名称不能为空", "warn")
            return
        self._invalid(self.ssid, False)
        self.command.emit("wifi.set", {"ssid": ssid, "password": self.password.text()})
        self.wifi_state.setText("已下发，等待设备重启重连…")
        self._countdown = 20
        self._timer.start()

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

    def _invalid(self, w: QLineEdit, bad: bool) -> None:
        w.setProperty("invalid", "true" if bad else "false")
        w.style().unpolish(w)
        w.style().polish(w)

    def _tick(self) -> None:
        self._countdown -= 1
        if self._countdown <= 0:
            self._timer.stop()
            self.wifi_state.setText("")
        else:
            self.wifi_state.setText(f"已下发，等待设备重启重连… {self._countdown}s")

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
        """config.get 的应答回填。"""
        if not cfg:
            return
        if "volume" in cfg:
            self.volume.setValue(int(cfg["volume"]))
        if "wake_enable" in cfg:
            self.wake.setCurrentIndex(0 if cfg["wake_enable"] else 1)
        if "ack_enable" in cfg:
            self.ack_enable.setCurrentIndex(0 if cfg["ack_enable"] else 1)
        host = cfg.get("host") or {}
        if host.get("ip"):
            self.host_ip.setText(str(host["ip"]))
        if host.get("port"):
            self.host_port.setValue(int(host["port"]))
        self.toast.emit("已读取设备当前配置", "ok")

    def notify_ack(self, cmd: str, code: int, result) -> None:
        if cmd in ("config.set", "wifi.set", "volume.set"):
            if code == 0:
                self.toast.emit(f"{cmd} 下发成功", "ok")
            else:
                self.toast.emit(f"{cmd} 下发失败：code={code} {result}", "error")
                self.apply_btn.setEnabled(True)
