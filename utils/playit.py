"""Playit.gg agent lifecycle, status inspection, and diagnostics."""

from __future__ import annotations

import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from core.constants import (
    PLAYIT_ENDPOINT_FILE_NAME,
    PLAYIT_SECRET_FILE_NAME,
    TUNNEL_PID_FILE_NAME,
    TUNNEL_STATUS_BINARY_MISSING,
    TUNNEL_STATUS_FAILED,
    TUNNEL_STATUS_MAPPING_MISSING,
    TUNNEL_STATUS_NOT_RUNNING,
    TUNNEL_STATUS_PROCESS_RUNNING,
    TUNNEL_STATUS_READY,
    TUNNEL_STATUS_SECRET_MISSING,
)
from utils.system import (
    is_pid_running,
    read_pid_file,
    read_text_file,
    remove_file,
    running_on_termux,
    write_text_file,
)
from utils.tunnel_models import TunnelCheck, TunnelStatus
from utils.tunnels import (
    build_playit_start_command,
    extract_playit_claim_url,
    extract_playit_public_endpoint,
)

# ---------------------------------------------------------------------------
# Binary resolution
# ---------------------------------------------------------------------------


def resolve_playit_binary(binary_path: str | None = None) -> str | None:
    """Return the first usable playit daemon binary, prioritizing playit and playitd."""
    candidates: list[str] = []
    if binary_path:
        # If binary_path points to playit-cli, check for sibling playit / playitd daemon
        if Path(binary_path).name == "playit-cli":
            cli_parent = Path(binary_path).parent
            if (cli_parent / "playit").exists():
                candidates.append(str(cli_parent / "playit"))
            if (cli_parent / "playitd").exists():
                candidates.append(str(cli_parent / "playitd"))
        else:
            candidates.append(binary_path)

    candidates.extend(["playit", "playitd"])

    if binary_path and Path(binary_path).name == "playit-cli":
        candidates.append(binary_path)

    # Remove duplicates while preserving order
    unique_candidates = []
    for c in candidates:
        if c not in unique_candidates:
            unique_candidates.append(c)

    for candidate in unique_candidates:
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


def get_saved_playit_endpoint(server_dir: Path) -> str | None:
    """Read a previously saved playit public endpoint."""
    return read_text_file(server_dir / PLAYIT_ENDPOINT_FILE_NAME)


def save_playit_endpoint(server_dir: Path, endpoint: str) -> None:
    """Persist the current playit public endpoint."""
    write_text_file(server_dir / PLAYIT_ENDPOINT_FILE_NAME, endpoint)


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------


def read_playit_log_tail(server_dir: Path, line_count: int = 80) -> str:
    """Return the last *line_count* lines of the playit log."""
    log_path = server_dir / ".msm.playit.log"
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


def inspect_playit_status(server_dir: Path) -> TunnelStatus:
    """Derive a *TunnelStatus* from the PID file and log output."""
    pid = read_pid_file(server_dir / TUNNEL_PID_FILE_NAME)
    running = pid is not None and is_pid_running(pid)
    log_tail = read_playit_log_tail(server_dir)
    endpoint = extract_playit_public_endpoint(log_tail) if log_tail else None
    claim_url = extract_playit_claim_url(log_tail) if log_tail else None

    if not endpoint:
        endpoint = get_saved_playit_endpoint(server_dir)

    if not endpoint and running and (server_dir / ".msm.playit.secret").exists():
        secret = read_text_file(server_dir / ".msm.playit.secret")
        if secret:
            try:
                from utils.playit_api import (
                    PlayitApiClient,
                    extract_tunnel_endpoint,
                )

                client = PlayitApiClient(agent_secret=secret, timeout=4)
                try:
                    rundata = client.agent_rundata()
                    if isinstance(rundata, dict):
                        for t in rundata.get("tunnels") or []:
                            ep = extract_tunnel_endpoint(t)
                            if ep:
                                endpoint = ep
                                save_playit_endpoint(server_dir, endpoint)
                                break
                except Exception:
                    pass

                if not endpoint:
                    try:
                        v1_rundata = client.v1_agents_rundata()
                        if isinstance(v1_rundata, dict):
                            for t in v1_rundata.get("tunnels") or []:
                                ep = extract_tunnel_endpoint(t)
                                if ep:
                                    endpoint = ep
                                    save_playit_endpoint(server_dir, endpoint)
                                    break
                    except Exception:
                        pass

                if not endpoint:
                    try:
                        tunnels = client.tunnels_list()
                        for t in tunnels:
                            ep = extract_tunnel_endpoint(t)
                            if ep:
                                endpoint = ep
                                save_playit_endpoint(server_dir, endpoint)
                                break
                    except Exception:
                        pass

                if not endpoint:
                    try:
                        v1_tunnels = client.v1_tunnels_list()
                        for t in v1_tunnels:
                            ep = extract_tunnel_endpoint(t)
                            if ep:
                                endpoint = ep
                                save_playit_endpoint(server_dir, endpoint)
                                break
                    except Exception:
                        pass
            except Exception:
                pass

    if endpoint and running:
        save_playit_endpoint(server_dir, endpoint)
        return TunnelStatus(
            provider="playit",
            state=TUNNEL_STATUS_READY,
            message=f"Tunnel ready: {endpoint}",
            endpoint=endpoint,
            claim_url=claim_url,
            pid=pid,
        )
    if endpoint:
        return TunnelStatus(
            provider="playit",
            state=TUNNEL_STATUS_NOT_RUNNING,
            message=f"Not running (last endpoint: {endpoint})",
            endpoint=endpoint,
        )
    if claim_url and running:
        return TunnelStatus(
            provider="playit",
            state=TUNNEL_STATUS_PROCESS_RUNNING,
            message="Playit needs account linking",
            claim_url=claim_url,
            pid=pid,
        )
    if running:
        return TunnelStatus(
            provider="playit",
            state=TUNNEL_STATUS_MAPPING_MISSING,
            message=(
                "Playit agent is running; "
                "run the tunnel setup wizard to create or update the mapping"
            ),
            pid=pid,
        )
    return TunnelStatus(
        provider="playit",
        state=TUNNEL_STATUS_NOT_RUNNING,
        message="Not running",
    )


