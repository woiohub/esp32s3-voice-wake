"""主题：颜色 / 尺寸 / 字体 token + QSS 生成。

约定：
- 页面与组件里**不允许**出现颜色字面量，一律用 `theme.apply_theme()` 返回的 token，
  或使用带 `role` 属性的对象由 QSS 统一样式。
- 深色为主，浅色通过同一份 token 结构切换。
"""
from __future__ import annotations

import os
import tempfile

from PySide6.QtGui import QFont

# ---------------------------------------------------------------- token

DARK = {
    "bg":        "#0F1115",
    "panel":     "#161A21",
    "hover":     "#1D232D",
    "border":    "#262C36",
    "text":      "#E6EAF2",
    "text_dim":  "#8B95A7",
    "primary":   "#4F8CFF",
    "success":   "#3DD68C",
    "warn":      "#F5A524",
    "error":     "#F2555A",
    "on_primary": "#FFFFFF",
    "radius":    10,
    "radius_sm": 8,
    "sp1": 4, "sp2": 8, "sp3": 12, "sp4": 16, "sp5": 24,
    "fs_body": 13, "fs_small": 12, "fs_title": 15, "fs_h1": 18,
    "font": "'Microsoft YaHei UI', 'Segoe UI', 'PingFang SC', sans-serif",
    "mono": "Consolas, 'JetBrains Mono', 'Cascadia Mono', monospace",
}

LIGHT = {
    "bg":        "#F5F7FA",
    "panel":     "#FFFFFF",
    "hover":     "#EDF1F7",
    "border":    "#DCE2EB",
    "text":      "#1B2330",
    "text_dim":  "#66707F",
    "primary":   "#2F6FE4",
    "success":   "#1F9D63",
    "warn":      "#B87200",
    "error":     "#C9363B",
    "on_primary": "#FFFFFF",
    "radius":    10,
    "radius_sm": 8,
    "sp1": 4, "sp2": 8, "sp3": 12, "sp4": 16, "sp5": 24,
    "fs_body": 13, "fs_small": 12, "fs_title": 15, "fs_h1": 18,
    "font": "'Microsoft YaHei UI', 'Segoe UI', 'PingFang SC', sans-serif",
    "mono": "Consolas, 'JetBrains Mono', 'Cascadia Mono', monospace",
}

_current = dict(DARK)


def tokens(mode: str = "dark") -> dict:
    return dict(DARK if mode != "light" else LIGHT)


def current() -> dict:
    return _current


# ---------------------------------------------------------------- QSS

