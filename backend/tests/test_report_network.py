"""网络抓包（域名模式）：stdlib 代理、设备代理恢复、JSONL、API status。

全部用例不依赖真实设备与外部网络：adb 用测试桩，代理上游是本机临时 TCP/HTTP server。
"""

from __future__ import annotations

import http.client
import json
import socket
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

import tests.test_core_support as support
from app import config
from app.android.driver import CommandResult
from app.capture import network
from app.db import session_scope
from app.errors import ApiError
from app.models import Artifact

core_client = support.core_client

# ---------------------------------------------------------------- fixtures / stubs


@pytest.fixture()
def net_env(tmp_path, temp_db):
    config.reset_to_defaults()
    config.set_db_overrides({"runtime.data_dir": str(tmp_path / "data")})
    yield tmp_path
    network.shutdown_all()
    network._ACTIVE.clear()
    network._LAST.clear()
    config.reset_to_defaults()


def _enable(port: int = 0) -> None:
    # 保留夹具设置的数据目录，只覆盖抓包开关（set_db_overrides 是整层替换）。
    config.set_db_overrides(
        {
            "runtime.data_dir": str(config.data_dir()),
            "capture.network.enabled": True,
            "capture.network.port": port,
        }
    )


class _StubAdb:
    """记录 adb 参数并模拟 settings / reverse 行为。"""

    def __init__(
        self,
        original_proxy: str | None = None,
        fail_when: Any = None,
    ) -> None:
        self.original_proxy = original_proxy
        self.fail_when = fail_when
        self.calls: list[list[str]] = []

    def run_adb(self, args: list[str], timeout_s: float = 15) -> CommandResult:
        self.calls.append(list(args))
        if self.fail_when is not None and self.fail_when(args):
            return CommandResult(1, "", "boom")
        if "get" in args and "http_proxy" in args:
            value = self.original_proxy if self.original_proxy is not None else "null"
            return CommandResult(0, f"{value}\n", "")
        return CommandResult(0, "", "")


@pytest.fixture()
def upstream_http():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, body: bytes) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 协议方法
            self._reply(b"upstream-ok")

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            self._reply(b"post:" + body)

        def log_message(self, *_args):  # noqa: D401 - 静默
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture()
def echo_tcp():
    class Handler(socketserver.BaseRequestHandler):
        def handle(self) -> None:
            while True:
                data = self.request.recv(65536)
                if not data:
                    return
                self.request.sendall(data)

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _RunningProxy:
    """启动一个抓包会话并暴露代理端口（测试用，端口 0 由系统分配）。"""

    def __init__(self, monkeypatch, serial: str = "fake-net", original: str | None = None):
        self.stub = _StubAdb(original_proxy=original)
        monkeypatch.setattr(network, "_adb_module", lambda: self.stub)
        self.info = network.start(serial)
        self.serial = serial

    @property
    def port(self) -> int:
        port = self.info.get("port")
        assert isinstance(port, int) and port > 0
        return port


# ---------------------------------------------------------------- 可用性 / 关闭配置


def test_disabled_by_default_raises_unsupported(net_env) -> None:
    assert network.available() is False
    for action in (
        lambda: network.start("fake-1"),
        lambda: network.stop("fake-1"),
        lambda: network.status("fake-1"),
    ):
        with pytest.raises(ApiError) as excinfo:
            action()
        assert excinfo.value.code == "UNSUPPORTED"
        assert "网络抓包未启用" in excinfo.value.message


def test_enabled_without_mitmproxy_is_supported(net_env, monkeypatch) -> None:
    """域名模式是 stdlib 实现，不再探测 mitmproxy。"""
    _enable()
    monkeypatch.setattr(network, "_adb_module", lambda: _StubAdb())
    assert network.available() is True


# ---------------------------------------------------------------- 生命周期 / artifact


