"""Ownership checks and bounded cleanup for per-server tunnel processes."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile

import psutil

from core.constants import TUNNEL_PID_FILE_NAME


def _start_ticks(pid: int) -> str | None:
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def record_tunnel(server_dir: Path, provider: str, process, **details) -> dict:
    proc = psutil.Process(process.pid)
    try:
        created = proc.create_time()
    except (psutil.Error, OSError):
        created = None
    state = {
        "pid": process.pid,
        "provider": provider,
        "created": created,
        "start_ticks": _start_ticks(process.pid),
        "cmdline": proc.cmdline(),
        **details,
    }
    descriptor, name = tempfile.mkstemp(
        dir=server_dir, prefix=".msm-tunnel-", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        Path(name).replace(server_dir / ".msm.tunnel.json")
        (server_dir / TUNNEL_PID_FILE_NAME).write_text(
            str(process.pid), encoding="utf-8"
        )
    finally:
        Path(name).unlink(missing_ok=True)
    return state


def tunnel_state(server_dir: Path, provider: str | None = None) -> dict | None:
    try:
        state = json.loads(
            (server_dir / ".msm.tunnel.json").read_text(encoding="utf-8")
        )
        if not isinstance(state, dict) or (
            provider and state.get("provider") != provider
        ):
            return None
        pid = int(state["pid"])
        if pid <= 0:
            return None
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() in (
            psutil.STATUS_ZOMBIE,
            psutil.STATUS_DEAD,
        ):
            return None
        if state.get("start_ticks"):
            if _start_ticks(pid) != state["start_ticks"]:
                return None
        elif state.get("created") is not None:
            if abs(proc.create_time() - state["created"]) > 0.1:
                return None
        else:
            # Never trust a bare PID when the OS cannot establish its identity.
            return None
        if not state.get("cmdline") or proc.cmdline() != state["cmdline"]:
            return None
        return state
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        return None


def stop_tunnel_process(server_dir: Path, process=None) -> bool:
    state = tunnel_state(server_dir)
    try:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        elif state:
            proc = psutil.Process(state["pid"])
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    except (psutil.NoSuchProcess, ProcessLookupError):
        pass
    except (psutil.Error, OSError, subprocess.TimeoutExpired):
        return False
    (server_dir / TUNNEL_PID_FILE_NAME).unlink(missing_ok=True)
    (server_dir / ".msm.tunnel.json").unlink(missing_ok=True)
    return True
