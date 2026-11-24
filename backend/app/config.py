"""三级配置解析：代码默认 < 配置文件 < DB settings。

设计要点：
- 全平台单一实现，不按操作系统分支；
- 值全部为"参数/开关"，不做实现切换；
- effective 结果缓存在模块内，任一层的写操作后由调用方触发失效（set_* 会清缓存）；
- DB 层由 settings 服务在启动与写入后通过 `set_db_overrides()` 注入（避免 config 直接依赖 ORM）。
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_FILE_LAYER: dict[str, Any] = {}
_DB_LAYER: dict[str, Any] = {}
_EFFECTIVE: dict[str, Any] | None = None

# 环境变量层（优先级最高，运维/CI/测试隔离用）
ENV_MAP: dict[str, str] = {
    "AUTOTEST_DATA_DIR": "runtime.data_dir",
    "AUTOTEST_ARTIFACTS_DIR": "runtime.artifacts_dir",
    "AUTOTEST_ADB": "adb.path",
    "AUTOTEST_HOST": "server.host",
    "AUTOTEST_PORT": "server.port",
    "AUTOTEST_TOKEN": "security.token",
    "AUTOTEST_SCRCPY_JAR": "scrcpy.jar",
}

# ---------------------------------------------------------------- defaults
DEFAULTS: dict[str, Any] = {
    "adb.path": "",
    "adb.connect_timeout_s": 20,
    "runtime.data_dir": "",
    "runtime.frontend_dir": "",
    "runtime.artifacts_dir": "",  # 空 = data_dir/artifacts
    "engine.poll_interval_ms": 350,
    "engine.default_timeout_ms": 8000,
    "engine.max_total_ms": 1800_000,
    "engine.max_steps": 2000,
    "engine.step_min_timeout_ms": 100,
    "engine.step_max_timeout_ms": 600_000,
    "engine.pause_abandon_grace_s": 5,
    "scrcpy.jar": "",
    "scrcpy.max_size": 1600,
    "scrcpy.max_fps": 30,
    "scrcpy.video_bit_rate": 8_000_000,
    "scrcpy.crop": "",
    "recording.click_dump": True,
    "recording.confirm_before_save": True,
    "recording.ring_buffer_ms": 1500,
    "recording.hierarchy_refresh_ms": 500,
    "recording.target_mode": "point",
    "capture.logcat_auto_attach": False,
    "capture.logcat_max_lines": 5000,
    "capture.network.enabled": False,
    "capture.network.auto_attach": False,
    "capture.network.port": 8080,
    "retention.days": 30,
    "retention.report_exclude_video": True,
    "retention.report_max_mb": 500,
    "security.token": "",
    "server.host": "127.0.0.1",
    "server.port": 8787,
}

# 设置页展示白名单（GET /api/settings 渲染顺序）
SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {"key": "adb.path", "type": "string", "title": "adb 路径", "desc": "留空自动探测"},
    {
        "key": "adb.connect_timeout_s",
        "type": "int",
        "title": "ADB 连接超时(s)",
        "min": 1,
        "max": 120,
        "desc": "网络设备连接/重连的等待上限",
    },
    {"key": "runtime.data_dir", "type": "string", "title": "数据目录", "desc": "留空使用 ~/.autotest/data"},
    {"key": "engine.poll_interval_ms", "type": "int", "title": "轮询间隔(ms)", "min": 100, "max": 2000},
    {"key": "engine.default_timeout_ms", "type": "int", "title": "默认节点超时(ms)", "min": 500, "max": 600000},
    {"key": "engine.max_total_ms", "type": "int", "title": "单次运行总时长(ms)", "min": 60000},
    {"key": "engine.max_steps", "type": "int", "title": "步骤总预算", "min": 1, "max": 100000},
    {"key": "scrcpy.max_size", "type": "int", "title": "镜像最大边(px)", "min": 480, "max": 2560},
    {"key": "scrcpy.max_fps", "type": "int", "title": "镜像帧率", "min": 5, "max": 120},
    {"key": "scrcpy.video_bit_rate", "type": "int", "title": "镜像码率(bps)", "min": 1_000_000, "max": 32_000_000},
    {"key": "scrcpy.crop", "type": "string", "title": "裁剪 w:h:x:y", "desc": "留空不裁剪"},
    {
        "key": "scrcpy.jar",
        "type": "string",
        "title": "scrcpy-server jar 路径",
        "desc": "留空使用 vendor 内置版本",
    },
    {"key": "recording.click_dump", "type": "bool", "title": "点击前抓层级(录制更准)"},
    {"key": "recording.confirm_before_save", "type": "bool", "title": "录后确认清单"},
    {
        "key": "recording.target_mode",
        "type": "enum",
        "title": "录制转脚本定位方式",
        "options": [
            {"label": "绝对坐标（推荐：游戏/自绘界面唯一可靠）", "value": "point"},
            {"label": "元素定位（文本/资源 id，失败退回坐标）", "value": "element"},
        ],
        "desc": "默认 point：只记坐标，最稳；element：优先元素，界面稳定时更耐分辨率变化",
    },
    {"key": "capture.logcat_auto_attach", "type": "bool", "title": "运行自动采集 logcat"},
    {
        "key": "capture.network.enabled",
        "type": "bool",
        "title": "网络域名抓包",
        "desc": "只记录 host/port，不抓 Header 与正文；需要设备系统代理",
    },
    {
        "key": "capture.network.auto_attach",
        "type": "bool",
        "title": "运行自动开启域名抓包",
        "desc": "随脚本运行开始/停止；手动抓包不会被运行结束误停",
    },
    {
        "key": "capture.network.port",
        "type": "int",
        "title": "网络抓包端口",
        "min": 1024,
        "max": 65535,
        "desc": "本机代理监听端口；被占用时改这里",
    },
    {"key": "retention.days", "type": "int", "title": "保留天数", "min": 1, "max": 3650},
    {"key": "retention.report_exclude_video", "type": "bool", "title": "报告排除视频"},
    {"key": "retention.report_max_mb", "type": "int", "title": "报告体积上限(MB)", "min": 1, "max": 4096},
    {"key": "security.token", "type": "string", "title": "访问令牌", "desc": "留空不校验(仅本机)"},
]


def home_dir() -> Path:
    return Path.home() / ".autotest"


def config_file_path() -> Path:
    env = os.getenv("AUTOTEST_CONFIG")
    if env:
        return Path(env).expanduser()
    return home_dir() / "config.json"


def platform_name() -> str:
    return platform.system().lower()  # darwin / windows / linux


# ---------------------------------------------------------------- layers
def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in data.items():
        full = f"{prefix}{key}"
        if isinstance(value, dict) and value:
            out.update(_flatten(value, f"{full}."))
        else:
            out[full] = value
    return out


def load_file_layer(path: Path | None = None) -> dict[str, Any]:
    p = path or config_file_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return _flatten(raw)


def _coerce(key: str, value: Any) -> Any:
    default = DEFAULTS.get(key, None)
    if value is None:
        return default
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, str):
        return str(value)
    return value


def set_db_overrides(values: dict[str, Any]) -> None:
    with _LOCK:
        global _DB_LAYER, _EFFECTIVE
        _DB_LAYER = {k: v for k, v in values.items() if v is not None}
        _EFFECTIVE = None


def set_file_layer(values: dict[str, Any]) -> None:
    with _LOCK:
        global _FILE_LAYER, _EFFECTIVE
        _FILE_LAYER = dict(values)
        _EFFECTIVE = None


def reload_file_layer() -> None:
    set_file_layer(load_file_layer())


def _env_layer() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for env_key, cfg_key in ENV_MAP.items():
        raw = os.getenv(env_key)
        if raw is not None and raw != "":
            out[cfg_key] = raw
    return out


def effective() -> dict[str, Any]:
    with _LOCK:
        global _EFFECTIVE
        if _EFFECTIVE is None:
            merged: dict[str, Any] = dict(DEFAULTS)
            # 优先级：defaults < file < db < env
            for layer in (_FILE_LAYER, _DB_LAYER, _env_layer()):
                for key, value in layer.items():
                    merged[key] = _coerce(key, value) if key in DEFAULTS else value
            _EFFECTIVE = merged
        return dict(_EFFECTIVE)


def get(key: str, default: Any = None) -> Any:
    return effective().get(key, DEFAULTS.get(key, default))


def source_of(key: str) -> str:
    if key in _env_layer():
        return "env"
    if key in _DB_LAYER:
        return "db"
    if key in _FILE_LAYER:
        return "file"
    return "default"


def sources() -> dict[str, str]:
    return {key: source_of(key) for key in DEFAULTS}


def reset_to_defaults() -> None:
    with _LOCK:
        global _FILE_LAYER, _DB_LAYER, _EFFECTIVE
        _FILE_LAYER = {}
        _DB_LAYER = {}
        _EFFECTIVE = None


# ---------------------------------------------------------------- paths
def _expand(path_value: str, fallback: Path) -> Path:
    if not path_value:
        return fallback
    return Path(path_value).expanduser()


def data_dir() -> Path:
    path = _expand(str(get("runtime.data_dir") or ""), home_dir() / "data")
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifacts_dir() -> Path:
    path = _expand(str(get("runtime.artifacts_dir") or ""), data_dir() / "artifacts")
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "autotest.db"


def reports_dir() -> Path:
    path = data_dir() / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def artifact_url(path: str | Path) -> str | None:
    """产物直链：/artifacts/<相对 artifacts_dir 的路径>（main.py 已挂载静态目录）。"""
    try:
        root = artifacts_dir().resolve()
        rel = Path(path).resolve().relative_to(root)
    except (ValueError, OSError):
        return None
    return f"/artifacts/{rel.as_posix()}"


def static_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "static"


def vendor_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "vendor"


def ensure_dirs() -> None:
    data_dir()
    artifacts_dir()
    reports_dir()


# ---------------------------------------------------------------- adb discovery
def find_adb() -> str:
    configured = str(get("adb.path") or "")
    if configured and Path(configured).exists():
        return configured
    found = shutil.which("adb")
    if found:
        return found
    candidates: list[Path] = []
    system = platform_name()
    if system == "darwin":
        candidates.append(Path.home() / "Library/Android/sdk/platform-tools/adb")
    elif system == "windows":
        local = os.getenv("LOCALAPPDATA") or ""
        if local:
            candidates.append(Path(local) / "Android/Sdk/platform-tools/adb.exe")
        candidates.append(Path("C:/Android/platform-tools/adb.exe"))
    else:
        candidates.append(Path.home() / "Android/Sdk/platform-tools/adb")
        candidates.append(Path("/usr/lib/android-sdk/platform-tools/adb"))
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "adb"


def env_summary() -> dict[str, Any]:
    return {
        "platform": platform_name(),
        "python": platform.python_version(),
        "data_dir": str(data_dir()),
        "artifacts_dir": str(artifacts_dir()),
        "db_path": str(db_path()),
        "config_file": str(config_file_path()),
        "adb": find_adb(),
    }