def test_capture_lifecycle_writes_jsonl_artifact(net_env, monkeypatch) -> None:
    _enable()
    runtime = _RunningProxy(monkeypatch, serial="fake-net")
    started = runtime.info
    assert started["status"] == "running"
    assert started["active"] is True
    assert started["mode"] == "domain"
    assert started["format"] == "jsonl"
    assert started["port"] == runtime.port
    path = Path(str(started["path"]))
    assert path.suffix == ".jsonl"
    assert path.parent == config.artifacts_dir() / "network"
    assert path.is_file()

    # 重复 start 幂等
    assert network.start("fake-net")["path"] == started["path"]
    assert network.status("fake-net")["active"] is True

    stopped = network.stop("fake-net")
    assert stopped["status"] == "stopped"
    assert stopped["active"] is False
    assert stopped["path"] == started["path"]
    assert network.status("fake-net")["active"] is False
    assert network._PROXY is None  # 最后一个会话停止时 listener 关闭

    with session_scope() as db:
        artifacts = list(db.scalars(select(Artifact).where(Artifact.kind == "network")))
    assert len(artifacts) == 1
    assert artifacts[0].path == stopped["path"]
    assert artifacts[0].run_id is None  # 域名抓包不绑定 run
    assert artifacts[0].meta["mode"] == "domain"
    assert artifacts[0].meta["format"] == "jsonl"
    assert artifacts[0].size == stopped["size"]

    with pytest.raises(ApiError) as excinfo:
        network.stop("fake-net")
    assert excinfo.value.code == "NOT_FOUND"


def test_second_device_conflicts(net_env, monkeypatch) -> None:
    """共享 adb reverse 端口时，只允许一台设备抓包，避免域名归属错误。"""
    _enable()
    monkeypatch.setattr(network, "_adb_module", lambda: _StubAdb())
    network.start("fake-first")
    try:
        with pytest.raises(ApiError) as excinfo:
            network.start("fake-second")
        assert excinfo.value.code == "CONFLICT"
        assert excinfo.value.details["active_serial"] == "fake-first"
    finally:
        network.stop("fake-first")


def test_artifact_run_id_stays_none(net_env, monkeypatch) -> None:
    _enable()
    monkeypatch.setattr(network, "_adb_module", lambda: _StubAdb())
    network.start("fake-run", run_id=123)
    network.stop("fake-run")
    with session_scope() as db:
        artifact = db.scalars(select(Artifact).where(Artifact.kind == "network")).one()
    assert artifact.run_id is None


# ---------------------------------------------------------------- 设备代理保存 / 恢复


def test_device_proxy_saved_and_restored(net_env, monkeypatch) -> None:
    _enable()
    runtime = _RunningProxy(monkeypatch, serial="fake-proxy", original="10.0.0.8:8888")
    port = runtime.port
    remote = f"tcp:{port}"
    assert runtime.stub.calls[:3] == [
        ["-s", "fake-proxy", "reverse", remote, remote],
        ["-s", "fake-proxy", "shell", "settings", "get", "global", "http_proxy"],
        [
            "-s",
            "fake-proxy",
            "shell",
            "settings",
            "put",
            "global",
            "http_proxy",
            f"127.0.0.1:{port}",
        ],
    ]
    network.stop("fake-proxy")
    assert [
        "-s",
        "fake-proxy",
        "shell",
        "settings",
        "put",
        "global",
        "http_proxy",
        "10.0.0.8:8888",
    ] in runtime.stub.calls
    assert ["-s", "fake-proxy", "reverse", "--remove", remote] in runtime.stub.calls


def test_device_proxy_without_original_restores_zero(net_env, monkeypatch) -> None:
    _enable()
    runtime = _RunningProxy(monkeypatch, serial="fake-none", original=None)
    network.stop("fake-none")
    assert [
        "-s",
        "fake-none",
        "shell",
        "settings",
        "put",
        "global",
        "http_proxy",
        ":0",
    ] in runtime.stub.calls


def test_reverse_failure_rolls_back_without_touching_proxy(net_env, monkeypatch) -> None:
    _enable()
    stub = _StubAdb(fail_when=lambda args: "reverse" in args and "--remove" not in args)
    monkeypatch.setattr(network, "_adb_module", lambda: stub)
    with pytest.raises(ApiError) as excinfo:
        network.start("fake-fail")
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert network._ACTIVE == {}
    assert network._PROXY is None
    assert not any("put" in call for call in stub.calls)
    assert not any("--remove" in call for call in stub.calls)


