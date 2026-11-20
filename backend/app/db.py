"""SQLite 引擎与会话。

约定（全后端统一）：
- 短事务：拿 session → 做事 → commit，绝不跨网络/设备操作持有事务；
- WAL + busy_timeout=5000，写并发靠短事务而非长锁；
- `session_scope()` 是推荐的获取方式（自动 commit/rollback/close）；
- 测试用 `reset_engine_for_tests(tmp_path/"t.db")` 切换独立 DB。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config

_ENGINE: Engine | None = None
_SESSION_FACTORY: sessionmaker[Session] | None = None
_OVERRIDE_PATH: Path | None = None


class Base(DeclarativeBase):
    pass


def _make_engine(path: Path) -> Engine:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 5.0},
        pool_pre_ping=True,
        future=True,
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - 取决驱动
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def get_engine() -> Engine:
    global _ENGINE, _SESSION_FACTORY
    if _ENGINE is None:
        path = _OVERRIDE_PATH or config.db_path()
        _ENGINE = _make_engine(path)
        _SESSION_FACTORY = sessionmaker(bind=_ENGINE, expire_on_commit=False, future=True)
    return _ENGINE


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SESSION_FACTORY is not None
    return _SESSION_FACTORY


def new_session() -> Session:
    return get_session_factory()()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """建表（幂等）。启动时调用一次。"""
    from . import models  # noqa: F401  注册 metadata

    Base.metadata.create_all(bind=get_engine())


def reset_engine_for_tests(db_file: Path) -> None:
    """测试用：切换 DB 文件（会清空现有引擎并重建表）。"""
    global _ENGINE, _SESSION_FACTORY, _OVERRIDE_PATH
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None
    _SESSION_FACTORY = None
    _OVERRIDE_PATH = db_file
    get_engine()
    init_db()