_QSS = """
* { outline: none; }
QWidget { color: $text; font-size: ${fs_body}px; font-family: $font; }
QWidget#root { background: $bg; }
QWidget#transparent { background: transparent; }

QFrame#panel {
    background: $panel;
    border: 1px solid $border;
    border-radius: ${radius}px;
}
QFrame#card {
    background: $panel;
    border: 1px solid $border;
    border-radius: ${radius}px;
}
QFrame#sep { background: $border; max-height: 1px; min-height: 1px; border: none; }

QLabel { background: transparent; }
QLabel[role="dim"]  { color: $text_dim; }
QLabel[role="h1"]   { font-size: ${fs_h1}px; font-weight: 600; }
QLabel[role="title"]{ font-size: ${fs_title}px; font-weight: 600; }
QLabel[role="small"]{ font-size: ${fs_small}px; color: $text_dim; }
QLabel[role="mono"] { font-family: $mono; font-size: ${fs_small}px; color: $text_dim; }
QLabel[role="error"]{ color: $error; }
QLabel[role="ok"]   { color: $success; }
QLabel[role="warn"] { color: $warn; }

QPushButton {
    background: $panel;
    border: 1px solid $border;
    border-radius: ${radius_sm}px;
    padding: 6px 12px;
    color: $text;
}
QPushButton:hover   { background: $hover; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { color: $text_dim; }
QPushButton:focus   { border: 1px solid $primary; }
QPushButton[role="primary"] {
    background: $primary; border-color: $primary; color: $on_primary; font-weight: 600;
}
QPushButton[role="primary"]:hover { background: $primary; }
QPushButton[role="ghost"] { background: transparent; border-color: transparent; }
QPushButton[role="ghost"]:hover { background: $hover; }
QPushButton[role="danger"] { color: $error; border-color: $border; }

/* 所有 QToolButton 默认透明；否则未命名的按钮会走系统原生样式 → 深色主题下出现白色方块 */
QToolButton {
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 2px;
}
QToolButton:hover   { background: $hover; border-color: $border; }
QToolButton:pressed { background: $border; }
QToolButton:disabled { background: transparent; }

QToolButton#nav {
    background: transparent;
    border: 1px solid transparent;
    border-radius: ${radius}px;
    color: $text_dim;
    padding: 6px 0;
}
QToolButton#nav:hover   { background: $hover; color: $text; }
QToolButton#nav:checked { background: $hover; color: $primary; border-color: $border; }
QToolButton#nav:focus   { border: 1px solid $primary; }

QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $bg;
    border: 1px solid $border;
    border-radius: ${radius_sm}px;
    padding: 5px 8px;
    min-height: 18px;
    selection-background-color: $primary;
    selection-color: $on_primary;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {
    border: 1px solid $primary;
}
QLineEdit[invalid="true"] { border: 1px solid $error; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: $panel; border: 1px solid $border;
    selection-background-color: $hover; selection-color: $text;
}

/*
 * QSpinBox 的上下按钮不要用 QSS 接管：一旦写了 ::up-button，Qt 就不再绘制原生箭头，
 * 而边框三角的写法并不可靠（实测箭头整块消失）。这里保留原生按钮与箭头，
 * 宽度交给代码里的 _fit_spin() 按“最大值的实际像素宽 + 按钮占位”算足。
 */
QSpinBox, QDoubleSpinBox { padding-right: 4px; }

/* 复选框：不写样式时是系统浅色方框，在深色底上很扎眼 */
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator {
    width: 14px; height: 14px;
    border: 1px solid $border;
    border-radius: 4px;
    background: $bg;
}
QCheckBox::indicator:hover { border-color: $primary; }
QCheckBox::indicator:checked {
    image: url(${check_png});
    background: $primary;
    border: 1px solid $primary;
}
QCheckBox::indicator:disabled { background: $panel; border-color: $border; }

QSlider::groove:horizontal { height: 4px; background: $border; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $primary; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 14px; height: 14px; margin: -6px 0;
    background: $text; border: 2px solid $primary; border-radius: 8px;
}
QSlider::handle:horizontal:hover { background: $primary; }

QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border; border-radius: 5px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: $text_dim; }
/* add-page / sub-page 不显式置透明，系统样式会补一块浅灰底 —— 就是右侧那条白竖条 */
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; border: none; background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border; border-radius: 5px; min-width: 24px; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QScrollBar::corner { background: transparent; border: none; }

QGroupBox {
    border: 1px solid $border; border-radius: ${radius}px;
    margin-top: 10px; padding: ${sp3}px;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: $text_dim; }

QPlainTextEdit#log {
    font-family: $mono; font-size: ${fs_small}px;
    background: $bg; border: 1px solid $border; border-radius: ${radius_sm}px;
}
QSplitter::handle { background: transparent; }
QToolTip {
    background: $panel; color: $text; border: 1px solid $border;
    border-radius: 6px; padding: 4px 6px;
}
"""


def _check_png(t: dict) -> str:
    """生成勾选用的对勾 PNG（QSS 不能画对勾，只能外挂图片）。

    失败时返回空串：QSS 里 `image: url()` 会被忽略，退化成"实心蓝方块"仍然可辨识。
    """
    try:
        path = os.path.join(tempfile.gettempdir(), "voice_assistant_gui")
        os.makedirs(path, exist_ok=True)
        f = os.path.join(path, "check_%s.png" % str(t["on_primary"]).strip("#"))
        if not os.path.exists(f):
            from .icons import pixmap
            if not pixmap("check", t["on_primary"], 12).save(f, "PNG"):
                return ""
        return f.replace("\\", "/")
    except Exception:              # noqa: BLE001 - 任何异常都退化为纯色
        return ""


def qss(t: dict) -> str:
    s = _QSS
    for k, v in t.items():
        s = s.replace("${%s}" % k, str(v)).replace("$%s" % k, str(v))
    png = _check_png(t)
    return s.replace("${check_png}", png)


def font(t: dict) -> QFont:
    f = QFont()
    f.setFamilies([x.strip().strip("'\"") for x in t["font"].split(",")])
    f.setPixelSize(int(t["fs_body"]))
    return f


def mono_font(t: dict) -> QFont:
    f = QFont()
    f.setFamilies([x.strip().strip("'\"") for x in t["mono"].split(",")])
    f.setPixelSize(int(t["fs_small"]))
    return f


def apply_theme(app, mode: str = "dark") -> dict:
    """应用主题并返回 token（页面保存一份用于绘制类组件）。"""
    global _current
    t = tokens(mode)
    _current = t
    app.setStyleSheet(qss(t))
    app.setFont(font(t))
    return t