def test_proxy_put_failure_rolls_back_proxy_and_reverse(net_env, monkeypatch) -> None:
    _enable()
    stub = _StubAdb(
        original_proxy="10.0.0.9:3128",
        fail_when=lambda args: "put" in args and str(args[-1]).startswith("127.0.0.1:"),
    )
    monkeypatch.setattr(network, "_adb_module", lambda: stub)
    with pytest.raises(ApiError) as excinfo:
        network.start("fake-put-fail")
    assert excinfo.value.code == "DEVICE_UNAVAILABLE"
    assert network._ACTIVE == {}
    assert network._PROXY is None
    # 失败后恢复原值 + 清理 reverse
    assert [
        "-s",
        "fake-put-fail",
        "shell",
        "settings",
        "put",
        "global",
        "http_proxy",
        "10.0.0.9:3128",
    ] in stub.calls
    assert any("--remove" in call for call in stub.calls)
    # 失败不会留下空的 JSONL 文件
    directory = config.artifacts_dir() / "network"
    assert not list(directory.glob("*.jsonl"))


# ---------------------------------------------------------------- 仅域名 JSONL


def test_proxy_records_only_hosts_for_http_and_connect(
    net_env, monkeypatch, upstream_http, echo_tcp
) -> None:
    _enable()
    runtime = _RunningProxy(monkeypatch, serial="fake-traffic")
    proxy_port = runtime.port

    # 普通 HTTP：绝对 URI 带 path/query，header 带敏感值；上游返回正文。
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
    conn.request(
        "GET",
        f"http://localhost:{upstream_http}/private/path?token=secret",
        headers={
            "Host": f"localhost:{upstream_http}",
            "X-Secret-Header": "should-not-leak",
        },
    )
    response = conn.getresponse()
    assert response.status == 200
    assert response.read() == b"upstream-ok"
    conn.close()

    # HTTPS CONNECT：不 TLS 解密，仅把原始字节双向转发给本机 echo server。
    tunnel = socket.create_connection(("127.0.0.1", proxy_port), timeout=5)
    tunnel.sendall(
        f"CONNECT 127.0.0.1:{echo_tcp} HTTP/1.1\r\nHost: 127.0.0.1:{echo_tcp}\r\n\r\n".encode()
    )
    handshake = b""
    while b"\r\n\r\n" not in handshake:
        chunk = tunnel.recv(4096)
        assert chunk
        handshake += chunk
    assert handshake.startswith(b"HTTP/1.1 200")
    tunnel.sendall(b"raw-tunnel-payload")
    assert tunnel.recv(4096) == b"raw-tunnel-payload"
    tunnel.close()

    live = network.status("fake-traffic")
    assert live["active"] is True
    assert live["domain_count"] == 2
    assert live["record_count"] == 2
    assert live["recent_domains"] == ["127.0.0.1", "localhost"]

    stopped = network.stop("fake-traffic")
    assert stopped["domain_count"] == 2
    path = Path(str(stopped["path"]))
    raw = path.read_text(encoding="utf-8")
    lines = [json.loads(line) for line in raw.splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        assert set(line) == {"ts", "host", "port", "scheme"}
    assert {(line["host"], line["port"], line["scheme"]) for line in lines} == {
        ("localhost", upstream_http, "http"),
        ("127.0.0.1", echo_tcp, "https"),
    }
    # 不落 path/query/header/body（响应正文也不在 JSONL 中）
    for forbidden in ("private", "token", "secret", "X-Secret", "upstream-ok", "raw-tunnel-payload"):
        assert forbidden not in raw
    assert stopped["size"] == path.stat().st_size
    assert stopped["size"] > 0


def test_proxy_forwards_http_body_without_recording(net_env, monkeypatch, upstream_http) -> None:
    _enable()
    runtime = _RunningProxy(monkeypatch, serial="fake-post")

    conn = http.client.HTTPConnection("127.0.0.1", runtime.port, timeout=5)
    conn.request(
        "POST",
        f"http://localhost:{upstream_http}/submit?x=1",
        body=b"request-secret-body",
        headers={"Host": f"localhost:{upstream_http}", "Content-Type": "text/plain"},
    )
    response = conn.getresponse()
    assert response.status == 200
    assert response.read() == b"post:request-secret-body"
    conn.close()

    # 相对形式（仅 Host 头）：同样能转发，且只记录 host
    conn = http.client.HTTPConnection("127.0.0.1", runtime.port, timeout=5)
    conn.request("GET", "/relative?y=2", headers={"Host": f"localhost:{upstream_http}"})
    response = conn.getresponse()
    assert response.status == 200
    assert response.read() == b"upstream-ok"
    conn.close()

    stopped = network.stop("fake-post")
    assert stopped["record_count"] == 2
    assert stopped["recent_domains"] == ["localhost"]
    raw = Path(str(stopped["path"])).read_text(encoding="utf-8")
    for forbidden in ("request-secret-body", "submit", "relative", "x=1", "y=2"):
        assert forbidden not in raw


def test_connect_forwards_payload_buffered_with_handshake(net_env, monkeypatch, echo_tcp) -> None:
    _enable()
    runtime = _RunningProxy(monkeypatch, serial="fake-early")
    tunnel = socket.create_connection(("127.0.0.1", runtime.port), timeout=5)
    # 头与首段字节同包发送：代理必须保留 rfile 中已缓冲的 TLS/协议字节。
    tunnel.sendall(
        f"CONNECT 127.0.0.1:{echo_tcp} HTTP/1.1\r\nHost: 127.0.0.1:{echo_tcp}\r\n\r\n".encode()
        + b"early-bytes"
    )
    received = b""
    while b"\r\n\r\n" not in received:
        chunk = tunnel.recv(4096)
        assert chunk
        received += chunk
    head, _, rest = received.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    while len(rest) < len(b"early-bytes"):
        rest += tunnel.recv(4096)
    assert rest == b"early-bytes"
    tunnel.close()
    network.stop("fake-early")


def test_status_without_capture(net_env) -> None:
    _enable()
    status = network.status("never-started")
    assert status["active"] is False
    assert status["path"] is None
    assert status["domain_count"] == 0
    assert status["recent_domains"] == []
    assert status["port"] == 0  # 测试配置的端口


# ---------------------------------------------------------------- API


def _core_net_enable() -> None:
    config.set_db_overrides({"capture.network.enabled": True, "capture.network.port": 0})


@pytest.fixture()
def core_net(core_client, monkeypatch):
    from app.capture import network as network_module

    stub = _StubAdb(original_proxy="10.1.2.3:8080")
    monkeypatch.setattr(network_module, "_adb_module", lambda: stub)
    yield core_client, stub
    network_module.shutdown_all()
    network_module._ACTIVE.clear()
    network_module._LAST.clear()


def test_api_network_status_requires_enabled(core_client) -> None:
    response = core_client.get("/api/devices/fake-1/network/status")
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "UNSUPPORTED"
    assert error["details"]["setting"] == "capture.network.enabled"


def test_api_network_start_status_stop(core_net) -> None:
    client, _stub = core_net
    _core_net_enable()
    started = client.post("/api/devices/fake-1/network/start")
    assert started.status_code == 200
    body = started.json()
    assert body["ok"] is True
    assert body["started"] is True
    capture = body["capture"]
    assert capture["active"] is True
    assert capture["path"].endswith(".jsonl")
    assert capture["port"] > 0

    status = client.get("/api/devices/fake-1/network/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["ok"] is True
    assert payload["serial"] == "fake-1"
    assert payload["active"] is True
    assert payload["port"] == capture["port"]
    assert payload["path"] == capture["path"]
    assert payload["size"] == 0
    assert payload["domain_count"] == 0
    assert payload["recent_domains"] == []

    stopped = client.post("/api/devices/fake-1/network/stop")
    assert stopped.status_code == 200
    assert stopped.json()["capture"]["active"] is False

    after = client.get("/api/devices/fake-1/network/status")
    assert after.status_code == 200
    assert after.json()["active"] is False
    assert after.json()["path"] == capture["path"]

    again = client.post("/api/devices/fake-1/network/stop")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "NOT_FOUND"


def test_api_network_start_device_failure_is_api_error(core_net, monkeypatch) -> None:
    client, _stub = core_net
    _core_net_enable()
    monkeypatch.setattr(
        network, "_adb_module", lambda: _StubAdb(fail_when=lambda args: "reverse" in args)
    )
    response = client.post("/api/devices/missing-device/network/start")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEVICE_UNAVAILABLE"