# ---------------------------------------------------------------------------
# Agent lifecycle
# ---------------------------------------------------------------------------


def start_playit_agent(
    server_dir: Path,
    binary_path: str,
    secret_file: Path,
    logger,
) -> tuple[TunnelStatus, Any]:
    """Start the playit background agent and return an initial status."""
    resolved = resolve_playit_binary(binary_path)
    if not resolved:
        if running_on_termux():
            install_hint = "Install with: pkg install tur-repo && pkg install playit"
        else:
            install_hint = (
                "Download playit from https://playit.gg/download "
                "or install via your distro's package manager."
            )
        return (
            TunnelStatus(
                provider="playit",
                state=TUNNEL_STATUS_BINARY_MISSING,
                message=(
                    f"Playit binary '{binary_path}' was not found. " + install_hint
                ),
            ),
            None,
        )
    if not secret_file.exists():
        return (
            TunnelStatus(
                provider="playit",
                state=TUNNEL_STATUS_SECRET_MISSING,
                message=(
                    "Playit secret file was not found. "
                    "Run the tunnel setup wizard first."
                ),
            ),
            None,
        )
    existing_pid = read_pid_file(server_dir / TUNNEL_PID_FILE_NAME)
    if existing_pid and is_pid_running(existing_pid):
        return inspect_playit_status(server_dir), None

    log_path = server_dir / ".msm.playit.log"
    log_handle = log_path.open("a", encoding="utf-8")
    socket_file = server_dir / ".msm.playit.sock"
    command = build_playit_start_command(
        resolved, secret_path=secret_file, socket_path=socket_file
    )
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
        tail = read_playit_log_tail(server_dir, line_count=10)
        return (
            TunnelStatus(
                provider="playit",
                state=TUNNEL_STATUS_FAILED,
                message=f"Playit exited immediately (code {exit_code}). {tail}",
            ),
            None,
        )

    log_handle.flush()
    status = inspect_playit_status(server_dir)
    return status, log_handle


# ---------------------------------------------------------------------------
# Mapping hint
# ---------------------------------------------------------------------------


def build_playit_mapping_hint(protocol: str, local_host: str, local_port: int) -> str:
    """Return user-facing instructions for creating a playit tunnel mapping."""
    return (
        f"Run the Playit setup wizard to create or update this tunnel:\n"
        f"  Protocol: {protocol.upper()}\n"
        f"  Local IP: {local_host}\n"
        f"  Local Port: {local_port}"
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


def diagnose_playit(
    server_dir: Path,
    tunnel_config: dict,
    server_port: int,
    server_flavor: str | None = None,
) -> list[TunnelCheck]:
    """Run a checklist of playit-related diagnostics."""
    checks: list[TunnelCheck] = []
    binary = tunnel_config.get("binary_path", "playit-cli")
    resolved = resolve_playit_binary(binary)
    checks.append(
        TunnelCheck(
            name="Playit binary",
            ok=resolved is not None,
            detail=resolved or f"'{binary}' not found",
        )
    )

    secret_file = server_dir / PLAYIT_SECRET_FILE_NAME
    checks.append(
        TunnelCheck(
            name="Playit secret",
            ok=secret_file.exists(),
            detail=str(secret_file) if secret_file.exists() else "Missing",
        )
    )

    protocol = tunnel_config.get("protocol", "tcp")
    checks.append(
        TunnelCheck(
            name="Protocol",
            ok=protocol in ("tcp", "udp"),
            detail=protocol,
        )
    )

    if server_flavor == "pocketmine" and protocol != "udp":
        checks.append(
            TunnelCheck(
                name="PocketMine protocol",
                ok=False,
                detail=(
                    "PocketMine/Bedrock requires UDP. " "Set tunnel protocol to udp."
                ),
            )
        )

    local_host = tunnel_config.get("local_host", "127.0.0.1")
    local_port = tunnel_config.get("local_port") or server_port
    if protocol == "tcp":
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
    else:
        checks.append(
            TunnelCheck(
                name="Local UDP port",
                ok=True,
                detail=(
                    f"{local_host}:{local_port} "
                    "(UDP cannot be verified with TCP connect)"
                ),
            )
        )

    status = inspect_playit_status(server_dir)
    checks.append(
        TunnelCheck(
            name="Playit status",
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
    elif status.state in (
        TUNNEL_STATUS_MAPPING_MISSING,
        TUNNEL_STATUS_PROCESS_RUNNING,
    ):
        hint = build_playit_mapping_hint(protocol, local_host, int(local_port))
        checks.append(
            TunnelCheck(
                name="Mapping hint",
                ok=False,
                detail=hint,
            )
        )

    return checks
