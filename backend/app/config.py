"""配置解析：代码默认 < 配置文件 < 环境变量（文件优先，DB 只存运行数据）。

设计要点：
- 全平台单一实现，不按操作系统分支；
- 值全部为"参数/开关"，不做实现切换；
- effective 结果缓存在模块内，写配置文件（write_file_values）或 reload 后失效重算；
- 不存数据库：配置是"人写、要 diff/备份/进 git"的东西，写进 settings 表会变成黑盒；
- 启动项（BOOTSTRAP_KEYS，如数据目录/端口）本就只能在打开数据库前确定，天然属于文件/环境变量；
  它们在首次载入时被冻结，运行中 reload 文件不会改变它们（改了要重启），避免 DB/产物目录分裂。
"""

from __future__ import annotations

import json
import logging
import os
import platform
import shutil
import threading
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_FILE_LAYER: dict[str, Any] = {}
_EFFECTIVE: dict[str, Any] | None = None
# 启动项在「首次载入」时冻结：(值, 来源)。运行中 reload 不改它们，改了也等重启。
_BOOTSTRAP_LOCKED: dict[str, tuple[Any, str]] | None = None

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

# 启动项（bootstrap）：在「打开数据库之前」就必须确定的配置。
# 它们只能来自 代码默认 < 配置文件 < 环境变量，**永远不写进 settings 表**：
# 数据目录就藏着数据库文件本身，把它存进数据库会出现"数据库不在配置值指向的目录里"
# 的自相矛盾状态（DB 在旧目录、artifacts 在新目录），且重启也不会自愈。
BOOTSTRAP_KEYS: frozenset[str] = frozenset(
    {
        "runtime.data_dir",
        "runtime.artifacts_dir",
        "runtime.frontend_dir",
        "server.host",
        "server.port",
    }
)

_ENV_BY_KEY: dict[str, str] = {cfg_key: env_key for env_key, cfg_key in ENV_MAP.items()}


def layer_of(key: str) -> str:
    """配置分层：bootstrap = 只走文件/环境变量；runtime = 允许设置页写数据库。"""
    return "bootstrap" if key in BOOTSTRAP_KEYS else "runtime"


def env_var_for(key: str) -> str | None:
    """该 key 对应的环境变量名（没有映射则为 None），用于设置页提示。"""
    return _ENV_BY_KEY.get(key)


def db_writable(key: str) -> bool:
    return key in DEFAULTS and key not in BOOTSTRAP_KEYS

