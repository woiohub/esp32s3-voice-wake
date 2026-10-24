"""统一日志：模块标识 + 时间戳 + 级别 + 可选文件落盘。

用法：
    from voice_assistant.log import get_logger
    log = get_logger(__name__)   # 模块名即标识，与下位机 TAG 对应
"""
from __future__ import annotations

import logging
import logging.handlers
import os
from typing import Optional

# 统一格式：时间戳 + 模块名 + 级别
_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def init_logging(level: int = logging.INFO, log_dir: Optional[str] = None) -> None:
    """初始化根日志（幂等：已初始化则跳过）。"""
    root = logging.getLogger()
    if root.handlers:
        return

    root.setLevel(level)
    formatter = logging.Formatter(_FORMAT)

    # 控制台输出
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    # 可选：滚动文件落盘（便于长期问题定位）
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "voice_assistant.log")
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
