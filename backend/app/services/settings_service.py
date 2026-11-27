"""设置服务（契约 §7.6 / §11）：配置文件读写（default < file < env），不再用数据库存配置。

- 设置页保存 = 原子写 `config.json` + 立即 reload，所以保存后照样「即时生效」；
- 手改配置文件后可以调 `POST /api/settings/reload` 重载，或重启；
- 启动项（BOOTSTRAP_KEYS：data_dir / artifacts_dir / frontend_dir / host / port）不能通过 API 写：
  它们在启动时被冻结，改了必须重启（config.reload_file_layer() 会忽略并告警）；
- 旧版本存在 `settings` 表里的配置由 `migrate_db_settings_to_file()` 一次性迁到文件。
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
    if kind == "enum":
        allowed = [str(option.get("value")) for option in entry.get("options") or []]
        text = str(value)
        if allowed and text not in allowed:
            raise ValueError(f"可选值：{'、'.join(allowed)}")
        return text
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
    """把旧的三态网络抓包设置收敛成单一 bool 开关。

    - 旧 ``enabled=true``（无论 auto_attach）-> ``capture.network.enabled=true``；
    - 旧 ``mode``：off -> false，manual/run -> true（新键已存在时以新键为准）；
    - 删除 ``auto_attach`` / ``mode``，不再保留“手动/自动”语义。
    """
    enabled_row = session.get(Setting, "capture.network.enabled")
    mode_row = session.get(Setting, "capture.network.mode")
    auto_row = session.get(Setting, "capture.network.auto_attach")
    if enabled_row is None and mode_row is None and auto_row is None:
        return
    if enabled_row is None:
        if mode_row is not None:
            value = str(mode_row.value or "off").strip().lower() != "off"
        else:
            value = _old_bool(auto_row)
        session.add(
            Setting(key="capture.network.enabled", value=value, updated_at=now_iso())
        )
    for row in (mode_row, auto_row):
        if row is not None:
            session.delete(row)
    session.flush()


def migrate_db_settings_to_file() -> dict[str, Any]:
    """一次性迁移：旧版把配置存在 settings 表，v0.2.8 起配置只认 config.json。

    - 旧 DB 行是用户显式保存过的（曾经优先级最高），迁移时写入文件、保持生效值不变；
    - 启动项行不迁移（它们本来就只支持文件/环境变量），只告警；
    - 写文件成功后才删除行；失败则保留，下次启动重试，绝不让生效值丢失。
    """
    with db.session_scope() as session:
        _migrate_network_mode(session)
        rows = session.execute(select(Setting)).scalars().all()
        if not rows:
            return {}
        updates: dict[str, Any] = {}
        ignored: list[str] = []
        for row in rows:
            if row.value is None or row.key not in config.DEFAULTS:
                continue
            if row.key in config.BOOTSTRAP_KEYS:
                ignored.append(f"{row.key}={row.value!r}")
                continue
            updates[row.key] = row.value
        if updates:
            try:
                config.write_file_values(updates)
            except (OSError, ValueError) as exc:
                logger.error(
                    "旧 DB 配置迁移到 %s 失败（settings 表保留，下次启动重试）：%s",
                    config.config_file_path(),
                    exc,
                )
                return {}
        for row in rows:
            session.delete(row)
        session.flush()
    logger.warning(
        "已把 %d 条 DB 设置迁移到 %s 并清空 settings 表（v0.2.8 起配置只存文件）%s",
        len(updates),
        config.config_file_path(),
        f"；忽略启动项：{', '.join(ignored)}" if ignored else "",
    )
    return updates


def response() -> dict[str, Any]:
    """GET /api/settings 响应：{effective, sources, schema, config_file}。

    schema 每项额外带 `default`（代码默认值）、`layer`（bootstrap/runtime）、
    `writable`（能否通过设置页保存）、`env`（对应环境变量名）；都是 additive 字段。
    """
    schema = [
        {
            **entry,
            "default": config.DEFAULTS.get(entry["key"]),
            "default_display": config.default_display(entry["key"]),
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
    """启动项不能通过设置页（写文件 + reload）改：只能手改配置文件/环境变量后重启。"""
    if key not in config.BOOTSTRAP_KEYS:
        return
    env_name = config.env_var_for(key)
    hint = f"改 {config.config_file_path()}"
    if env_name:
        hint += f" 或设置环境变量 {env_name}"
    raise ApiError(
        "INVALID_ARGUMENT",
        f"{key} 是启动项（打开数据库前就要确定，运行中切换会造成目录/端口分裂）；{hint}，然后重启 AutoTest",
        {
            "key": key,
            "layer": "bootstrap",
            "config_file": str(config.config_file_path()),
            "env": env_name,
        },
    )


def update(session: Session, values: dict[str, Any]) -> dict[str, Any]:
    """PUT /api/settings：写配置文件；value=None -> 从文件删除该项（回到默认/环境变量）。"""
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
    changes: dict[str, Any] = {**coerced, **{key: None for key in deletes}}
    # 纯删除且配置文件不存在时无需创建空文件
    if changes and (coerced or config.config_file_path().exists()):
        _write_config(changes)
    return response()


def delete(session: Session, key: str) -> dict[str, Any]:
    """DELETE /api/settings/{key}：把该项从配置文件里移除（回到默认/环境变量）。"""
    if key not in config.DEFAULTS:
        raise ApiError("INVALID_ARGUMENT", f"unknown setting: {key}", {"key": key})
    _ensure_runtime_key(key)
    if config.config_file_path().exists():
        _write_config({key: None})
    return response()


def reset(session: Session) -> dict[str, Any]:
    """POST /api/settings/reset：移除配置文件里所有由设置页管理的项（回到代码默认）。"""
    current = config.load_file_layer()
    updates = {
        key: None
        for key in current
        if key in config.DEFAULTS and key not in config.BOOTSTRAP_KEYS
    }
    if updates:
        _write_config(updates)
    return response()


def reload() -> dict[str, Any]:
    """POST /api/settings/reload：重新读取配置文件（手改 config.json 后不用重启）。

    启动项（数据目录/端口）运行中不可切换：若有改动，会出现在返回的 restart_required 里。
    """
    config.reload_file_layer()
    body = response()
    body["restart_required"] = config.bootstrap_pending_changes()
    return body


def _write_config(changes: dict[str, Any]) -> None:
    try:
        config.write_file_values(changes)
    except ValueError as exc:
        raise ApiError(
            "INVALID_ARGUMENT",
            str(exc),
            {"config_file": str(config.config_file_path())},
        ) from exc
    except OSError as exc:
        raise ApiError(
            "INVALID_ARGUMENT",
            f"写入配置文件失败：{exc}",
            {"config_file": str(config.config_file_path())},
        ) from exc


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
    "migrate_db_settings_to_file",
    "reload",
    "reset",
    "response",
    "scrcpy_options",
    "update",
]
