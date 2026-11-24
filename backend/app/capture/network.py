"""网络抓包 - 域名模式（可选能力，契约 §0 / §7.1）。

实现要点：

- **纯 stdlib 最小 HTTP 代理**（不依赖 mitmproxy）：监听 ``127.0.0.1:<port>``，
  普通 HTTP 转发请求/响应，``CONNECT host:port`` 建立原始 TCP 隧道；不解密 TLS、
  不装 CA、不落 body/header。
- **只记录域名**：每一行 JSONL 仅包含 ``{ts, host, port, scheme}``，绝不写
  header、请求体、响应体、URL path/query。
- **设备侧代理**：``adb reverse tcp:<port> tcp:<port>`` + ``settings put global
  http_proxy 127.0.0.1:<port>``；开始前保存设备原 ``http_proxy``，停止/失败时恢复
  （无原值写 ``:0``）并清理 reverse。所有 adb 命令都带 ``-s <serial>``，只影响目标设备。
- **进程内单例**：一个代理 listener 服务采集会话；会话各自写自己的 JSONL。
  当前只允许一台设备同时抓包，避免 adb reverse 共享端口导致域名归属不清；
  停止最后一个会话时关闭 listener 与连接。
- **已知限制**：QUIC/HTTP3、不使用系统代理的 App、自签证书/证书固定（pinning）的
  流量可能漏抓；域名模式只记录 host，不记录 URL path/query/header/body。
"""

from __future__ import annotations

import json
import re
import socket
import socketserver
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, TextIO
from urllib.parse import urlsplit

from .. import config
from ..db import session_scope
from ..errors import ApiError, device_unavailable
from ..logs import logger
from ..models import Artifact, now_iso

_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_ACTIVE: dict[str, NetworkCapture] = {}
_LAST: dict[str, dict[str, Any]] = {}
_LOCK = threading.RLock()
_PROXY: _ProxyServer | None = None

_DISABLED_MESSAGE = "网络抓包未启用"
_DEFAULT_PORT = 8080
_RECENT_LIMIT = 30
_HEADER_LIMIT = 64 * 1024
_SOCKET_TIMEOUT_S = 30.0
_PROXY_HOST = "127.0.0.1"


def _safe_serial(serial: str) -> str:
    return _SAFE_RE.sub("_", str(serial or "")) or "device"


def mode() -> str:
    """网络抓包模式：off / manual / run；非法值按 off 处理。"""
    value = str(config.get("capture.network.mode") or "off").strip().lower()
    return value if value in {"off", "manual", "run"} else "off"


def available() -> bool:
    """抓包是否可用：manual/run 模式；域名代理为 stdlib 实现，无第三方依赖。"""
    return mode() != "off"


def _ensure_available() -> None:
    if not available():
        raise ApiError("UNSUPPORTED", _DISABLED_MESSAGE)


def _configured_port() -> int:
    """监听端口：``capture.network.port``（设置页可改）> 默认 8080。

    允许 0：由系统分配空闲端口（测试/联调避免端口冲突）；真实采集使用分配后的端口。
    """
    raw = config.get("capture.network.port", _DEFAULT_PORT)
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PORT
    if not 0 <= port <= 65535:
        return _DEFAULT_PORT
    return port


# ---------------------------------------------------------------- adb
def _adb_module() -> Any | None:
    try:
        from ..android import adb  # noqa: PLC0415 - 可选依赖，测试可 monkeypatch
    except ImportError:
        return None
    return adb


def _run_adb(args: list[str], timeout_s: float = 15) -> Any:
    adb = _adb_module()
    if adb is None:
        raise ApiError("UNSUPPORTED", "adb module is not available")
    try:
        return adb.run_adb(list(args), timeout_s=timeout_s)
    except ApiError:
        raise
    except Exception as exc:  # noqa: BLE001 - 设备异常统一转 ApiError
        raise device_unavailable(f"adb command failed: {exc}", {"args": list(args)}) from exc


def _adb_ok(result: Any) -> bool:
    return int(getattr(result, "code", 1) or 0) == 0


def _adb_output(result: Any) -> str:
    return f"{getattr(result, 'stdout', '') or ''}{getattr(result, 'stderr', '') or ''}".strip()


