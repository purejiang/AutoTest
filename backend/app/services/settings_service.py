"""设置服务（契约 §7.6 / §11）：DB 层读写 + 四层配置合并（default < file < db < env）。

分层规则：
- runtime 项（行为/偏好）：允许写 settings 表，DB 优先级高于文件（用户显式操作应生效）；
- bootstrap 项（启动项：数据目录/端口/host 等）：**不进 DB**，只支持 文件/环境变量；
  设置页只读展示，API 写入直接 INVALID_ARGUMENT，避免「数据库不在配置值指向的目录」的分裂状态。

- 只有本服务写 DB settings 表；写完立即 `config.set_db_overrides()` 重载，保证 effective 实时；
- 值校验按 `config.SETTINGS_SCHEMA`（type/min/max），未知 key 直接 INVALID_ARGUMENT；
- `PUT /api/settings {values}` 中 value=None 表示删除该 key 的 DB 覆盖。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config, db
from ..errors import ApiError
from ..logs import logger
from ..models import Setting, now_iso

SCRCPY_SERVER_VERSION = "5.0.1"
_SETTINGS_SCHEMA_BY_KEY: dict[str, dict[str, Any]] = {item["key"]: item for item in config.SETTINGS_SCHEMA}
_TRUE_STRINGS = {"1", "true", "yes", "on"}
_FALSE_STRINGS = {"0", "false", "no", "off", ""}


def _type_of(key: str) -> str:
    entry = _SETTINGS_SCHEMA_BY_KEY.get(key)
    if entry and entry.get("type"):
        return str(entry["type"])
    default = config.DEFAULTS.get(key)
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    return "string"


def _coerce_value(key: str, value: Any) -> Any:
    """按 schema 校验并转换；非法时 raise ValueError（消息给用户看）。"""
    kind = _type_of(key)
    entry = _SETTINGS_SCHEMA_BY_KEY.get(key) or {}
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in _TRUE_STRINGS:
                return True
            if text in _FALSE_STRINGS:
                return False
        raise ValueError("必须是布尔值")
    if kind == "int":
        if isinstance(value, bool):
            return int(value)
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("必须是整数") from None
        low, high = entry.get("min"), entry.get("max")
        if low is not None and number < int(low):
            raise ValueError(f"不能小于 {low}")
        if high is not None and number > int(high):
            raise ValueError(f"不能大于 {high}")
        return number
    if isinstance(value, (dict, list, tuple)):
        raise ValueError("必须是字符串")
    return str(value)


# ---------------------------------------------------------------- DB 层 <-> config
def _old_bool(row: Setting | None) -> bool:
    if row is None:
        return False
    value = row.value
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUE_STRINGS
    return bool(value)


def _migrate_network_mode(session: Session) -> None:
    """把旧的 enabled + auto_attach 两个开关迁移成单一下拉 mode。

    enabled=false -> off（删除旧行即可，默认值就是 off）
    enabled=true + auto_attach=false -> manual
    enabled=true + auto_attach=true  -> run
    已有 mode 时只清掉旧行，不覆盖新值。
    """
    mode_row = session.get(Setting, "capture.network.mode")
    enabled_row = session.get(Setting, "capture.network.enabled")
    auto_row = session.get(Setting, "capture.network.auto_attach")
    if enabled_row is None and auto_row is None:
        return
    if mode_row is None and _old_bool(enabled_row):
        value = "run" if _old_bool(auto_row) else "manual"
        session.add(Setting(key="capture.network.mode", value=value, updated_at=now_iso()))
    for row in (enabled_row, auto_row):
        if row is not None:
            session.delete(row)
    session.flush()


def load_into_config(session: Session | None = None) -> dict[str, Any]:
    """读 settings 表 -> config.set_db_overrides()。启动时与每次写入后调用。

    bootstrap 项即使旧库里还有行也不会进入 effective（只告警一次），
    因为它们在打开数据库之前就已确定，DB 覆盖会造成目录/端口分裂。
    """
    if session is not None:
        _migrate_network_mode(session)
        rows = session.execute(select(Setting)).scalars().all()
    else:
        with db.session_scope() as own:
            _migrate_network_mode(own)
            rows = own.execute(select(Setting)).scalars().all()
    values: dict[str, Any] = {}
    ignored: list[tuple[str, Any]] = []
    for row in rows:
        if row.key not in config.DEFAULTS or row.value is None:
            continue
        if row.key in config.BOOTSTRAP_KEYS:
            ignored.append((row.key, row.value))
            continue
        values[row.key] = row.value
    if ignored:
        logger.warning(
            "settings 表里有启动项覆盖，已忽略（启动项只支持配置文件/环境变量，见 docs/contracts.md §11）：%s",
            ", ".join(f"{key}={value!r}" for key, value in ignored),
        )
    config.set_db_overrides(values)
    return values


def _db_values(session: Session) -> dict[str, Any]:
    rows = session.execute(select(Setting)).scalars().all()
    return {row.key: row.value for row in rows}


def _upsert(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    stamp = now_iso()
    if row is None:
        session.add(Setting(key=key, value=value, updated_at=stamp))
    else:
        row.value = value
        row.updated_at = stamp


def _delete_rows(session: Session, keys: list[str]) -> None:
    if not keys:
        return
    for key in keys:
        row = session.get(Setting, key)
        if row is not None:
            session.delete(row)


# ---------------------------------------------------------------- 对外 API
def response() -> dict[str, Any]:
    """GET /api/settings 响应：{effective, sources, schema}。

    schema 每项额外带 `default`（代码默认值）、`layer`（bootstrap/runtime）、
    `writable`（是否可写数据库）、`env`（对应环境变量名，供设置页提示）；
    这些是 additive 字段，不影响已有消费方。
    """
    schema = [
        {
            **entry,
            "default": config.DEFAULTS.get(entry["key"]),
            "layer": config.layer_of(entry["key"]),
            "writable": config.db_writable(entry["key"]),
            "env": config.env_var_for(entry["key"]),
        }
        for entry in config.SETTINGS_SCHEMA
    ]
    return {
        "effective": config.effective(),
        "sources": config.sources(),
        "schema": schema,
        "config_file": str(config.config_file_path()),
    }


def _ensure_runtime_key(key: str) -> None:
    """启动项不允许写数据库：给出明确的替代路径（配置文件/环境变量 + 重启）。"""
    if key not in config.BOOTSTRAP_KEYS:
        return
    env_name = config.env_var_for(key)
    hint = f"改 {config.config_file_path()}"
    if env_name:
        hint += f" 或设置环境变量 {env_name}"
    raise ApiError(
        "INVALID_ARGUMENT",
        f"{key} 是启动项（打开数据库前就要确定），不能存数据库；{hint}，然后重启 AutoTest",
        {
            "key": key,
            "layer": "bootstrap",
            "config_file": str(config.config_file_path()),
            "env": env_name,
        },
    )


def update(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    """PUT /api/settings：value=None -> 删除该层覆盖；校验失败 -> VALIDATION_ERROR。"""
    if not isinstance(values, dict):
        raise ApiError("INVALID_ARGUMENT", "values must be an object", {"values": type(values).__name__})
    coerced: dict[str, Any] = {}
    deletes: list[str] = []
    errors: list[dict[str, Any]] = []
    for key, value in values.items():
        if key not in config.DEFAULTS:
            raise ApiError("INVALID_ARGUMENT", f"unknown setting: {key}", {"key": key})
        _ensure_runtime_key(key)
        if value is None:
            deletes.append(key)
            continue
        try:
            coerced[key] = _coerce_value(key, value)
        except ValueError as exc:
            errors.append({"path": key, "code": "invalid_value", "message": str(exc)})
    if errors:
        raise ApiError("VALIDATION_ERROR", "invalid setting value", {"errors": errors})
    existing = _db_values(session)
    for key, value in coerced.items():
        _upsert(session, key, value)
    _delete_rows(session, [key for key in deletes if key in existing])
    session.flush()
    load_into_config(session)
    return response()


def delete(session: Session, key: str) -> dict[str, Any]:
    """DELETE /api/settings/{key}：只删 DB 层覆盖（file/default 生效）。"""
    if key not in config.DEFAULTS:
        raise ApiError("INVALID_ARGUMENT", f"unknown setting: {key}", {"key": key})
    _ensure_runtime_key(key)
    _delete_rows(session, [key])
    session.flush()
    load_into_config(session)
    return response()


def reset(session: Session) -> dict[str, Any]:
    """POST /api/settings/reset：清空 DB 层（file 层仍生效）。"""
    rows = session.execute(select(Setting)).scalars().all()
    for row in rows:
        session.delete(row)
    session.flush()
    load_into_config(session)
    return response()


def scrcpy_options() -> dict[str, Any]:
    """GET /api/settings/scrcpy/options：镜像参数白名单 + 当前值（渲染表单用）。"""
    items: list[dict[str, Any]] = []
    for key in config.DEFAULTS:
        if not key.startswith("scrcpy."):
            continue
        entry = dict(_SETTINGS_SCHEMA_BY_KEY.get(key) or {})
        entry.setdefault("key", key)
        entry.setdefault("type", _type_of(key))
        entry.setdefault("title", key)
        entry["value"] = config.get(key)
        entry["source"] = config.source_of(key)
        items.append(entry)
    jar = config.vendor_dir() / f"scrcpy-server-v{SCRCPY_SERVER_VERSION}.jar"
    return {
        "server_version": SCRCPY_SERVER_VERSION,
        "codec": "h264",
        "jar": str(jar),
        "jar_present": jar.exists(),
        "items": items,
    }


__all__ = [
    "SCRCPY_SERVER_VERSION",
    "delete",
    "load_into_config",
    "reset",
    "response",
    "scrcpy_options",
    "update",
]
