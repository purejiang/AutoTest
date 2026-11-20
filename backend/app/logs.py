"""日志：控制台 + 单文件轮转，全后端统一 from .logs import logger。"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from . import config

_LOGGER_NAME = "autotest"
_CONFIGURED = False


def _configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    try:
        path = config.data_dir() / "autotest.log"
        file_handler = RotatingFileHandler(path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:  # 数据目录不可写时只打控制台
        pass

    logger.propagate = False
    _CONFIGURED = True


def get_logger() -> logging.Logger:
    _configure()
    return logging.getLogger(_LOGGER_NAME)


logger = get_logger()
