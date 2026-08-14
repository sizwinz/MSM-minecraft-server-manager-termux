"""Ngrok agent lifecycle, status inspection, and diagnostics."""

from __future__ import annotations

import shutil
from typing import Any
import socket
import subprocess
import time
from pathlib import Path

from core.constants import (
    NGROK_ENDPOINT_FILE_NAME,
    NGROK_TIMEOUT,
    TUNNEL_PID_FILE_NAME,
    TUNNEL_STATUS_BINARY_MISSING,
    TUNNEL_STATUS_FAILED,
    TUNNEL_STATUS_NOT_RUNNING,
    TUNNEL_STATUS_PROCESS_RUNNING,
    TUNNEL_STATUS_READY,
)
from utils.network import get_ngrok_public_url
from utils.system import (
    is_pid_running,
    read_pid_file,
    read_text_file,
    remove_file,
    write_text_file,
)
from utils.tunnel_models import TunnelCheck, TunnelStatus

# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def resolve_ngrok_binary(binary_path: str | None = None) -> str | None:
    """Return the first usable ngrok binary, or *None*."""
    candidates: list[str] = []
    if binary_path:
        candidates.append(binary_path)
    candidates.append("ngrok")
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        expanded = Path(candidate).expanduser()
        if expanded.exists() and expanded.is_file():
            return str(expanded)
        home_candidate = Path.home() / "bin" / candidate
        if home_candidate.exists() and home_candidate.is_file():
            return str(home_candidate)
    return None


# ---------------------------------------------------------------------------
# Persistent endpoint cache
# ---------------------------------------------------------------------------


def get_saved_ngrok_endpoint(server_dir: Path) -> str | None:
    """Read a previously saved ngrok public endpoint."""
    return read_text_file(server_dir / NGROK_ENDPOINT_FILE_NAME)


def save_ngrok_endpoint(server_dir: Path, endpoint: str) -> None:
    """Persist the current ngrok public endpoint."""
    write_text_file(server_dir / NGROK_ENDPOINT_FILE_NAME, endpoint)


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------


def _read_ngrok_log_tail(server_dir: Path, line_count: int = 10) -> str:
    """Return the last *line_count* lines of the ngrok log."""
    log_path = server_dir / ".msm.ngrok.log"
    if not log_path.exists():
        return ""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-line_count:])


# ---------------------------------------------------------------------------
# Status inspection
# ---------------------------------------------------------------------------


def inspect_ngrok_status(
    server_dir: Path,
    port: int,
    logger=None,
) -> TunnelStatus:
    """Derive a *TunnelStatus* from the PID file and the ngrok API."""
    pid = read_pid_file(server_dir / TUNNEL_PID_FILE_NAME)
    running = pid is not None and is_pid_running(pid)

    if running:
        endpoint = get_ngrok_public_url(port, logger=logger, timeout=2)
        if endpoint:
            save_ngrok_endpoint(server_dir, endpoint)
            return TunnelStatus(
                provider="ngrok",
                state=TUNNEL_STATUS_READY,
                message=f"Tunnel ready: {endpoint}",
                endpoint=endpoint,
                pid=pid,
            )
        saved = get_saved_ngrok_endpoint(server_dir)
        if saved:
            return TunnelStatus(
                provider="ngrok",
                state=TUNNEL_STATUS_READY,
                message=f"Tunnel ready: {saved}",
                endpoint=saved,
                pid=pid,
            )
        tail = _read_ngrok_log_tail(server_dir, line_count=2)
        msg = "Ngrok is running; waiting for public URL"
        if "ERR_NGROK_" in tail or "error" in tail.lower():
            clean_tail = " ".join(tail.split())
            if len(clean_tail) > 60:
                clean_tail = clean_tail[:60] + "..."
            msg = f"Ngrok: {clean_tail}"
        return TunnelStatus(
            provider="ngrok",
            state=TUNNEL_STATUS_PROCESS_RUNNING,
            message=msg,
            pid=pid,
        )

    saved = get_saved_ngrok_endpoint(server_dir)
    if saved:
        return TunnelStatus(
            provider="ngrok",
            state=TUNNEL_STATUS_NOT_RUNNING,
            message=f"Not running (last endpoint: {saved})",
            endpoint=saved,
        )
    return TunnelStatus(
        provider="ngrok",
        state=TUNNEL_STATUS_NOT_RUNNING,
        message="Not running",
    )


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------


