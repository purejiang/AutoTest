"""日志：控制台（import 即生效）+ 文件轮转（启动时按生效的数据目录挂载）。

文件 handler 必须等 `config.reload_file_layer()` 之后才能确定路径——否则会用
"代码默认数据目录"算日志路径，配置文件里指定的 data_dir 反而写不进去。
所以：
- import 时只挂控制台，任何模块都能 `from .logs import logger`；
- `create_app()` 载入配置文件层后再调 `configure_file_logging()` 挂文件 handler；
- 测试用 `disable_file_logging()` 关掉文件写入，避免污染真实数据目录。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config

_LOGGER_NAME = "autotest"
_CONFIGURED = False
_FORMATTER = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
_FILE_HANDLER: RotatingFileHandler | None = None
_FILE_ENABLED = True


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(_FORMATTER)
    logger.addHandler(stream)
    logger.propagate = False
    _CONFIGURED = True


def get_logger() -> logging.Logger:
    _configure()
    return logging.getLogger(_LOGGER_NAME)


def disable_file_logging() -> None:
    """测试/嵌入场景：不写日志文件（可重复调用）。"""
    global _FILE_ENABLED
    _FILE_ENABLED = False


def configure_file_logging() -> None:
    """启动时调用（配置文件层已加载）：日志落到 `<data_dir>/autotest.log`（5MB × 4 轮转）。"""
    global _FILE_HANDLER
    if _FILE_HANDLER is not None or not _FILE_ENABLED:
        return
    log = get_logger()
    try:
        path = config.data_dir() / "autotest.log"
        handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(_FORMATTER)
        log.addHandler(handler)
    except OSError:  # 数据目录不可写时只打控制台
        return
    _FILE_HANDLER = handler


logger = get_logger()