# ---------------------------------------------------------------- defaults
DEFAULTS: dict[str, Any] = {
    "adb.path": "",
    "adb.connect_timeout_s": 20,
    "runtime.data_dir": "",
    "runtime.frontend_dir": "",
    "runtime.artifacts_dir": "",  # 空 = data_dir/artifacts
    "engine.poll_interval_ms": 350,
    "engine.default_timeout_ms": 8000,
    # 新建脚本的默认停顿初值：只在 create_script 时写入脚本 settings，运行时不兜底
    # （录制转换/复制的脚本保留自己的值，回放节奏不受影响）。
    "engine.default_delay_before_ms": 0,
    "engine.default_delay_after_ms": 0,
    "engine.max_total_ms": 1800_000,
    "engine.max_steps": 2000,
    "engine.step_min_timeout_ms": 100,
    "engine.step_max_timeout_ms": 600_000,
    "engine.pause_abandon_grace_s": 5,
    "scrcpy.jar": "",
    "scrcpy.scale": 85,
    "scrcpy.max_size": 4096,
    # 0 = 不限（跟随设备刷新率），与 scrcpy 的 --max-fps 默认值一致：
    # 限帧是“为了省带宽/省解码”才需要的动作，不该是默认。性能不足时在设置页下调。
    "scrcpy.max_fps": 0,
    "scrcpy.video_bit_rate": 10_000_000,
    "scrcpy.crop": "",
    "recording.click_dump": True,
    "recording.confirm_before_save": True,
    "recording.ring_buffer_ms": 1500,
    "recording.hierarchy_refresh_ms": 500,
    "recording.target_mode": "point",
    "capture.logcat_auto_attach": False,
    "capture.logcat_max_lines": 5000,
    "capture.network.mode": "off",
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
    {"key": "adb.path", "type": "string", "title": "ADB 可执行文件", "desc": "留空自动探测系统 adb"},
    {
        "key": "adb.connect_timeout_s",
        "type": "int",
        "title": "ADB 连接超时（秒）",
        "min": 1,
        "max": 120,
        "desc": "连接或重连网络设备的等待上限",
    },
    {
        "key": "runtime.data_dir",
        "type": "string",
        "title": "数据目录",
        "desc": "启动项：留空使用 ~/.autotest/data；要改请写 ~/.autotest/config.json 或 AUTOTEST_DATA_DIR，然后重启",
    },
    {
        "key": "engine.poll_interval_ms",
        "type": "int",
        "title": "轮询间隔（ms）",
        "min": 100,
        "max": 2000,
        "desc": "等待与暂停检查的基础间隔",
    },
    {
        "key": "engine.default_timeout_ms",
        "type": "int",
        "title": "默认节点超时（ms）",
        "min": 500,
        "max": 600000,
        "desc": "节点未单独设置超时时使用",
    },
    {
        "key": "engine.default_delay_before_ms",
        "type": "int",
        "title": "新建脚本默认执行前停顿（ms）",
        "min": 0,
        "max": 600000,
        "desc": "只作为新建脚本的初值；节点停顿填 0 时跟随脚本默认，录制脚本不受影响",
    },
    {
        "key": "engine.default_delay_after_ms",
        "type": "int",
        "title": "新建脚本默认执行后停顿（ms）",
        "min": 0,
        "max": 600000,
        "desc": "只作为新建脚本的初值；节点停顿填 0 时跟随脚本默认，录制脚本不受影响",
    },
    {
        "key": "engine.max_total_ms",
        "type": "int",
        "title": "单次运行总时长（ms）",
        "min": 60000,
        "desc": "按活跃时间计，暂停不计入",
    },
    {
        "key": "engine.max_steps",
        "type": "int",
        "title": "步骤总预算",
        "min": 1,
        "max": 100000,
        "desc": "包含重试与 goto 重入",
    },
    {
        "key": "scrcpy.scale",
        "type": "int",
        "title": "按设备长边缩放（%）",
        "min": 50,
        "max": 100,
        "desc": "按设备长边百分比缩放；实际分辨率启动时计算，不放大",
    },
    {
        "key": "scrcpy.max_size",
        "type": "int",
        "title": "镜像长边上限（px）",
        "min": 480,
        "max": 4096,
        "desc": "缩放后的长边上限，越高越清晰、越费带宽",
    },
    {
        "key": "scrcpy.max_fps",
        "type": "int",
        "title": "镜像帧率（fps）",
        "min": 0,
        "max": 240,
        "desc": "0 = 不限（跟随设备刷新率，与 scrcpy 默认一致）；只有在电脑解码跟不上时才需要限帧",
    },
    {
        "key": "scrcpy.video_bit_rate",
        "type": "int",
        "title": "镜像码率（bps）",
        "min": 1_000_000,
        "max": 32_000_000,
        "desc": "越高越清晰，带宽占用越大",
    },
    {
        "key": "scrcpy.crop",
        "type": "string",
        "title": "裁剪区域（w:h:x:y）",
        "desc": "留空不裁剪；格式为宽:高:x:y",
    },
    {
        "key": "scrcpy.jar",
        "type": "string",
        "title": "scrcpy-server JAR 路径",
        "desc": "留空使用内置 vendor 版本",
    },
    {
        "key": "recording.click_dump",
        "type": "bool",
        "title": "录制前抓取界面层级",
        "desc": "点击定位更准，会多一次层级抓取",
    },
    {
        "key": "recording.confirm_before_save",
        "type": "bool",
        "title": "录制结束显示确认清单",
        "desc": "转脚本前检查节点",
    },
    {
        "key": "recording.target_mode",
        "type": "enum",
        "title": "录制转脚本定位方式",
        "options": [
            {"label": "绝对坐标（推荐）", "value": "point"},
            {"label": "元素定位（失败回退坐标）", "value": "element"},
        ],
        "desc": "坐标最稳；元素定位更耐分辨率变化，失败会回退坐标",
    },
    {
        "key": "capture.logcat_auto_attach",
        "type": "bool",
        "title": "运行自动采集 logcat",
        "desc": "还需脚本设置 logcat=true；关闭后仍可手动查看",
    },
    {
        "key": "capture.network.mode",
        "type": "enum",
        "title": "网络域名抓包",
        "options": [
            {"label": "关闭（不抓包）", "value": "off"},
            {"label": "仅手动（控制台开始/停止）", "value": "manual"},
            {"label": "跟随运行（每次运行自动抓包）", "value": "run"},
        ],
        "desc": "只记录访问的 host/port，不抓 Header 与正文；跟随运行会随脚本自动启停",
    },
    {
        "key": "capture.network.port",
        "type": "int",
        "title": "域名抓包端口",
        "min": 1024,
        "max": 65535,
        "desc": "本机代理监听端口，默认 8080；被占用时修改",
    },
    {
        "key": "retention.days",
        "type": "int",
        "title": "运行保留天数",
        "min": 1,
        "max": 3650,
        "desc": "清理超过该天数的运行与产物",
    },
    {
        "key": "retention.report_exclude_video",
        "type": "bool",
        "title": "报告中排除视频",
        "desc": "减小报告体积",
    },
    {
        "key": "retention.report_max_mb",
        "type": "int",
        "title": "报告体积上限（MB）",
        "min": 1,
        "max": 4096,
        "desc": "超过时排除大文件",
    },
    {
        "key": "security.token",
        "type": "string",
        "title": "访问令牌",
        "desc": "留空不校验；对外暴露服务时建议设置并重启后端",
    },
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


def set_file_layer(values: dict[str, Any]) -> None:
    """测试/嵌入用：整层替换文件层（不落盘）。生产写配置走 write_file_values()。"""
    with _LOCK:
        global _FILE_LAYER, _EFFECTIVE
        _FILE_LAYER = dict(values)
        _EFFECTIVE = None


def _read_config_raw() -> dict[str, Any]:
    """严格读取配置文件（嵌套 JSON）。文件缺失 -> {}；非法 JSON -> ValueError。

    写配置前必须严格读：宁可报错拒绝保存，也不能把用户手写的文件覆盖成一片空白。
    """
    path = config_file_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"配置文件不是合法 JSON：{path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"配置文件顶层必须是 JSON 对象：{path}")
    return raw


def _set_dotted(raw: dict[str, Any], key: str, value: Any) -> None:
    parts = key.split(".")
    node = raw
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def _delete_dotted(raw: dict[str, Any], key: str) -> None:
    parts = key.split(".")
    stack: list[tuple[dict[str, Any], str]] = []
    node = raw
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            return
        stack.append((node, part))
        node = child
    node.pop(parts[-1], None)
    # 删掉因为这次删除而变空的面包屑对象（{"engine": {}} 这类残留）
    for parent, part in reversed(stack):
        if isinstance(parent.get(part), dict) and not parent[part]:
            parent.pop(part, None)
        else:
            break


def write_file_values(values: dict[str, Any]) -> Path:
    """把「点号 key -> 值」写进配置文件：None 表示删除该项；原子写 + 立即 reload。

    返回写入的配置文件路径。非法 JSON 抛 ValueError（由 API 层转成 400）。
    """
    with _LOCK:
        raw = _read_config_raw()
        for key, value in values.items():
            if value is None:
                _delete_dotted(raw, key)
            else:
                _set_dotted(raw, key, value)
        path = config_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    reload_file_layer()
    return path


def _bootstrap_state(layer: dict[str, Any], env: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    state: dict[str, tuple[Any, str]] = {}
    for key in BOOTSTRAP_KEYS:
        if key in env:
            state[key] = (_coerce(key, env[key]), "env")
        elif key in layer:
            state[key] = (_coerce(key, layer[key]), "file")
        else:
            state[key] = (DEFAULTS.get(key), "default")
    return state


def reload_file_layer() -> None:
    """重新读取配置文件。

    启动项（数据目录/端口/host…）在第一次载入时冻结：运行中改它们的文件值不会生效
    （只打一条 warning），否则会出现「DB 还在旧目录、artifacts 已经切到新目录」的分裂状态。
    """
    global _FILE_LAYER, _EFFECTIVE, _BOOTSTRAP_LOCKED
    with _LOCK:
        layer = load_file_layer()
        env = _env_layer()
        state = _bootstrap_state(layer, env)
        if _BOOTSTRAP_LOCKED is None:
            _BOOTSTRAP_LOCKED = state
        else:
            changed = [key for key, (value, _src) in state.items() if value != _BOOTSTRAP_LOCKED[key][0]]
            if changed:
                logging.getLogger("autotest").warning(
                    "检测到启动项变化（%s）：运行中不生效，重启 AutoTest 后生效",
                    ", ".join(sorted(changed)),
                )
            for key in changed:
                state.pop(key, None)
            _BOOTSTRAP_LOCKED.update(state)
        _FILE_LAYER = layer
        _EFFECTIVE = None


def bootstrap_pending_changes() -> list[str]:
    """配置文件/环境变量里的启动项与启动时冻结值不一致的 key（需重启才生效）。"""
    locked = _BOOTSTRAP_LOCKED
    if locked is None:
        return []
    state = _bootstrap_state(load_file_layer(), _env_layer())
    return sorted(key for key, (value, _src) in state.items() if value != locked.get(key, (None, ""))[0])


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
            # 优先级：defaults < file < env
            for layer in (_FILE_LAYER, _env_layer()):
                for key, value in layer.items():
                    merged[key] = _coerce(key, value) if key in DEFAULTS else value
            # 启动项用启动时冻结的值覆盖（运行中 reload 不得改变它们）
            for key, (value, _source) in (_BOOTSTRAP_LOCKED or {}).items():
                merged[key] = value
            _EFFECTIVE = merged
        return dict(_EFFECTIVE)


def get(key: str, default: Any = None) -> Any:
    return effective().get(key, DEFAULTS.get(key, default))


def source_of(key: str) -> str:
    locked = _BOOTSTRAP_LOCKED
    if key in BOOTSTRAP_KEYS and locked is not None:
        return locked.get(key, (None, "default"))[1]
    if key in _env_layer():
        return "env"
    if key in _FILE_LAYER:
        return "file"
    return "default"


def sources() -> dict[str, str]:
    return {key: source_of(key) for key in DEFAULTS}


def reset_to_defaults() -> None:
    """清空内存层与启动项冻结（测试用）；不改磁盘上的配置文件。"""
    with _LOCK:
        global _FILE_LAYER, _EFFECTIVE, _BOOTSTRAP_LOCKED
        _FILE_LAYER = {}
        _EFFECTIVE = None
        _BOOTSTRAP_LOCKED = None


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