def _normalize_proxy_value(value: str | None) -> str | None:
    """把 ``settings get global http_proxy`` 的输出规范化为"原值/无原值"。"""
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    text = lines[-1] if lines else ""
    if text.lower() in ("", "null", "none", "undefined"):
        return None
    return text


def _device_settings(serial: str, *parts: str) -> list[str]:
    return ["-s", str(serial), "shell", "settings", *parts]


# ================================================================ 代理监听器
class _ProxyTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    block_on_close = False


class _ProxyServer:
    """进程内单例代理：一个 listener + 所有活跃连接的跟踪/关闭。"""

    def __init__(self, port: int, on_record: Callable[[dict[str, Any]], None]) -> None:
        self.on_record = on_record
        self._lock = threading.RLock()
        self._sockets: set[socket.socket] = set()
        self._stopping = False
        try:
            self._server = _ProxyTCPServer((_PROXY_HOST, int(port)), _ProxyHandler)
        except OSError as exc:
            raise ApiError(
                "CONFLICT",
                f"cannot start network proxy on {_PROXY_HOST}:{port}: {exc}",
                {"host": _PROXY_HOST, "port": port},
            ) from exc
        self._server.on_record = on_record
        self._server.proxy = self
        self.host = _PROXY_HOST
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._serve,
            name=f"network-proxy-{self.port}",
            daemon=True,
        )
        self._thread.start()

    def _serve(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.2)
        except Exception:  # pragma: no cover - 线程退出只记录
            logger.warning("network proxy serve loop failed", exc_info=True)

    def track(self, sock: socket.socket) -> bool:
        """登记连接；代理已进入停止流程时立即关闭并返回 False。"""
        with self._lock:
            if self._stopping:
                close_now = True
            else:
                self._sockets.add(sock)
                close_now = False
        if close_now:
            try:
                sock.close()
            except OSError:
                pass
            return False
        return True

    def untrack(self, sock: socket.socket) -> None:
        with self._lock:
            self._sockets.discard(sock)

    def stop(self) -> None:
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
            sockets = list(self._sockets)
            self._sockets.clear()
        try:
            self._server.shutdown()
        except Exception:  # pragma: no cover - 已停止/线程异常
            pass
        try:
            self._server.server_close()
        except OSError:  # pragma: no cover
            pass
        for sock in sockets:
            try:
                sock.close()
            except OSError:  # pragma: no cover
                pass
        self._thread.join(timeout=2.0)


