"""GNU Screen process backend implementation."""

from __future__ import annotations

import shutil
import time

import psutil

from process.base import LaunchSpec, ProcessBackend
from process.state import ProcessState, create_process_state
from utils.system import (
    build_screen_launch_command,
    run_command,
    screen_session_exists,
    wait_for_pid_file,
)


class ScreenBackend(ProcessBackend):
    """Manages server process lifecycle inside a GNU Screen session."""

    @property
    def name(self) -> str:
        return "screen"

    def _get_screen_name(self, spec: LaunchSpec | None = None) -> str:
        if spec and spec.screen_name:
            return spec.screen_name
        from utils.system import get_screen_name

        return get_screen_name(self.server_name)

    def start(self, spec: LaunchSpec) -> ProcessState:
        if shutil.which("screen") is None:
            raise RuntimeError("GNU Screen is not installed or not available on PATH.")

        screen_name = self._get_screen_name(spec)
        spec.log_file.parent.mkdir(parents=True, exist_ok=True)

        launch_command = build_screen_launch_command(
            screen_name,
            spec.command,
            self.pid_file,
            startup_log=spec.log_file,
        )

        started = run_command(
            launch_command,
            logger=self.logger,
            cwd=self.server_dir,
            env=spec.env,
        )
        if started is None or (
            hasattr(started, "returncode") and started.returncode != 0
        ):
            raise RuntimeError(
                f"Failed to launch screen session for {self.server_name}."
            )

        pid = wait_for_pid_file(self.pid_file, timeout_seconds=12)
        if not pid:
            raise RuntimeError(
                f"Failed to acquire PID for {self.server_name} in screen."
            )

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
        if state:
            return True
        screen_name = self._get_screen_name()
        return screen_session_exists(screen_name, logger=self.logger)

    def get_pid(self) -> int | None:
        state = self.read_state()
        return state.pid if state else None

    def send_command(self, command: str) -> bool:
        # Try RCON first if available
        state = self.read_state()
        if not state:
            return False

        screen_name = self._get_screen_name()
        res = run_command(
            [
                "screen",
                "-S",
                screen_name,
                "-p",
                "0",
                "-X",
                "stuff",
                f"{command}\n",
            ],
            logger=self.logger,
            check=False,
        )
        return res is not None and res.returncode == 0

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
        screen_name = self._get_screen_name()
        run_command(
            ["screen", "-S", screen_name, "-X", "quit"],
            logger=self.logger,
            check=False,
        )

        pid = self.get_pid()
        if pid:
            try:
                proc = psutil.Process(pid)
                # Terminate children first
                for child in proc.children(recursive=True):
                    try:
                        child.terminate()
                    except psutil.Error:
                        pass
                proc.terminate()
                proc.wait(timeout=timeout)
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
        screen_name = self._get_screen_name()
        run_command(
            ["screen", "-S", screen_name, "-X", "quit"],
            logger=self.logger,
            check=False,
        )

        pid = self.get_pid()
        if pid:
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
        screen_name = self._get_screen_name()
        if not self.is_running():
            return False, f"Server {self.server_name} is not running."
        return True, f"screen -r {screen_name}"
