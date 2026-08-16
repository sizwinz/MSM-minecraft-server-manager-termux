"""Structured process state and process identity validation."""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil


@dataclass
class ProcessState:
    """Structured runtime state for a managed server process."""

    schema_version: int = 1
    pid: int = 0
    create_time: float = 0.0
    executable: str = ""
    cmdline: list[str] = field(default_factory=list)
    cmd_fingerprint: str = ""
    cwd: str = ""
    server_name: str = ""
    instance_id: str = ""
    backend: str = "native_posix"
    platform: str = sys.platform
    started_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProcessState:
        return cls(
            schema_version=int(data.get("schema_version", 1)),
            pid=int(data.get("pid", 0)),
            create_time=float(data.get("create_time", 0.0)),
            executable=str(data.get("executable", "")),
            cmdline=list(data.get("cmdline", [])),
            cmd_fingerprint=str(data.get("cmd_fingerprint", "")),
            cwd=str(data.get("cwd", "")),
            server_name=str(data.get("server_name", "")),
            instance_id=str(data.get("instance_id", "")),
            backend=str(data.get("backend", "native_posix")),
            platform=str(data.get("platform", sys.platform)),
            started_at=str(data.get("started_at", datetime.now().isoformat())),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save(self, state_file: Path | str) -> None:
        path = Path(state_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(
            f"{path.suffix}.tmp_{os.getpid()}_{int(time.time() * 1000)}"
        )
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
        tmp_path.replace(path)

    @classmethod
    def load(cls, state_file: Path | str) -> ProcessState | None:
        path = Path(state_file)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict) and "pid" in raw:
                return cls.from_dict(raw)
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        return None

    def validate_identity(self, logger=None) -> bool:
        """Validate whether the process represented by this state
        is actively running with matching identity."""
        if not self.pid or self.pid <= 0:
            return False

        try:
            proc = psutil.Process(self.pid)
            if not proc.is_running():
                return False

            # Check creation time with tolerance (within 3 seconds)
            if self.create_time > 0:
                try:
                    proc_create_time = proc.create_time()
                    if abs(proc_create_time - self.create_time) > 3.0:
                        if logger:
                            logger.log(
                                "WARNING",
                                f"Process ID {self.pid} reused: creation time mismatch "
                                f"({proc_create_time} vs recorded {self.create_time}).",
                            )
                        return False
                except (psutil.Error, OSError):
                    pass

            # Check working directory if accessible
            if self.cwd:
                try:
                    proc_cwd = proc.cwd()
                    if Path(proc_cwd).resolve() != Path(self.cwd).resolve():
                        # CWD mismatch indicates PID recycling
                        if logger:
                            logger.log(
                                "WARNING",
                                f"Process ID {self.pid} reused: cwd mismatch "
                                f"({proc_cwd} vs recorded {self.cwd}).",
                            )
                        return False
                except (psutil.Error, OSError):
                    pass

            # Check executable / name
            try:
                proc_name = proc.name().lower()
                cmdline_str = " ".join(proc.cmdline()).lower()
                known_tokens = (
                    "java",
                    "php",
                    "screen",
                    "msm",
                    "bedrock",
                    "mojang",
                    "paper",
                    "fabric",
                    "quilt",
                    "purpur",
                    "folia",
                    "vanilla",
                    "pocketmine",
                    "python",
                    "pytest",
                    "py",
                    "node",
                )
                matches_exe = (
                    self.executable
                    and os.path.basename(self.executable).lower().replace(".exe", "")
                    in proc_name
                )
                if not matches_exe and not any(
                    token in proc_name or token in cmdline_str for token in known_tokens
                ):
                    if logger:
                        logger.log(
                            "WARNING",
                            f"Process ID {self.pid} does not look like a Minecraft "
                            f"server ({proc_name}).",
                        )
                    return False
            except (psutil.Error, OSError):
                pass

            return True

        except psutil.NoSuchProcess:
            return False
        except psutil.AccessDenied:
            # Process exists but we lack inspection permissions (e.g. other user)
            return True
        except psutil.Error:
            return False


def create_process_state(
    pid: int,
    cmdline: list[str],
    cwd: Path | str,
    server_name: str,
    backend: str,
    instance_id: str | None = None,
) -> ProcessState:
    """Construct a ProcessState instance from a running PID and launch specs."""
    cwd_str = str(Path(cwd).resolve())
    create_time = 0.0
    exe = ""
    try:
        proc = psutil.Process(pid)
        create_time = proc.create_time()
        exe = proc.exe()
    except (psutil.Error, OSError):
        pass

    if not exe and cmdline:
        exe = cmdline[0]

    fingerprint = ":".join(os.path.basename(arg) for arg in cmdline[:5])
    return ProcessState(
        schema_version=1,
        pid=pid,
        create_time=create_time,
        executable=exe,
        cmdline=cmdline,
        cmd_fingerprint=fingerprint,
        cwd=cwd_str,
        server_name=server_name,
        instance_id=instance_id or f"{server_name}_{int(time.time())}",
        backend=backend,
        platform=sys.platform,
        started_at=datetime.now().isoformat(),
    )
