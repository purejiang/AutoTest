"""无真机测试：jar 校验 / server 参数 / start_server 全链路（假 adb + 假 scrcpy server TCP）。

假 server 在本机 localhost 监听（代替 adb forward 的隧道），按 raw/meta 协议发送
12 字节头帧；不需要真机、不需要 adb、不联网。
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import struct
import threading
import time
from pathlib import Path

import pytest

from app.android import scrcpy
from app.errors import ApiError
from tests.test_media_h264 import annexb, build_idr, build_pps, build_sps

PORTRAIT_SPS = build_sps(1080, 1920)
PPS = build_pps()
KEYFRAME = annexb(PORTRAIT_SPS, PPS, build_idr())


# ---------------------------------------------------------------- 工具
def make_jar(tmp_path: Path, data: bytes = b"fake-scrcpy-server-jar") -> tuple[Path, Path]:
    jar = tmp_path / scrcpy.JAR_NAME
    jar.write_bytes(data)
    lock = {
        "version": scrcpy.SCRCPY_VERSION,
        "url": "https://example.invalid/scrcpy-server.jar",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "fetched_at": "2026-01-01T00:00:00+00:00",
    }
    lock_path = tmp_path / scrcpy.LOCK_NAME
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return jar, lock_path


class FakeScrcpyServer(threading.Thread):
    """模拟 v5 server：accept video（control=true 时再 accept control），按握手模式发流。"""

    def __init__(
        self,
        port: int,
        mode: str,
        *,
        silent: bool = False,
        expects_control: bool = True,
        width: int = 1080,
        height: int = 1920,
    ) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.mode = mode
        self.silent = silent
        self.expects_control = expects_control
        self.width = width
        self.height = height
        self.stop_event = threading.Event()
        self.connections: list[socket.socket] = []
        self.error: Exception | None = None

    def run(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", self.port))
            listener.listen(4)
            listener.settimeout(0.2)
            while not self.stop_event.is_set():
                try:
                    conn, _addr = listener.accept()
                except TimeoutError:
                    continue
                self.connections.append(conn)
                if len(self.connections) == 1:
                    threading.Thread(
                        target=_send_stream,
                        args=(conn, self.mode, self.width, self.height),
                        kwargs={"silent": self.silent, "stop_event": self.stop_event},
                        daemon=True,
                    ).start()
                else:
                    threading.Thread(target=self._hold_control, args=(conn,), daemon=True).start()
        except Exception as exc:  # noqa: BLE001 - 测试线程失败在断言里暴露
            self.error = exc
        finally:
            for conn in self.connections:
                try:
                    conn.close()
                except OSError:
                    pass
            listener.close()

    def _hold_control(self, conn: socket.socket) -> None:
        """control socket：保持打开（真机 server 只在关闭时报错）。"""
        try:
            while not self.stop_event.is_set():
                try:
                    data = conn.recv(4096)
                except TimeoutError:
                    continue
                if not data:
                    return
                self.control_bytes = getattr(self, "control_bytes", b"") + data
        except OSError:
            return

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=2)


def _frame_with_flags(flags: int, pts: int, payload: bytes) -> bytes:
    return struct.pack(">QI", flags | pts, len(payload)) + payload


def _send_stream(
    conn: socket.socket,
    mode: str,
    width: int,
    height: int,
    *,
    silent: bool,
    stop_event: threading.Event,
) -> None:
    """按 v5 协议模拟 server：dummy → 64B 设备名 → codec id → session meta → config → keyframe → 帧。"""
    if silent:
        while not stop_event.is_set():
            time.sleep(0.02)
        return

    def _send(data: bytes) -> None:
        conn.sendall(data)

    try:
        if mode in ("full", "meta_dummy"):
            _send(b"\x00")  # send_dummy_byte
        if mode != "raw":
            _send(b"fake-device".ljust(scrcpy.DEVICE_NAME_FIELD_LENGTH, b"\x00"))
            _send(struct.pack(">I", int.from_bytes(b"h264", "big")))
            # session meta 伪包头：u64 = PACKET_FLAG_SESSION|u32 flags, u32 = height
            _send(struct.pack(">III", 0x80000000, width, height))
        # codec config 包（SPS/PPS，flag=CONFIG，pts=0）
        _send(_frame_with_flags(scrcpy.PACKET_FLAG_CONFIG, 0, annexb(PORTRAIT_SPS, PPS)))
        # 第一个真实帧：keyframe flag + IDR
        _send(_frame_with_flags(scrcpy.PACKET_FLAG_KEY_FRAME, 0, KEYFRAME))
        pts = 30_000
        while not stop_event.is_set():
            time.sleep(0.02)
            _send(scrcpy.pack_frame(pts, annexb(b"\x41\x01\x02\x03")))
            pts += 30_000
    except OSError:
        return


class FakeScrcpyConnector(threading.Thread):
    """reverse 模式的 server 端替身：主动连接 host 监听端口并发送流。"""

    def __init__(self, port: int, mode: str = "raw", *, silent: bool = False) -> None:
        super().__init__(daemon=True)
        self.port = port
        self.mode = mode
        self.silent = silent
        self.stop_event = threading.Event()
        self.conn: socket.socket | None = None

    def run(self) -> None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not self.stop_event.is_set():
            try:
                self.conn = socket.create_connection(("127.0.0.1", self.port), timeout=1.0)
                break
            except OSError:
                time.sleep(0.02)
        if self.conn is None:
            return
        if self.mode == "raw":
            _send_stream(self.conn, "raw", 1080, 1920, silent=self.silent, stop_event=self.stop_event)
        try:
            self.conn.close()
        except OSError:
            pass

    def stop(self) -> None:
        self.stop_event.set()
        self.join(timeout=2)


class FakeProcess:
    def __init__(self, thread: FakeScrcpyServer | None = None, *, dead: bool = False, stderr_text: str = "") -> None:
        self.thread = thread
        self.dead = dead
        self.stderr_text = stderr_text
        self.terminated = False

    def poll(self):
        if self.dead:
            return 1
        if self.thread is None:
            return 1
        return None if self.thread.is_alive() else 0

    def terminate(self) -> None:
        self.terminated = True
        if self.thread is not None:
            self.thread.stop()

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout=None):
        if self.thread is not None:
            self.thread.join(timeout)
        return 0


class FakeAdbOps:
    """adbd 替身：forward 只记录端口，start_process 在本机起假 server；模拟 ls -l 远端 jar。"""

    def __init__(self, *, reject_full: bool = False, silent: bool = False) -> None:
        self.reject_full = reject_full
        self.silent = silent
        self.push_calls: list[tuple[str, str]] = []
        self.forward_calls: list[tuple[str, str]] = []
        self.removed_forwards: list[str] = []
        self.reverse_calls: list[tuple[str, str]] = []
        self.commands: list[str] = []
        self.processes: list[FakeProcess] = []
        self.shell_calls: list[str] = []
        self.port: int | None = None
        self.remote_jar_size: int | None = None
        self.devices = [{"serial": "fake-1", "state": "device"}]

    # ---- 契约 §14.1 形状
    def push(self, local: Path, remote: str) -> None:
        self.push_calls.append((str(local), remote))
        try:
            self.remote_jar_size = Path(local).stat().st_size
        except OSError:
            self.remote_jar_size = 0

    def forward(self, local: str, remote: str) -> None:
        self.forward_calls.append((local, remote))
        self.port = int(local.split(":")[1])

    def remove_forward(self, local: str) -> None:
        self.removed_forwards.append(local)

    def reverse(self, remote: str, local: str) -> None:
        self.reverse_calls.append((remote, local))

    def remove_reverse(self, remote: str) -> None:
        pass

    def list_devices(self):
        return [dict(item) for item in self.devices]

    def shell_text(self, command: str, timeout: float = 10.0) -> str:
        self.shell_calls.append(command)
        if command.startswith("ls -l"):
            if self.remote_jar_size is None:
                return f"ls: {scrcpy.REMOTE_JAR_PATH}: No such file or directory\n"
            return (
                f"-rw-r--r-- 1 shell shell {self.remote_jar_size} 2026-11-20 08:26 "
                f"{scrcpy.REMOTE_JAR_PATH}\n"
            )
        if "logcat -b crash" in command:
            return "--------- beginning of crash\n"
        if "ps -A -o PID,ARGS" in command:
            scid_hex = "00000000"
            if self.commands:
                match = re.search(r"scid=([0-9a-fA-F]+)", self.commands[-1])
                if match:
                    scid_hex = match.group(1)
            return (
                "12345 com.genymobile.scrcpy.Server 5.0.1 scid="
                f"{scid_hex}\n"
            )
        if "/proc/net/unix" in command:
            scid_hex = "00000000"
            if self.commands:
                match = re.search(r"scid=([0-9a-fA-F]+)", self.commands[-1])
                if match:
                    scid_hex = match.group(1)
            return (
                "0000000000000000: 00000002 00000000 00010000 0001 01 12079724 "
                f"@scrcpy_{scid_hex}\n"
            )
        return "Physical size: 1080x1920\n"

    def start_process(self, command: str) -> FakeProcess:
        self.commands.append(command)
        if self.reject_full and "send_dummy_byte=true" in command:
            proc = FakeProcess(dead=True, stderr_text="java.lang.IllegalArgumentException: rejected full handshake")
            self.processes.append(proc)
            return proc
        if "send_device_meta=false" in command:
            mode = "raw"
        elif "send_dummy_byte=true" in command:
            mode = "full"
        else:
            mode = "meta"
        expects_control = "control=true" in command
        assert self.port is not None, "forward() must be called before start_process()"
        thread = FakeScrcpyServer(
            self.port,
            mode,
            silent=self.silent,
            expects_control=expects_control,
        )
        thread.start()
        proc = FakeProcess(thread)
        self.processes.append(proc)
        return proc


def make_cfg(tmp_path: Path, **overrides) -> scrcpy.ServerConfig:
    jar, _lock = make_jar(tmp_path)
    values = dict(
        jar_path=jar,
        max_size=800,
        max_fps=30,
        video_bit_rate=4_000_000,
        stream_mode="raw",
        connect_timeout_s=3.0,
        handshake_timeout_s=1.0,
        first_frame_timeout_s=2.0,
    )
    values.update(overrides)
    return scrcpy.ServerConfig(**values)


# ---------------------------------------------------------------- verify_jar
def test_verify_jar_ok(tmp_path) -> None:
    jar, lock = make_jar(tmp_path)
    info = scrcpy.verify_jar(scrcpy.ServerConfig(jar_path=jar))
    assert info["version"] == scrcpy.SCRCPY_VERSION
    assert info["sha256"] == hashlib.sha256(b"fake-scrcpy-server-jar").hexdigest()
    assert info["size"] == len(b"fake-scrcpy-server-jar")
    assert lock.exists()


def test_verify_jar_missing_jar(tmp_path) -> None:
    cfg = scrcpy.ServerConfig(jar_path=tmp_path / "nope.jar")
    with pytest.raises(ApiError) as exc:
        scrcpy.verify_jar(cfg)
    assert exc.value.code == "SCRCPY_FAILED"
    assert "未就位" in exc.value.message
    assert exc.value.status_code == 502


def test_verify_jar_missing_lock(tmp_path) -> None:
    jar = tmp_path / scrcpy.JAR_NAME
    jar.write_bytes(b"data")
    with pytest.raises(ApiError) as exc:
        scrcpy.verify_jar(scrcpy.ServerConfig(jar_path=jar))
    assert exc.value.code == "SCRCPY_FAILED"
    assert "lock" in exc.value.message.lower()


def test_verify_jar_sha_mismatch(tmp_path) -> None:
    jar, lock = make_jar(tmp_path)
    jar.write_bytes(b"tampered")
    with pytest.raises(ApiError) as exc:
        scrcpy.verify_jar(scrcpy.ServerConfig(jar_path=jar))
    assert exc.value.code == "SCRCPY_FAILED"
    assert "校验失败" in exc.value.message
    assert exc.value.details["expected_sha256"] != exc.value.details["actual_sha256"]


def test_verify_jar_env_override(tmp_path, monkeypatch) -> None:
    jar, _ = make_jar(tmp_path)
    monkeypatch.setenv("AUTOTEST_SCRCPY_JAR", str(jar))
    info = scrcpy.verify_jar()
    assert info["jar"] == str(jar)


# ---------------------------------------------------------------- 参数构造
def test_build_server_args_contract(tmp_path) -> None:
    cfg = make_cfg(tmp_path, control=True)
    args = scrcpy.build_server_args(cfg, "raw")
    assert args[0] == scrcpy.SCRCPY_VERSION
    joined = " ".join(args)
    for expected in (
        "audio=false",
        "video=true",
        "video_codec=h264",
        "log_level=info",
        "max_size=800",
        "max_fps=30",
        "video_bit_rate=4000000",
        "tunnel_forward=true",
        "control=true",
        f"scid={int(cfg.scid) & 0x7FFFFFFF:08x}",
        "send_frame_meta=true",
        "send_device_meta=false",
        "send_dummy_byte=false",
        "send_stream_meta=false",
    ):
        assert expected in joined
    assert "send_codec_meta" not in joined  # v5 不存在该参数
    assert "socket=" not in joined  # v5 用 scid 派生 localabstract 名
    command = scrcpy.build_server_command(cfg, "raw")
    assert command.startswith(f"CLASSPATH={scrcpy.REMOTE_JAR_PATH} app_process / ")
    assert scrcpy.SERVER_CLASS in command
    assert command.endswith(" ".join(args))


def test_build_server_args_full_handshake(tmp_path) -> None:
    """v5 官方默认：dummy + device meta + codec/session meta 全开。"""
    cfg = make_cfg(tmp_path)
    joined = " ".join(scrcpy.build_server_args(cfg, "full"))
    assert "send_device_meta=true" in joined
    assert "send_dummy_byte=true" in joined
    assert "send_stream_meta=true" in joined
    assert "send_frame_meta=true" in joined


def test_max_fps_zero_means_unlimited(tmp_path, monkeypatch) -> None:
    """0 = 不限帧率（scrcpy 默认语义）：合法、透传给 server、from_settings 不会被 ``or`` 吞掉。"""
    jar, _ = make_jar(tmp_path)
    cfg = scrcpy.ServerConfig(jar_path=jar, max_fps=0)
    assert cfg.max_fps == 0
    assert "max_fps=0" in " ".join(scrcpy.build_server_args(cfg, "raw"))

    from app import config

    monkeypatch.setattr(config, "_DB_LAYER", {"scrcpy.max_fps": 0})
    monkeypatch.setattr(config, "_EFFECTIVE", None)
    assert scrcpy.ServerConfig.from_settings().max_fps == 0
    # 未配置时默认 0 = 不限（与 scrcpy --max-fps 默认一致）
    monkeypatch.setattr(config, "_DB_LAYER", {})
    monkeypatch.setattr(config, "_EFFECTIVE", None)
    assert scrcpy.ServerConfig.from_settings().max_fps == 0


def test_split_nals_matches_byte_scan_and_handles_keyframes() -> None:
    """split_nals 改用 bytes.find 后必须与逐字节扫描结果一致（含 4 字节起始码、尾部零、无起始码）。"""
    def byte_scan(data: bytes) -> list[bytes]:
        nals: list[bytes] = []
        n = len(data)
        i = 0
        start = -1
        while i + 3 <= n:
            if data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1:
                if start >= 0:
                    end = i
                    while end > start and data[end - 1] == 0:
                        end -= 1
                    if end > start:
                        nals.append(data[start:end])
                start = i + 3
                i += 3
            else:
                i += 1
        if start >= 0:
            end = n
            while end > start and data[end - 1] == 0:
                end -= 1
            if end > start:
                nals.append(data[start:end])
        return nals

    sps, pps = bytes(PORTRAIT_SPS), bytes(PPS)
    sc3 = scrcpy.START_CODE_3
    idr_nal = build_idr()
    slice_payload = bytes([0x41, 0x9A, 0x12]) + bytes(range(1, 200)) * 40  # 不含起始码的 P 帧 slice
    samples = {
        "p-frame": sc3 + slice_payload,
        "idr-4byte-codes": b"\x00\x00\x00\x01" + sps + b"\x00\x00\x00\x01" + pps + b"\x00\x00\x00\x01" + idr_nal,
        "idr-mixed-codes": sc3 + sps + b"\x00\x00\x00\x01" + pps + sc3 + idr_nal + b"\x00\x00",
        "leading-zeros": b"\x00\x00\x00\x00" + sc3 + sps,
        "no-start-code": slice_payload,
        "empty": b"",
    }
    for name, payload in samples.items():
        assert scrcpy.split_nals(payload) == byte_scan(payload), name

    idr = scrcpy.split_nals(samples["idr-4byte-codes"])
    assert [scrcpy.nal_type(nal) for nal in idr] == [7, 8, 5]
    assert idr == [sps, pps, idr_nal]
    assert scrcpy.split_nals(samples["p-frame"]) == [slice_payload]
    assert scrcpy.split_nals(samples["no-start-code"]) == []


def test_build_server_args_includes_crop_and_control(tmp_path) -> None:
    cfg = make_cfg(tmp_path, crop="800:1600:100:200", control=True)
    joined = " ".join(scrcpy.build_server_args(cfg, "meta"))
    assert "crop=800:1600:100:200" in joined
    assert "control=true" in joined
    assert "send_device_meta=true" in joined
    assert "send_dummy_byte=false" in joined
    assert "send_stream_meta=true" in joined


def test_device_socket_name_derived_from_scid(tmp_path) -> None:
    jar, _ = make_jar(tmp_path)
    cfg = scrcpy.ServerConfig(jar_path=jar, scid=0x1234ABCD)
    assert cfg.device_socket_name == "scrcpy_1234abcd"
    assert scrcpy.ServerConfig(jar_path=jar, scid=-1).device_socket_name == "scrcpy"
    assert scrcpy.ServerConfig(jar_path=jar).device_socket_name.startswith("scrcpy_")
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, scid=-2)
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, scid=0x80000000)


def test_socket_name_is_unique_and_prefixed() -> None:
    first = scrcpy.default_socket_name()
    second = scrcpy.default_socket_name()
    assert first.startswith(scrcpy.SOCKET_PREFIX)
    assert first != second
    assert str(__import__("os").getpid()) in first


def test_resolved_max_size_uses_device_long_edge_and_cap() -> None:
    # 均衡：85% 长边，上限 1920
    cfg = scrcpy.ServerConfig(scale_pct=85, max_size=1920)
    assert cfg.resolved_max_size(1080, 2400) == 1920  # 2040 -> 上限
    assert cfg.resolved_max_size(1080, 1920) == 1632
    assert cfg.resolved_max_size(720, 1600) == 1360
    assert cfg.resolved_max_size(1440, 3200) == 1920  # 2720 -> 上限
    # 清晰：100% 原生，但受 2560 上限
    sharp = scrcpy.ServerConfig(scale_pct=100, max_size=4096)
    assert sharp.resolved_max_size(1080, 2400) == 2400
    assert sharp.resolved_max_size(1440, 3200) == 3200
    # 100% 忽略上限：同一设备把上限压到 1920 也按原生 2400 推流
    capped = scrcpy.ServerConfig(scale_pct=100, max_size=1920)
    assert capped.resolved_max_size(1080, 2400) == 2400
    # 不放大：设备长边低于上限时保持原生
    assert sharp.resolved_max_size(720, 1280) == 1280
    # 未知尺寸回退到绝对上限
    assert sharp.resolved_max_size(0, 0) == 4096
    # 偶数长边
    odd = scrcpy.ServerConfig(scale_pct=85, max_size=1920)
    assert odd.resolved_max_size(1080, 1989) % 2 == 0


def test_server_config_validation(tmp_path) -> None:
    jar, _ = make_jar(tmp_path)
    with pytest.raises(ApiError) as exc:
        scrcpy.ServerConfig(jar_path=jar, max_fps=-1)
    assert exc.value.code == "INVALID_ARGUMENT"
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, max_fps=241)
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, crop="1:2:3")
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, codec="h265")
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, socket_name="bad name!")
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, stream_mode="weird")
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, scale_pct=40)
    with pytest.raises(ApiError):
        scrcpy.ServerConfig(jar_path=jar, scale_pct=101)


# ---------------------------------------------------------------- ensure_device_online
def test_ensure_device_online_ok() -> None:
    ops = FakeAdbOps()
    info = scrcpy.ensure_device_online("fake-1", ops)
    assert info["serial"] == "fake-1"


def test_ensure_device_online_missing() -> None:
    ops = FakeAdbOps()
    with pytest.raises(ApiError) as exc:
        scrcpy.ensure_device_online("other", ops)
    assert exc.value.code == "DEVICE_UNAVAILABLE"
    assert exc.value.status_code == 503


def test_ensure_device_online_offline() -> None:
    ops = FakeAdbOps()
    ops.devices = [{"serial": "fake-1", "state": "offline"}]
    with pytest.raises(ApiError) as exc:
        scrcpy.ensure_device_online("fake-1", ops)
    assert exc.value.code == "DEVICE_UNAVAILABLE"


# ---------------------------------------------------------------- start_server 全链路
def test_start_server_raw_mode(tmp_path) -> None:
    cfg = make_cfg(tmp_path, control=True)
    ops = FakeAdbOps()
    server = scrcpy.start_server("fake-1", cfg, ops=ops)
    try:
        assert server.stream_mode == "raw"
        assert server.process_alive()
        assert (server.geometry.width, server.geometry.height) == (1080, 1920)
        assert server.geometry.codec == "avc1.64001F"
        assert server.geometry.sps == PORTRAIT_SPS
        assert server.geometry.pps == PPS
        first_pts, first_payload = server.first_frame
        assert first_pts == 0
        assert first_payload == KEYFRAME
        # 每次 start 前检查远端 jar（首次缺失 → push 一次）
        assert any(cmd.startswith("ls -l") for cmd in ops.shell_calls)
        assert ops.push_calls[0] == (str(cfg.jar_path), scrcpy.REMOTE_JAR_PATH)
        assert ops.forward_calls[0] == (f"tcp:{server.local_port}", f"localabstract:{cfg.device_socket_name}")
        assert cfg.device_socket_name.startswith("scrcpy_")
        # server 参数（v5 合法性）
        command = ops.commands[0]
        assert "audio=false" in command and "video=true" in command
        assert f"scid={int(cfg.scid) & 0x7FFFFFFF:08x}" in command
        assert "socket=" not in command
        assert "send_codec_meta" not in command
        # control socket 已连接 → 可以请求 keyframe
        assert server.request_keyframe() is True
    finally:
        server.stop()
    assert ops.removed_forwards == [f"tcp:{server.local_port}"]
    assert not server.process_alive()
    scid_tag = f"scid={int(cfg.scid) & 0x7FFFFFFF:08x}"
    killed = [cmd for cmd in ops.shell_calls if cmd.startswith("kill -9 ")]
    assert killed == ["kill -9 12345"], (scid_tag, ops.shell_calls)


def test_start_server_full_handshake_mode(tmp_path) -> None:
    """v5 官方握手：session meta 提供尺寸，config 包提供 SPS/PPS。"""
    cfg = make_cfg(tmp_path, stream_mode="full")
    ops = FakeAdbOps()
    server = scrcpy.start_server("fake-1", cfg, ops=ops)
    try:
        assert server.stream_mode == "full"
        assert (server.geometry.width, server.geometry.height) == (1080, 1920)
        assert server.geometry.codec == "avc1.64001F"
        assert server.geometry.sps == PORTRAIT_SPS
        # control 已连：第二个 accept 成功
        assert server.control_sock is not None
    finally:
        server.stop()


def test_start_server_pushes_remote_jar_when_size_mismatch(tmp_path) -> None:
    cfg = make_cfg(tmp_path)
    ops = FakeAdbOps()
    ops.remote_jar_size = 123  # 远端残留旧 jar
    server = scrcpy.start_server("fake-1", cfg, ops=ops)
    try:
        assert len(ops.push_calls) == 1
        assert ops.remote_jar_size == cfg.jar_path.stat().st_size
    finally:
        server.stop()


def test_start_server_falls_back_to_raw_mode(tmp_path) -> None:
    cfg = make_cfg(tmp_path, stream_mode="auto")
    ops = FakeAdbOps(reject_full=True)
    server = scrcpy.start_server("fake-1", cfg, ops=ops)
    try:
        assert server.stream_mode == "raw"
        assert (server.geometry.width, server.geometry.height) == (1080, 1920)
        assert len(ops.commands) == 2
        assert "send_dummy_byte=true" in ops.commands[0]  # 第一次是 full
        assert "send_device_meta=false" in ops.commands[1]  # 第二次退回 raw
        # 第一次失败的 forward 也被清理
        assert len(ops.removed_forwards) == 1
    finally:
        server.stop()
    assert len(ops.removed_forwards) == 2


def test_start_server_first_frame_timeout_cleans_up(tmp_path) -> None:
    cfg = make_cfg(tmp_path, stream_mode="raw", first_frame_timeout_s=0.2)
    ops = FakeAdbOps(silent=True)
    with pytest.raises(ApiError) as exc:
        scrcpy.start_server("fake-1", cfg, ops=ops)
    assert exc.value.code == "SCRCPY_FAILED"
    assert "首帧超时" in exc.value.message
    assert exc.value.details["command"]
    assert exc.value.details["scid"] == cfg.scid
    assert "server_log_tail" in exc.value.details
    assert "adb_stderr" in exc.value.details
    # 清理：进程被 terminate、forward 被 remove、没有登记活跃 server
    assert all(proc.terminated for proc in ops.processes)
    assert ops.removed_forwards
    assert scrcpy.active_servers() == []


def test_start_server_all_modes_fail(tmp_path) -> None:
    cfg = make_cfg(tmp_path, stream_mode="auto", first_frame_timeout_s=0.15)
    ops = FakeAdbOps(silent=True)
    with pytest.raises(ApiError) as exc:
        scrcpy.start_server("fake-1", cfg, ops=ops)
    assert exc.value.code == "SCRCPY_FAILED"
    assert len(exc.value.details["attempts"]) == 2
    assert [item["mode"] for item in exc.value.details["attempts"]] == ["full", "raw"]


def test_start_server_requires_jar(tmp_path) -> None:
    cfg = scrcpy.ServerConfig(jar_path=tmp_path / "missing.jar")
    with pytest.raises(ApiError) as exc:
        scrcpy.start_server("fake-1", cfg, ops=FakeAdbOps())
    assert exc.value.code == "SCRCPY_FAILED"


def test_stop_server_idempotent(tmp_path) -> None:
    cfg = make_cfg(tmp_path)
    server = scrcpy.start_server("fake-1", cfg, ops=FakeAdbOps())
    server.stop()
    server.stop()  # 幂等
    assert scrcpy.active_servers() == []


def test_reverse_mode_accepts_socket(tmp_path) -> None:
    cfg = make_cfg(tmp_path, tunnel_forward=False, stream_mode="raw", control=False)
    ops = FakeAdbOps()
    # reverse 模式下 server 主动连本机端口：用假 server 监听该端口
    original_reverse = ops.reverse

    def reverse(remote: str, local: str) -> None:
        original_reverse(remote, local)
        port = int(local.split(":")[1])
        thread = FakeScrcpyConnector(port, "raw")
        thread.start()
        ops._reverse_thread = thread  # type: ignore[attr-defined]

    ops.reverse = reverse  # type: ignore[method-assign]

    def start_process(command: str) -> FakeProcess:
        ops.commands.append(command)
        return FakeProcess(ops._reverse_thread)  # type: ignore[attr-defined]

    ops.start_process = start_process  # type: ignore[method-assign]
    server = scrcpy.start_server("fake-1", cfg, ops=ops)
    try:
        assert server.stream_mode == "raw"
        assert ops.reverse_calls
        assert (server.geometry.width, server.geometry.height) == (1080, 1920)
    finally:
        server.stop()
        ops._reverse_thread.stop()  # type: ignore[attr-defined]
