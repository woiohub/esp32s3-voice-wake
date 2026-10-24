"""内嵌 SVG 图标（不依赖网络与图标字体）。

用法：
    icon("chat", t["primary"], 20)      # 返回 QIcon
    pixmap("alert", t["error"], 16)     # 返回 QPixmap（供自绘使用）
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 24x24 视口的路径片段（stroke 风格）
_PATHS = {
    "chat": '<path d="M4 5h16v11H9l-5 4z"/>',
    "device": '<rect x="5" y="2" width="14" height="20" rx="3"/><path d="M9 18h6"/>',
    "model": ('<rect x="6" y="6" width="12" height="12" rx="2"/>'
              '<path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>'),
    "log": '<path d="M5 3h14v18H5z"/><path d="M8 8h8M8 12h8M8 16h5"/>',
    "gear": ('<circle cx="12" cy="12" r="3"/>'
             '<path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1'
             'M19.1 4.9L17 7M7 17l-2.1 2.1"/>'),
    "copy": '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5h10"/>',
    "replay": '<path d="M4 12a8 8 0 1 0 3-6.2"/><path d="M4 5v6h6"/>',
    "refresh": '<path d="M20 12a8 8 0 1 0-2.3 5.6"/><path d="M20 4v5h-5"/>',
    "folder": '<path d="M3 6h6l2 2h10v11H3z"/>',
    "eye": '<path d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/>',
    "eye_off": ('<path d="M3 3l18 18"/><path d="M10.6 6.2A9.7 9.7 0 0 1 12 6c6 0 10 6 10 6'
                'a17 17 0 0 1-3 3.6"/><path d="M6.3 6.9A17 17 0 0 0 2 12s4 6 10 6'
                'a9.5 9.5 0 0 0 4.2-1"/>'),
    "play": '<path d="M8 5l11 7-11 7z"/>',
    "send": '<path d="M4 12l16-8-6 16-3-7z"/>',
    "check": '<path d="M4 12l5 5L20 6"/>',
    "alert": '<path d="M12 3l9 17H3z"/><path d="M12 9v5M12 17h.01"/>',
    "wifi": ('<path d="M3 9a15 15 0 0 1 18 0"/><path d="M6.5 12.5a10 10 0 0 1 11 0"/>'
             '<path d="M10 16a5 5 0 0 1 4 0"/><circle cx="12" cy="19" r="1"/>'),
    "chevron_right": '<path d="M9 6l6 6-6 6"/>',
    "chevron_left": '<path d="M15 6l-6 6 6 6"/>',
    "power": '<path d="M12 3v9"/><path d="M6.5 6.5a8 8 0 1 0 11 0"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/>',
    "download": '<path d="M12 4v10"/><path d="M8 11l4 4 4-4"/><path d="M5 20h14"/>',
}

_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        'stroke="{color}" stroke-width="1.8" stroke-linecap="round" '
        'stroke-linejoin="round">{body}</svg>')


def _render(name: str, color: str, size: int) -> QPixmap:
    body = _PATHS.get(name)
    if body is None:
        body = _PATHS["alert"]
    svg = _SVG.format(color=color, body=body)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))

    pm = QPixmap(QSize(int(size), int(size)))
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    pm.setDevicePixelRatio(1.0)
    return pm


def pixmap(name: str, color: str = "#E6EAF2", size: int = 18) -> QPixmap:
    return _render(name, color, size)


def icon(name: str, color: str = "#E6EAF2", size: int = 18) -> QIcon:
    return QIcon(_render(name, color, size))