class _ProxyHandler(socketserver.StreamRequestHandler):
    """最小 HTTP/CONNECT 代理 handler：只解析 host，不记录任何正文/头部。"""

    timeout = _SOCKET_TIMEOUT_S

    def handle(self) -> None:  # noqa: C901 - 协议分支集中在一个方法里更直观
        proxy: _ProxyServer = self.server.proxy  # type: ignore[attr-defined]
        upstream: socket.socket | None = None
        if not proxy.track(self.connection):
            return
        try:
            head = self._read_head()
            if head is None:
                return
            request_line, headers = head
            parts = request_line.split()
            if len(parts) < 2:
                self._send_simple(400, "Bad Request")
                return
            method, target = parts[0], parts[1]
            if method.upper() == "CONNECT":
                upstream = self._handle_connect(target)
            else:
                upstream = self._handle_http(method, target, headers)
        except OSError:
            # 客户端断开/超时：正常结束，不记录流量内容
            pass
        except Exception:  # noqa: BLE001 - 单连接异常不影响代理
            logger.debug("network proxy connection failed", exc_info=True)
        finally:
            if upstream is not None:
                proxy.untrack(upstream)
                try:
                    upstream.close()
                except OSError:
                    pass
            proxy.untrack(self.connection)

    # ------------------------------------------------------------ 协议解析
    def _read_head(self) -> tuple[str, list[tuple[str, str]]] | None:
        lines: list[bytes] = []
        total = 0
        while True:
            line = self.rfile.readline(_HEADER_LIMIT)
            if not line:
                return None
            total += len(line)
            if total > _HEADER_LIMIT:
                self._send_simple(431, "Request Header Fields Too Large")
                return None
            if line in (b"\r\n", b"\n"):
                break
            lines.append(line)
        if not lines:
            return None
        request_line = lines[0].decode("latin-1").strip()
        headers: list[tuple[str, str]] = []
        for raw in lines[1:]:
            text = raw.decode("latin-1").rstrip("\r\n")
            name, sep, value = text.partition(":")
            if not sep:
                continue
            headers.append((name.strip(), value.strip()))
        return request_line, headers

    @staticmethod
    def _header(headers: list[tuple[str, str]], name: str) -> str | None:
        for key, value in headers:
            if key.lower() == name.lower():
                return value
        return None

    @staticmethod
    def _parse_authority(authority: str, default_port: int) -> tuple[str, int]:
        parsed = urlsplit("//" + authority.strip())
        host = parsed.hostname
        if not host:
            raise ValueError(f"invalid authority: {authority!r}")
        port = parsed.port if parsed.port is not None else default_port
        return host, int(port)

    # ------------------------------------------------------------ 记录
    def _record(self, host: str, port: int, scheme: str) -> None:
        host = str(host or "").strip().lower().rstrip(".")
        if not host:
            return
        try:
            self.server.proxy.on_record(  # type: ignore[attr-defined]
                {"ts": now_iso(), "host": host, "port": int(port), "scheme": scheme}
            )
        except Exception:  # pragma: no cover - 记录失败不影响转发
            logger.warning("network domain record failed", exc_info=True)

    # ------------------------------------------------------------ CONNECT
    def _handle_connect(self, target: str) -> socket.socket | None:
        try:
            host, port = self._parse_authority(target, 443)
        except (ValueError, TypeError):
            self._send_simple(400, "Bad CONNECT Target")
            return None
        self._record(host, port, "https")
        try:
            upstream = socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S)
        except OSError as exc:
            logger.info("network proxy CONNECT %s:%s failed: %s", host, port, exc)
            self._send_simple(502, "Bad Gateway")
            return None
        proxy: _ProxyServer = self.server.proxy  # type: ignore[attr-defined]
        if not proxy.track(upstream):
            return None

        self.connection.sendall(
            b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: AutoTest\r\n\r\n"
        )
        self.wfile.flush()
        # 隧道建立后可能长时间空闲（HTTPS keep-alive），不设读超时；stop 时主动关 socket。
        self.connection.settimeout(None)

        def client_to_upstream() -> None:
            try:
                while True:
                    # read1：保留 rfile 里已经缓冲的 TLS ClientHello 字节
                    data = self.rfile.read1(65536)
                    if not data:
                        break
                    upstream.sendall(data)
            except (OSError, ValueError):
                pass
            finally:
                try:
                    upstream.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

        pump = threading.Thread(
            target=client_to_upstream,
            name="network-proxy-connect",
            daemon=True,
        )
        pump.start()
        self._relay_to_client(upstream)
        pump.join(timeout=1.0)
        return upstream

    # ------------------------------------------------------------ 普通 HTTP
    def _handle_http(
        self,
        method: str,
        target: str,
        headers: list[tuple[str, str]],
    ) -> socket.socket | None:
        try:
            host, port, path = self._split_http_target(target, headers)
        except (ValueError, TypeError):
            self._send_simple(400, "Bad Request")
            return None
        self._record(host, port, "http")
        try:
            upstream = socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT_S)
        except OSError as exc:
            logger.info("network proxy %s %s:%s failed: %s", method, host, port, exc)
            self._send_simple(502, "Bad Gateway")
            return None
        proxy: _ProxyServer = self.server.proxy  # type: ignore[attr-defined]
        if not proxy.track(upstream):
            return None

        try:
            self._forward_request(method, path, headers, host, port, upstream)
            self._relay_to_client(upstream)
        finally:
            try:
                upstream.shutdown(socket.SHUT_WR)
            except OSError:
                pass
        return upstream

    def _split_http_target(
        self,
        target: str,
        headers: list[tuple[str, str]],
    ) -> tuple[str, int, str]:
        parsed = urlsplit(target)
        if parsed.scheme.lower() == "http" and parsed.hostname:
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            return parsed.hostname, int(parsed.port or 80), path
        host_header = self._header(headers, "Host")
        if not host_header:
            raise ValueError("Host header is required")
        host, port = self._parse_authority(host_header, 80)
        path = target if target.startswith("/") else f"/{target}"
        return host, port, path

    def _forward_request(
        self,
        method: str,
        path: str,
        headers: list[tuple[str, str]],
        host: str,
        port: int,
        upstream: socket.socket,
    ) -> None:
        # 客户端若期待 100-continue，先回复再转发（避免双方互等）；Expect 不转发。
        if "100-continue" in (self._header(headers, "Expect") or "").lower():
            self.connection.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        host_value = host if port in (80, 443) else f"{host}:{port}"
        if ":" in host:  # IPv6：Host 头必须加方括号
            host_value = f"[{host}]" if port in (80, 443) else f"[{host}]:{port}"
        skip = {"connection", "proxy-connection", "proxy-authorization", "expect", "host"}
        lines = [f"{method} {path} HTTP/1.1"]
        lines.append(f"Host: {host_value}")
        for name, value in headers:
            if name.lower() in skip:
                continue
            lines.append(f"{name}: {value}")
        lines.append("Connection: close")
        upstream.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1", errors="replace"))
        self._forward_request_body(headers, upstream)

    def _forward_request_body(self, headers: list[tuple[str, str]], upstream: socket.socket) -> None:
        transfer_encoding = (self._header(headers, "Transfer-Encoding") or "").lower()
        if "chunked" in transfer_encoding:
            self._forward_chunked(upstream)
            return
        raw_length = self._header(headers, "Content-Length")
        if raw_length is None:
            return
        try:
            remaining = int(raw_length)
        except ValueError:
            return
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                return
            upstream.sendall(chunk)
            remaining -= len(chunk)

    def _forward_chunked(self, upstream: socket.socket) -> None:
        while True:
            line = self.rfile.readline(_HEADER_LIMIT)
            if not line:
                return
            upstream.sendall(line)
            token = line.split(b";", 1)[0].strip()
            try:
                size = int(token, 16)
            except ValueError:
                return
            if size == 0:
                while True:
                    trailer = self.rfile.readline(_HEADER_LIMIT)
                    if not trailer:
                        return
                    upstream.sendall(trailer)
                    if trailer in (b"\r\n", b"\n"):
                        return
            remaining = size
            while remaining > 0:
                chunk = self.rfile.read(min(65536, remaining))
                if not chunk:
                    return
                upstream.sendall(chunk)
                remaining -= len(chunk)
            crlf = self.rfile.read(2)
            if crlf:
                upstream.sendall(crlf)

    # ------------------------------------------------------------ 双向转发
    def _relay_to_client(self, upstream: socket.socket) -> None:
        # 已建立连接后按字节流持续转发，不因长时间空闲而截断；关闭由 stop 主动触发。
        upstream.settimeout(None)
        while True:
            try:
                data = upstream.recv(65536)
            except OSError:
                break
            if not data:
                break
            self.connection.sendall(data)

    def _send_simple(self, status: int, reason: str) -> None:
        body = b""
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1")
        try:
            self.connection.sendall(head + body)
        except OSError:
            pass


