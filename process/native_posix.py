"""Native POSIX process backend implementation for Linux, macOS, WSL, FreeBSD, and Termux."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import psutil

from process.base import LaunchSpec, ProcessBackend
from process.state import ProcessState, create_process_state
from utils.system import write_text_file


class NativePosixBackend(ProcessBackend):
    """Manages headless server process tree using POSIX process groups / sessions."""

    @property
    def name(self) -> str:
        return "native_posix"

    def start(self, spec: LaunchSpec) -> ProcessState:
        spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = spec.log_file.open("a", encoding="utf-8", errors="replace")

        exec_env = os.environ.copy()
        if spec.env:
            exec_env.update(spec.env)

        # Execute in a new session / process group
        proc = subprocess.Popen(
            spec.command,
            cwd=self.server_dir,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            start_new_session=(sys.platform != "win32"),
            env=exec_env,
            close_fds=(sys.platform != "win32"),
        )

        pid = proc.pid
        write_text_file(self.pid_file, str(pid))

        state = create_process_state(
            pid=pid,
            cmdline=spec.command,
            cwd=self.server_dir,
            server_name=self.server_name,
            backend=self.name,
        )
        state.save(self.state_file)
        return state

    def is_running(self) -> bool:
        state = self.read_state()
        return state is not None

    def get_pid(self) -> int | None:
        state = self.read_state()
        return state.pid if state else None

    def send_command(self, command: str) -> bool:
        state = self.read_state()
        if not state:
            return False

        # RCON is the primary reliable channel for headless servers
        return False

    def graceful_stop(self, timeout: int = 20) -> bool:
        if not self.is_running():
            self.clean_stale_state()
            return True

        state = self.read_state()
        if state:
            if hasattr(os, "killpg") and hasattr(os, "getpgid"):
                try:
                    pgid = os.getpgid(state.pid)
                    os.killpg(pgid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass
            else:
                try:
                    os.kill(state.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError, OSError):
                    pass

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running():
                self.clean_stale_state()
                return True
            time.sleep(0.5)

        if not self.is_running():
            self.clean_stale_state()
            return True

        return self.terminate(timeout=5)

    def terminate(self, timeout: int = 5) -> bool:
        state = self.read_state()
        if not state:
            self.clean_stale_state()
            return True

        pid = state.pid
        # Process group termination
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

        try:
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                try:
                    child.terminate()
                except psutil.Error:
                    pass
            proc.terminate()
        except (psutil.Error, OSError):
            pass

        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running():
                self.clean_stale_state()
                return True
            time.sleep(0.25)

        if not self.is_running():
            self.clean_stale_state()
            return True
        return False

    def kill(self) -> bool:
        state = self.read_state()
        if not state:
            self.clean_stale_state()
            return True

        pid = state.pid
        if hasattr(os, "killpg") and hasattr(os, "getpgid"):
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass

        try:
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
            proc.kill()
        except (psutil.Error, OSError):
            pass

        self.clean_stale_state()
        return not self.is_running()

    def attach(self) -> tuple[bool, str]:
        return (
            False,
            "Interactive console attachment is not available with the native POSIX backend. "
            "Use MSM command dispatch, RCON, or view logs/latest.log.",
        )
