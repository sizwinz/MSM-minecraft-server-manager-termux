"""Process manager factory for resolving platform process backends."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from platforms.detector import detect_platform
from process.base import ProcessBackend
from process.native_posix import NativePosixBackend
from process.screen import ScreenBackend
from process.windows import WindowsBackend


class ProcessManager:
    """Factory and registry for resolving appropriate process backend instances."""

    def __init__(self, logger=None):
        self.logger = logger
        self.platform = detect_platform()

    def get_backend(
        self,
        server_name: str,
        server_dir: Path,
        server_config: dict[str, Any] | None = None,
    ) -> ProcessBackend:
        """Resolve and instantiate the appropriate ProcessBackend for the server."""
        cfg = server_config or {}
        requested = (cfg.get("process_backend") or "auto").lower()

        state_file = server_dir / ".msm.process.json"
        pid_file = server_dir / ".msm.pid"

        if requested == "auto" or not requested:
            backend_type = self.platform.capabilities.default_backend
        else:
            backend_type = requested

        # Platform compatibility fallback checks
        if backend_type == "windows" and sys.platform != "win32":
            backend_type = "native_posix"
        elif backend_type == "screen" and shutil.which("screen") is None:
            if self.logger:
                self.logger.log(
                    "WARNING",
                    "Screen backend requested but GNU Screen is not installed; "
                    "falling back to native backend.",
                )
            backend_type = "windows" if sys.platform == "win32" else "native_posix"
        elif backend_type == "native_posix" and sys.platform == "win32":
            backend_type = "windows"

        if backend_type == "screen":
            return ScreenBackend(
                server_name=server_name,
                server_dir=server_dir,
                state_file=state_file,
                pid_file=pid_file,
                logger=self.logger,
            )
        if backend_type == "windows":
            return WindowsBackend(
                server_name=server_name,
                server_dir=server_dir,
                state_file=state_file,
                pid_file=pid_file,
                logger=self.logger,
            )
        return NativePosixBackend(
            server_name=server_name,
            server_dir=server_dir,
            state_file=state_file,
            pid_file=pid_file,
            logger=self.logger,
        )
