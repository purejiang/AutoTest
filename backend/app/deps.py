"""依赖注入（契约 §0/§14.2）。

- `get_db()`：请求级 SQLAlchemy session，来自 `db.session_scope()`（自动 commit/rollback/close）；
- `get_driver_or_503(serial)`：按 serial（路径参数）取 Driver，失败统一 DEVICE_UNAVAILABLE；
  与 device_registry 的缓存/占用表配合，端点里不要自己造 Driver。
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from . import db
from .android.driver import Driver
from .errors import ApiError, device_unavailable
from .logs import logger
from .services import device_registry


def get_db() -> Iterator[Session]:
    """请求级 session：短事务，依赖结束时提交（异常回滚）。"""
    with db.session_scope() as session:
        yield session


def get_driver_or_503(serial: str = "auto") -> Driver:
    """取 Driver；无设备/驱动层未就绪 -> ApiError(DEVICE_UNAVAILABLE, 503)。

    声明为 `serial: str` 时 FastAPI 会把同名路径参数注入进来。
    """
    try:
        return device_registry.get_driver(serial)
    except ApiError:
        raise
    except Exception as exc:  # 驱动层未预期错误 -> 设备不可用（不暴露 500）
        logger.exception("failed to get driver for serial=%s", serial)
        raise device_unavailable(f"device {serial} unavailable: {exc}", {"serial": serial}) from exc


__all__ = ["get_db", "get_driver_or_503"]