def start_ngrok_agent(
    server_dir: Path,
    binary_path: str,
    port: int,
    logger,
) -> tuple[TunnelStatus, Any]:
    """Start the ngrok background agent and return an initial status."""
    resolved = resolve_ngrok_binary(binary_path)
    if not resolved:
        return (
            TunnelStatus(
                provider="ngrok",
                state=TUNNEL_STATUS_BINARY_MISSING,
                message=f"Ngrok binary '{binary_path}' was not found.",
            ),
            None,
        )
    existing_pid = read_pid_file(server_dir / TUNNEL_PID_FILE_NAME)
    if existing_pid and is_pid_running(existing_pid):
        return inspect_ngrok_status(server_dir, port, logger=logger), None

    log_path = server_dir / ".msm.ngrok.log"
    log_handle = log_path.open("a", encoding="utf-8")
    command = [resolved, "tcp", str(port), "--log", "stdout"]
    process = subprocess.Popen(
        command,
        cwd=server_dir,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    write_text_file(server_dir / TUNNEL_PID_FILE_NAME, str(process.pid))

    deadline = time.monotonic() + 3.0
    exit_code = process.poll()
    while exit_code is None and time.monotonic() < deadline:
        time.sleep(0.1)
        exit_code = process.poll()

    if exit_code is not None:
        remove_file(server_dir / TUNNEL_PID_FILE_NAME)
        log_handle.flush()
        log_handle.close()
        tail = _read_ngrok_log_tail(server_dir)
        return (
            TunnelStatus(
                provider="ngrok",
                state=TUNNEL_STATUS_FAILED,
                message=f"Ngrok exited immediately (code {exit_code}). {tail}",
            ),
            None,
        )

    log_handle.flush()

    # Poll the ngrok API for the public URL.
    poll_timeout = min(NGROK_TIMEOUT, 15)
    poll_deadline = time.monotonic() + poll_timeout
    public_url: str | None = None
    while time.monotonic() < poll_deadline:
        public_url = get_ngrok_public_url(port, logger=logger, timeout=2)
        if public_url:
            break
        time.sleep(1)

    if public_url:
        save_ngrok_endpoint(server_dir, public_url)
        return (
            TunnelStatus(
                provider="ngrok",
                state=TUNNEL_STATUS_READY,
                message=f"Tunnel ready: {public_url}",
                endpoint=public_url,
                pid=process.pid,
            ),
            log_handle,
        )
    return (
        TunnelStatus(
            provider="ngrok",
            state=TUNNEL_STATUS_PROCESS_RUNNING,
            message=(
                "Ngrok process started. " "Check http://127.0.0.1:4040 for status."
            ),
            pid=process.pid,
        ),
        log_handle,
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def diagnose_ngrok(
    server_dir: Path,
    tunnel_config: dict,
    server_port: int,
    server_flavor: str | None = None,
    logger=None,
) -> list[TunnelCheck]:
    """Run a checklist of ngrok-related diagnostics."""
    checks: list[TunnelCheck] = []
    binary = tunnel_config.get("binary_path", "ngrok")
    resolved = resolve_ngrok_binary(binary)
    checks.append(
        TunnelCheck(
            name="Ngrok binary",
            ok=resolved is not None,
            detail=resolved or f"'{binary}' not found",
        )
    )

    protocol = tunnel_config.get("protocol", "tcp")
    if protocol != "tcp":
        checks.append(
            TunnelCheck(
                name="Protocol",
                ok=False,
                detail=(
                    f"Ngrok in this MSM implementation supports TCP only. "
                    f"'{protocol}' is not supported."
                ),
            )
        )
    else:
        checks.append(TunnelCheck(name="Protocol", ok=True, detail="tcp"))

    if server_flavor == "pocketmine":
        checks.append(
            TunnelCheck(
                name="PocketMine compatibility",
                ok=False,
                detail=(
                    "PocketMine/Bedrock requires UDP. "
                    "Ngrok is TCP-only in this implementation. "
                    "Use Playit.gg instead."
                ),
            )
        )

    local_host = tunnel_config.get("local_host", "127.0.0.1")
    local_port = tunnel_config.get("local_port") or server_port
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((local_host, int(local_port)))
        sock.close()
        port_ok = result == 0
    except OSError:
        port_ok = False
    checks.append(
        TunnelCheck(
            name="Local TCP port reachable",
            ok=port_ok,
            detail=f"{local_host}:{local_port}",
        )
    )

    status = inspect_ngrok_status(server_dir, int(local_port), logger=logger)
    checks.append(
        TunnelCheck(
            name="Ngrok status",
            ok=status.state == TUNNEL_STATUS_READY,
            detail=status.message,
        )
    )

    if status.endpoint:
        checks.append(
            TunnelCheck(
                name="Endpoint",
                ok=True,
                detail=status.endpoint,
            )
        )
    elif status.state == TUNNEL_STATUS_NOT_RUNNING:
        checks.append(
            TunnelCheck(
                name="Auth hint",
                ok=True,
                detail=(
                    "If auth is missing, run: " "ngrok config add-authtoken <token>"
                ),
            )
        )

    return checks
