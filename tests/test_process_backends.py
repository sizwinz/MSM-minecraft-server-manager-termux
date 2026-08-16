from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
import psutil
import pytest

from process.base import LaunchSpec
from process.manager import ProcessManager
from process.native_posix import NativePosixBackend
from process.screen import ScreenBackend
from process.state import ProcessState
from process.windows import WindowsBackend


def test_process_state_serialization(tmp_path: Path):
    state_file = tmp_path / ".msm.process.json"
    state = ProcessState(
        schema_version=1,
        pid=12345,
        create_time=time.time(),
        executable="/usr/bin/java",
        cmdline=["/usr/bin/java", "-jar", "server.jar"],
        cmd_fingerprint="java:server.jar",
        cwd=str(tmp_path),
        server_name="test_server",
        instance_id="inst_1",
        backend="native_posix",
    )
    state.save(state_file)
    assert state_file.exists()

    loaded = ProcessState.load(state_file)
    assert loaded is not None
    assert loaded.pid == 12345
    assert loaded.server_name == "test_server"
    assert loaded.backend == "native_posix"


def test_process_state_identity_validation_detects_mismatch(tmp_path: Path):
    # Dead PID
    state = ProcessState(
        schema_version=1,
        pid=99999999,
        create_time=time.time(),
        executable="/usr/bin/java",
        cwd=str(tmp_path),
        server_name="test_server",
    )
    assert state.validate_identity() is False

    # Current process with wrong create_time (simulating PID recycling)
    curr_pid = os.getpid()
    proc = psutil.Process(curr_pid)
    real_time = proc.create_time()

    recycled_state = ProcessState(
        schema_version=1,
        pid=curr_pid,
        create_time=real_time - 1000.0,
        executable="different_exe",
        cwd=str(tmp_path / "other"),
        server_name="test_server",
    )
    assert recycled_state.validate_identity() is False


def test_screen_backend_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "shutil.which", lambda cmd: "/usr/bin/screen" if cmd == "screen" else None
    )
    monkeypatch.setattr(
        "process.screen.run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, stdout="mc_test", stderr=""
        ),
    )
    monkeypatch.setattr(
        "process.screen.wait_for_pid_file", lambda path, timeout_seconds=12: os.getpid()
    )

    server_dir = tmp_path / "server"
    server_dir.mkdir()
    state_file = server_dir / ".msm.process.json"
    pid_file = server_dir / ".msm.pid"
    log_file = server_dir / "logs" / "latest.log"

    backend = ScreenBackend("test", server_dir, state_file, pid_file)
    spec = LaunchSpec(
        server_name="test",
        command=["java", "-jar", "server.jar"],
        cwd=server_dir,
        log_file=log_file,
        state_file=state_file,
        pid_file=pid_file,
        screen_name="mc_test",
    )

    state = backend.start(spec)
    assert state.pid == os.getpid()

    # Mock screen exists
    monkeypatch.setattr(
        "process.screen.screen_session_exists", lambda name, logger=None: True
    )
    assert backend.is_running() is True

    can_attach, attach_cmd = backend.attach()
    assert can_attach is True
    assert "screen -r mc_test" in attach_cmd


def test_native_posix_backend_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    state_file = server_dir / ".msm.process.json"
    pid_file = server_dir / ".msm.pid"
    log_file = server_dir / "logs" / "latest.log"

    backend = NativePosixBackend("test_posix", server_dir, state_file, pid_file)

    # Launch a quick non-blocking helper
    spec = LaunchSpec(
        server_name="test_posix",
        command=["python", "-c", "import time; time.sleep(0.5)"],
        cwd=server_dir,
        log_file=log_file,
        state_file=state_file,
        pid_file=pid_file,
    )

    state = backend.start(spec)
    assert state.pid > 0
    assert backend.is_running() is True

    can_attach, reason = backend.attach()
    assert can_attach is False
    assert "Interactive console attachment is not available" in reason

    stopped = backend.graceful_stop(timeout=2)
    assert stopped is True
    assert backend.is_running() is False


def test_windows_backend_lifecycle(tmp_path: Path):
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    state_file = server_dir / ".msm.process.json"
    pid_file = server_dir / ".msm.pid"
    log_file = server_dir / "logs" / "latest.log"

    backend = WindowsBackend("test_win", server_dir, state_file, pid_file)
    spec = LaunchSpec(
        server_name="test_win",
        command=["python", "-c", "import time; time.sleep(0.5)"],
        cwd=server_dir,
        log_file=log_file,
        state_file=state_file,
        pid_file=pid_file,
    )

    state = backend.start(spec)
    assert state.pid > 0
    assert backend.is_running() is True

    can_attach, reason = backend.attach()
    assert can_attach is False
    assert "Console attachment is not available" in reason

    stopped = backend.terminate(timeout=2)
    assert stopped is True
    assert backend.is_running() is False


def test_process_manager_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mgr = ProcessManager()
    server_dir = tmp_path / "srv"
    server_dir.mkdir()

    # Explicit backend
    win_b = mgr.get_backend("s1", server_dir, {"process_backend": "windows"})
    assert win_b.name in ("windows", "native_posix")

    screen_b = mgr.get_backend("s2", server_dir, {"process_backend": "screen"})
    assert screen_b.name in ("screen", "native_posix", "windows")