# ---------------------------------------------------------------- 代理单例
def _record_domain(entry: dict[str, Any]) -> None:
    """把一条域名记录分发给活跃会话（进程内只允许一台设备抓包）。"""
    with _LOCK:
        captures = list(_ACTIVE.values())
    for capture in captures:
        if capture.active:
            capture.record(entry)


def _ensure_proxy() -> _ProxyServer:
    global _PROXY
    with _LOCK:
        if _PROXY is None:
            _PROXY = _ProxyServer(_configured_port(), _record_domain)
        return _PROXY


def _stop_proxy() -> None:
    global _PROXY
    with _LOCK:
        proxy = _PROXY
        _PROXY = None
    if proxy is not None:
        proxy.stop()


def _detach_proxy_if_idle() -> _ProxyServer | None:
    """在锁内确认没有活跃会话后再摘下单例，避免与并发 start 竞态。"""
    global _PROXY
    with _LOCK:
        if any(capture.active for capture in _ACTIVE.values()):
            return None
        proxy = _PROXY
        _PROXY = None
        return proxy


def _stop_proxy_if_idle() -> None:
    proxy = _detach_proxy_if_idle()
    if proxy is not None:
        proxy.stop()


# ---------------------------------------------------------------- 采集会话
class NetworkCapture:
    """单个 serial 的域名采集会话：设备代理 + JSONL + artifact。"""

    def __init__(
        self,
        serial: str,
        run_id: int | None = None,
        started_by_run: bool = False,
    ) -> None:
        self.serial = str(serial)
        self.run_id = run_id
        self.started_by_run = bool(started_by_run and run_id is not None)
        self.started_at = now_iso()
        self._started_monotonic = time.monotonic()
        self.port: int | None = None
        self.path: Path | None = None
        self.active = False
        self._file: TextIO | None = None
        self._lock = threading.RLock()
        self._record_count = 0
        self._domains: set[str] = set()
        self._recent: list[str] = []
        self._recent_records: deque[dict[str, Any]] = deque(maxlen=500)
        self.original_proxy: str | None = None
        self._reverse_installed = False
        self._proxy_touched = False

    # ------------------------------------------------------------ 生命周期
    def start(self) -> NetworkCapture:
        proxy = _ensure_proxy()
        self.port = proxy.port
        try:
            directory = config.artifacts_dir() / "network"
            directory.mkdir(parents=True, exist_ok=True)
            timestamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
            self.path = directory / f"network_{_safe_serial(self.serial)}_{timestamp}.jsonl"
            self._file = self.path.open("a", encoding="utf-8")
        except OSError as exc:  # pragma: no cover - 磁盘异常
            _stop_proxy_if_idle()
            raise ApiError("INTERNAL", "网络抓包文件创建失败", {"error": str(exc)}) from exc
        try:
            self._install_device_proxy(proxy.port)
        except Exception:
            self._restore_device_proxy()
            self._close_file()
            self._remove_empty_file()
            _stop_proxy_if_idle()
            raise
        self.active = True
        logger.info(
            "network capture started: %s -> %s (proxy %s:%s)",
            self.serial,
            self.path,
            proxy.host,
            proxy.port,
        )
        return self

    def stop(self) -> dict[str, Any]:
        with self._lock:
            self.active = False
        self._restore_device_proxy()
        self._close_file()
        result = self.to_dict()
        self._write_artifact(result)
        logger.info("network capture stopped: %s -> %s", self.serial, self.path)
        return result

    def _close_file(self) -> None:
        with self._lock:
            handle = self._file
            self._file = None
        if handle is not None:
            try:
                handle.flush()
            except OSError:
                pass
            try:
                handle.close()
            except OSError:  # pragma: no cover
                pass

    def _remove_empty_file(self) -> None:
        if self.path is None:
            return
        try:
            if self.path.exists() and self.path.stat().st_size == 0:
                self.path.unlink()
        except OSError:  # pragma: no cover
            pass

    # ------------------------------------------------------------ 设备代理
    def _install_device_proxy(self, port: int) -> None:
        if _adb_module() is None:
            raise ApiError("UNSUPPORTED", "adb module is not available")
        remote = f"tcp:{port}"
        result = _run_adb(["-s", self.serial, "reverse", remote, remote], timeout_s=15)
        if not _adb_ok(result):
            raise device_unavailable(
                f"adb reverse failed: {_adb_output(result) or 'unknown error'}",
                {"serial": self.serial, "port": port},
            )
        self._reverse_installed = True

        original = _run_adb(_device_settings(self.serial, "get", "global", "http_proxy"), timeout_s=10)
        if not _adb_ok(original):
            raise device_unavailable(
                f"read device http_proxy failed: {_adb_output(original) or 'unknown error'}",
                {"serial": self.serial},
            )
        self.original_proxy = _normalize_proxy_value(getattr(original, "stdout", ""))

        self._proxy_touched = True
        applied = _run_adb(
            _device_settings(self.serial, "put", "global", "http_proxy", f"127.0.0.1:{port}"),
            timeout_s=10,
        )
        if not _adb_ok(applied):
            raise device_unavailable(
                f"set device http_proxy failed: {_adb_output(applied) or 'unknown error'}",
                {"serial": self.serial, "port": port},
            )

    def _restore_device_proxy(self) -> None:
        """恢复设备原代理并清理 reverse；清理路径只告警，不阻塞 artifact 落盘。"""
        if self._proxy_touched:
            value = self.original_proxy if self.original_proxy is not None else ":0"
            try:
                restored = _run_adb(
                    _device_settings(self.serial, "put", "global", "http_proxy", value),
                    timeout_s=10,
                )
                if not _adb_ok(restored):
                    logger.warning(
                        "restore http_proxy failed for %s: %s",
                        self.serial,
                        _adb_output(restored),
                    )
            except Exception as exc:  # noqa: BLE001 - 清理失败不能丢 artifact
                logger.warning("restore http_proxy failed for %s: %s", self.serial, exc)
            finally:
                self._proxy_touched = False
        if self._reverse_installed:
            remote = f"tcp:{self.port}"
            try:
                removed = _run_adb(
                    ["-s", self.serial, "reverse", "--remove", remote],
                    timeout_s=10,
                )
                if not _adb_ok(removed):
                    logger.warning(
                        "remove adb reverse failed for %s: %s",
                        self.serial,
                        _adb_output(removed),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("remove adb reverse failed for %s: %s", self.serial, exc)
            finally:
                self._reverse_installed = False

    # ------------------------------------------------------------ 记录
    def record(self, entry: dict[str, Any]) -> None:
        host = str(entry.get("host") or "").strip().lower().rstrip(".")
        if not host:
            return
        with self._lock:
            if not self.active or self._file is None:
                return
            try:
                port = int(entry.get("port") or 0)
            except (TypeError, ValueError):
                port = 0
            record = {
                "ts": str(entry.get("ts") or now_iso()),
                "host": host,
                "port": port,
                "scheme": str(entry.get("scheme") or "http"),
            }
            try:
                self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._file.flush()
            except OSError as exc:  # pragma: no cover - 磁盘异常
                logger.warning("network record write failed for %s: %s", self.serial, exc)
                return
            self._record_count += 1
            self._domains.add(host)
            self._recent_records.append(dict(record))
            if host in self._recent:
                self._recent.remove(host)
            self._recent.append(host)
            if len(self._recent) > _RECENT_LIMIT:
                del self._recent[: len(self._recent) - _RECENT_LIMIT]

    # ------------------------------------------------------------ 输出
    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            recent = list(reversed(self._recent))
            domain_count = len(self._domains)
            record_count = self._record_count
            active = self.active
        return {
            "serial": self.serial,
            "status": "running" if active else "stopped",
            "active": active,
            "mode": "domain",
            "format": "jsonl",
            "port": self.port,
            "path": str(self.path) if self.path is not None else None,
            "size": self._size(),
            "domain_count": domain_count,
            "record_count": record_count,
            "recent_domains": recent,
            "started_at": self.started_at,
            "run_id": self.run_id,
            "started_by_run": self.started_by_run,
        }

    def recent_records(self, limit: int = 200, since_iso: str | None = None) -> list[dict[str, Any]]:
        """返回最近的域名记录（时间正序）；可按 ISO 时间戳过滤节点时间窗。"""
        count = max(1, int(limit or 200))
        with self._lock:
            records = list(self._recent_records)
        if since_iso:
            records = [record for record in records if str(record.get("ts") or "") >= str(since_iso)]
        return records[-count:]

    def _size(self) -> int:
        try:
            return int(self.path.stat().st_size) if self.path is not None else 0
        except OSError:  # pragma: no cover
            return 0

    def _write_artifact(self, result: dict[str, Any]) -> None:
        if self.path is None:
            return
        try:
            with session_scope() as db:
                db.add(
                    Artifact(
                        run_id=self.run_id,
                        step_result_id=None,
                        kind="network",
                        name=f"network {self.serial}",
                        path=str(self.path),
                        size=int(result.get("size") or 0),
                        meta={
                            "serial": self.serial,
                            "mode": "domain",
                            "format": "jsonl",
                            "port": self.port,
                            "domain_count": int(result.get("domain_count") or 0),
                            "record_count": int(result.get("record_count") or 0),
                            "started_at": self.started_at,
                            "duration_ms": int((time.monotonic() - self._started_monotonic) * 1000),
                        },
                        created_at=now_iso(),
                    )
                )
        except Exception as exc:  # pragma: no cover - 落库失败不丢文件
            logger.warning("network artifact row insert failed: %s", exc)


# ================================================================ 模块级入口
def start(
    serial: str,
    run_id: int | None = None,
    started_by_run: bool = False,
) -> dict[str, Any]:
    _ensure_available()
    serial = str(serial)
    with _LOCK:
        existing = _ACTIVE.get(serial)
        if existing is not None and existing.active:
            return existing.to_dict()
        other = next(
            (capture for key, capture in _ACTIVE.items() if key != serial and capture.active),
            None,
        )
        if other is not None:
            raise ApiError(
                "CONFLICT",
                "网络域名抓包同时只支持一台设备",
                {"serial": serial, "active_serial": other.serial},
            )
        capture = NetworkCapture(serial, run_id=run_id, started_by_run=started_by_run).start()
        _ACTIVE[serial] = capture
        _LAST.pop(serial, None)
        return capture.to_dict()


def _stop_capture(serial: str) -> dict[str, Any]:
    with _LOCK:
        capture = _ACTIVE.pop(serial, None)
        if capture is None:
            raise ApiError("NOT_FOUND", "没有进行中的网络抓包", {"serial": serial})
    # 先关 listener/连接，再恢复设备代理、写 artifact，避免停止期间还有新流量写入。
    # 是否真的关闭在 _detach_proxy_if_idle 的锁内重新判断，防止与并发 start 竞态。
    idle_proxy = _detach_proxy_if_idle()
    if idle_proxy is not None:
        idle_proxy.stop()
    result = capture.stop()
    with _LOCK:
        _LAST[serial] = result
    return result


def stop(serial: str) -> dict[str, Any]:
    _ensure_available()
    return _stop_capture(str(serial))


def attach_if_needed(run_id: int) -> NetworkCapture | None:
    """运行期自动开启域名抓包；仅 mode=run 生效，不抢占手动/其他 run 的采集。"""
    if mode() != "run":
        return None
    try:
        run_key = int(run_id)
    except (TypeError, ValueError):
        return None
    try:
        from ..models import Run

        with session_scope() as db:
            run = db.get(Run, run_key)
            serial = str(run.device_serial or "") if run is not None else ""
    except Exception:  # pragma: no cover - DB 异常
        return None
    if not serial:
        return None
    with _LOCK:
        existing = _ACTIVE.get(serial)
        if existing is not None and existing.active:
            if existing.started_by_run and existing.run_id == run_key:
                return existing
            return None
        other = next(
            (capture for key, capture in _ACTIVE.items() if key != serial and capture.active),
            None,
        )
        if other is not None:
            return None
        capture = NetworkCapture(serial, run_id=run_key, started_by_run=True).start()
        _ACTIVE[serial] = capture
        _LAST.pop(serial, None)
        return capture


def stop_run_capture(run_id: int, serial: str | None = None) -> dict[str, Any] | None:
    """只停止本 run 自动启动的网络抓包；手动采集不误停。"""
    try:
        run_key = int(run_id)
    except (TypeError, ValueError):
        return None
    with _LOCK:
        target: NetworkCapture | None = None
        if serial is not None:
            candidate = _ACTIVE.get(str(serial))
            if candidate is not None and candidate.started_by_run and candidate.run_id == run_key:
                target = candidate
        else:
            for candidate in _ACTIVE.values():
                if candidate.started_by_run and candidate.run_id == run_key:
                    target = candidate
                    break
        if target is None:
            return None
        target_serial = target.serial
    return _stop_capture(target_serial)


def recent_records(
    serial: str,
    limit: int = 200,
    since_iso: str | None = None,
) -> list[dict[str, Any]]:
    """当前活跃采集的最近域名记录；没有活跃采集时返回空列表。"""
    with _LOCK:
        capture = _ACTIVE.get(str(serial))
    if capture is None or not capture.active:
        return []
    return capture.recent_records(limit=limit, since_iso=since_iso)


def status(serial: str) -> dict[str, Any]:
    _ensure_available()
    serial = str(serial)
    with _LOCK:
        capture = _ACTIVE.get(serial)
        if capture is not None and capture.active:
            return capture.to_dict()
        last = _LAST.get(serial)
    if last is not None:
        return dict(last)
    return {
        "serial": serial,
        "status": "stopped",
        "active": False,
        "mode": "domain",
        "format": "jsonl",
        "port": _configured_port(),
        "path": None,
        "size": 0,
        "domain_count": 0,
        "record_count": 0,
        "recent_domains": [],
    }


def shutdown_all() -> None:
    with _LOCK:
        serials = list(_ACTIVE)
    for serial in serials:
        try:
            _stop_capture(serial)
        except Exception as exc:  # pragma: no cover - 关闭路径只告警
            logger.warning("network shutdown failed for %s: %s", serial, exc)
    with _LOCK:
        idle = not any(capture.active for capture in _ACTIVE.values())
    if idle:
        _stop_proxy()


__all__ = [
    "NetworkCapture",
    "attach_if_needed",
    "available",
    "recent_records",
    "shutdown_all",
    "start",
    "status",
    "stop",
    "stop_run_capture",
]
