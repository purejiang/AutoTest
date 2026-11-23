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

from sqlalchemy import create_engine, event, inspect
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


class IncompatibleDatabaseError(RuntimeError):
    """旧库 schema 与 v0.2 不兼容：明确报错，绝不自动修改/删除用户数据。"""


def _database_path() -> Path:
    return _OVERRIDE_PATH or config.db_path()


def _assert_v02_schema(engine: Engine) -> None:
    """schema guard：v0.2 新库必须带 scripts.origin_recording_id。

    create_all 只会建缺失的表，不会给旧表加列；如果这里发现旧库，直接抛出可操作的
    异常，由用户自行备份并移走/删除旧库，绝不自动 ALTER / 回填 / 删数据。
    """
    inspector = inspect(engine)
    if "scripts" not in inspector.get_table_names():
        return
    columns = {str(column["name"]) for column in inspector.get_columns("scripts")}
    if "origin_recording_id" in columns:
        return
    path = _database_path()
    raise IncompatibleDatabaseError(
        "数据库 schema 与 AutoTest v0.2 不兼容：scripts 表缺少 origin_recording_id 列。"
        "v0.2 不支持 v0.1.x 的旧数据库，也不会自动修改或删除任何数据。"
        f"请先备份 {path}，然后把 ~/.autotest/data 下的旧数据库（或你配置的数据目录里的旧库）"
        "移走或删除，再重新启动 AutoTest。"
    )


def init_db() -> None:
    """建表（幂等）+ v0.2 schema guard。启动时调用一次。"""
    from . import models  # noqa: F401  注册 metadata

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _assert_v02_schema(engine)


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
