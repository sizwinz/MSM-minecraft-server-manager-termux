"""Windows process backend implementation with job objects and process tree management."""

from __future__ import annotations

import os
import signal
import subprocess
import time

import psutil

from process.base import LaunchSpec, ProcessBackend
from process.state import ProcessState, create_process_state
from utils.system import write_text_file


class WindowsBackend(ProcessBackend):
    """Manages Windows process tree using process groups and psutil tree traversal."""

    @property
    def name(self) -> str:
        return "windows"

    def start(self, spec: LaunchSpec) -> ProcessState:
        spec.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_handle = spec.log_file.open("a", encoding="utf-8", errors="replace")

        exec_env = os.environ.copy()
        if spec.env:
            exec_env.update(spec.env)

        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS

        proc = subprocess.Popen(
            spec.command,
            cwd=self.server_dir,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            creationflags=creationflags,
            env=exec_env,
            close_fds=False,
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
        return False

    def graceful_stop(self, timeout: int = 20) -> bool:
        if not self.is_running():
            self.clean_stale_state()
            return True

        self.send_command("stop")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self.is_running():
                self.clean_stale_state()
                return True
            time.sleep(0.5)

        return not self.is_running()

    def terminate(self, timeout: int = 5) -> bool:
        state = self.read_state()
        if not state:
            self.clean_stale_state()
            return True

        pid = state.pid
        try:
            # Try CTRL_BREAK_EVENT first if process group exists
            if hasattr(signal, "CTRL_BREAK_EVENT"):
                try:
                    os.kill(pid, signal.CTRL_BREAK_EVENT)
                except (OSError, ValueError):
                    pass

            proc = psutil.Process(pid)
            children = proc.children(recursive=True)
            for child in children:
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
        try:
            proc = psutil.Process(pid)
            children = proc.children(recursive=True)
            for child in children:
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
            "Console attachment is not available on Windows native backend. "
            "Use MSM command dispatch, RCON, or view logs/latest.log.",
        )
