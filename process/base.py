"""Abstract base class and models for process management backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from process.state import ProcessState


@dataclass
class LaunchSpec:
    """Complete parameters needed to launch a managed server process."""

    server_name: str
    command: list[str]
    cwd: Path
    log_file: Path
    state_file: Path
    pid_file: Path
    env: dict[str, str] = field(default_factory=dict)
    rcon_host: str = "127.0.0.1"
    rcon_port: int | None = None
    rcon_password: str | None = None
    screen_name: str | None = None


class ProcessBackend(ABC):
    """Abstract interface implemented by all MSM process backends."""

    def __init__(
        self,
        server_name: str,
        server_dir: Path,
        state_file: Path,
        pid_file: Path,
        logger=None,
    ):
        self.server_name = server_name
        self.server_dir = Path(server_dir)
        self.state_file = Path(state_file)
        self.pid_file = Path(pid_file)
        self.logger = logger

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend name (e.g. 'screen', 'native_posix', 'windows')."""
        ...

    @abstractmethod
    def start(self, spec: LaunchSpec) -> ProcessState:
        """Launch the managed process and return its initial verified state."""
        ...

    @abstractmethod
    def is_running(self) -> bool:
        """Check if the managed process is currently running and verified."""
        ...

    @abstractmethod
    def get_pid(self) -> int | None:
        """Return the validated process ID if running, or None."""
        ...

    @abstractmethod
    def send_command(self, command: str) -> bool:
        """Dispatch a console command to the running server."""
        ...

    @abstractmethod
    def graceful_stop(self, timeout: int = 20) -> bool:
        """Request graceful shutdown via server command, waiting up to timeout."""
        ...

    @abstractmethod
    def terminate(self, timeout: int = 5) -> bool:
        """Forcibly terminate the server process tree."""
        ...

    @abstractmethod
    def kill(self) -> bool:
        """Immediately kill the server process tree (SIGKILL / force kill)."""
        ...

    @abstractmethod
    def attach(self) -> tuple[bool, str]:
        """Attach interactively to the server console if supported, or explain why not."""
        ...

    def read_state(self) -> ProcessState | None:
        """Read structured process state from disk, validating process identity."""
        state = ProcessState.load(self.state_file)
        if state and state.validate_identity(logger=self.logger):
            return state

        # Fallback to legacy PID file if state file not yet present
        if self.pid_file.exists():
            try:
                pid = int(self.pid_file.read_text(encoding="utf-8").strip())
                legacy_state = ProcessState(
                    schema_version=1,
                    pid=pid,
                    server_name=self.server_name,
                    cwd=str(self.server_dir),
                    backend=self.name,
                )
                if legacy_state.validate_identity(logger=self.logger):
                    return legacy_state
            except (OSError, ValueError):
                pass
        return None

    def clean_stale_state(self) -> None:
        """Clean stale process files if process is verified dead or recycled."""
        if not self.is_running():
            self.state_file.unlink(missing_ok=True)
            self.pid_file.unlink(missing_ok=True)

    def inspect(self) -> dict[str, Any]:
        """Return diagnostic runtime info for this backend and process."""
        state = self.read_state()
        running = self.is_running()
        return {
            "backend": self.name,
            "running": running,
            "pid": state.pid if state else None,
            "started_at": state.started_at if state else None,
            "cmdline": state.cmdline if state else None,
            "state_file": str(self.state_file),
            "pid_file": str(self.pid_file),
        }
