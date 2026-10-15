from __future__ import annotations

import os
import subprocess
import sys
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


def test_process_state_identity_validation_detects_mismatch(
    tmp_path: Path, monkeypatch
):
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
    try:
        real_time = proc.create_time()
    except psutil.AccessDenied:
        real_time = time.time()
    monkeypatch.setattr(psutil.Process, "create_time", lambda self: real_time)

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


@pytest.mark.skipif(
    os.name == "nt", reason="Native POSIX backend lifecycle requires POSIX OS"
)
def test_native_posix_backend_lifecycle(tmp_path: Path):
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    state_file = server_dir / ".msm.process.json"
    pid_file = server_dir / ".msm.pid"
    log_file = server_dir / "logs" / "latest.log"

    backend = NativePosixBackend("test_posix", server_dir, state_file, pid_file)

    # Launch using sys.executable with adequate sleep for lifecycle check
    spec = LaunchSpec(
        server_name="test_posix",
        command=[sys.executable, "-c", "import time; time.sleep(10)"],
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

    stopped = backend.graceful_stop(timeout=5)
    assert stopped is True
    assert backend.is_running() is False


@pytest.mark.skipif(
    os.name != "nt", reason="Windows backend lifecycle requires Windows OS"
)
def test_windows_backend_lifecycle(tmp_path: Path):
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    state_file = server_dir / ".msm.process.json"
    pid_file = server_dir / ".msm.pid"
    log_file = server_dir / "logs" / "latest.log"

    backend = WindowsBackend("test_win", server_dir, state_file, pid_file)
    spec = LaunchSpec(
        server_name="test_win",
        command=[sys.executable, "-c", "import time; time.sleep(10)"],
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

    stopped = backend.terminate(timeout=5)
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


def test_invalid_process_identity_never_falls_back_to_legacy_pid(tmp_path):
    backend = NativePosixBackend("test", tmp_path, tmp_path / "state", tmp_path / "pid")
    backend.pid_file.write_text(str(os.getpid()))
    ProcessState(
        pid=os.getpid(),
        create_time=psutil.Process().create_time() - 1,
        cwd=str(Path.cwd()),
        executable=sys.executable,
    ).save(backend.state_file)
    assert backend.read_state() is None
    backend.state_file.write_text('{"pid": null}')
    assert backend.read_state() is None


def test_windows_never_selects_screen_from_git_path(tmp_path, monkeypatch):
    monkeypatch.setattr("process.manager.sys.platform", "win32")
    monkeypatch.setattr("process.manager.shutil.which", lambda _: "screen.exe")
    backend = ProcessManager().get_backend(
        "test", tmp_path, {"process_backend": "screen"}
    )
    assert backend.name == "windows"
    assert (
        ProcessManager()
        .get_backend("test", tmp_path, {"process_backend": "invalid"})
        .name
        == "windows"
    )


@pytest.mark.parametrize("operation", ["terminate", "kill"])
def test_native_backend_never_signals_an_unowned_process_group(
    tmp_path, monkeypatch, operation
):
    from unittest.mock import MagicMock

    backend = NativePosixBackend("test", tmp_path, tmp_path / "state", tmp_path / "pid")
    monkeypatch.setattr(backend, "read_state", lambda: ProcessState(pid=12345))
    monkeypatch.setattr(backend, "is_running", lambda: False)
    monkeypatch.setattr("process.native_posix.psutil.Process", MagicMock())
    monkeypatch.setattr(
        "process.native_posix.os.getpgid", lambda _: 9999, raising=False
    )
    kill_group = MagicMock()
    monkeypatch.setattr("process.native_posix.os.killpg", kill_group, raising=False)
    if operation == "terminate":
        backend.terminate(timeout=0)
    else:
        backend.kill()
    kill_group.assert_not_called()


def test_native_backend_stops_parent_and_child_in_directory_with_spaces(tmp_path):
    server_dir = tmp_path / "server with spaces"
    server_dir.mkdir()
    backend_class = WindowsBackend if os.name == "nt" else NativePosixBackend
    backend = backend_class(
        "tree", server_dir, server_dir / "state", server_dir / "pid"
    )
    child_file = server_dir / "child.pid"
    child = None
    spec = LaunchSpec(
        server_name="tree",
        command=[
            sys.executable,
            "-c",
            "import pathlib, subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "pathlib.Path('child.pid').write_text(str(child.pid)); time.sleep(60)",
        ],
        cwd=server_dir,
        log_file=server_dir / "logs/latest.log",
        state_file=backend.state_file,
        pid_file=backend.pid_file,
    )
    try:
        state = backend.start(spec)
        deadline = time.monotonic() + 5
        while not child_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert child_file.exists()
        child = psutil.Process(int(child_file.read_text()))
        assert backend.is_running()
        assert backend.terminate(timeout=5)
        assert not psutil.pid_exists(state.pid)
        deadline = time.monotonic() + 5
        while child.is_running() and time.monotonic() < deadline:
            if child.status() == psutil.STATUS_ZOMBIE:
                break
            time.sleep(0.05)
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        backend.kill()
        if child is not None:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
