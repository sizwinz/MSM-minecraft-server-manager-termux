"""Playit.gg agent lifecycle, status inspection, and diagnostics."""

from __future__ import annotations

import re
import psutil
import shutil
import socket
import subprocess
import time
from pathlib import Path

from core.constants import (
    PLAYIT_ENDPOINT_FILE_NAME,
    PLAYIT_SECRET_FILE_NAME,
    TUNNEL_STATUS_BINARY_MISSING,
    TUNNEL_STATUS_FAILED,
    TUNNEL_STATUS_MAPPING_MISSING,
    TUNNEL_STATUS_NOT_RUNNING,
    TUNNEL_STATUS_PROCESS_RUNNING,
    TUNNEL_STATUS_READY,
    TUNNEL_STATUS_SECRET_MISSING,
)
from utils.system import (
    read_text_file,
    write_text_file,
)
from utils.tunnel_models import TunnelCheck, TunnelStatus
from utils.tunnel_process import record_tunnel, tunnel_state, stop_tunnel_process
from utils.tunnels import (
    build_playit_start_command,
    extract_playit_claim_url,
    ANSI_ESCAPE_PATTERN,
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


def extract_playit_secret_from_file(path: Path) -> str | None:
    """Extract secret key from a .secret or playit.toml file."""
    if not path.exists():
        return None
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    match = re.search(r'secret(?:_key)?\s*=\s*"([^"]+)"', content)
    if match:
        return match.group(1)
    if content and not content.startswith("["):
        return content
    return None


def save_playit_secret(path: Path, secret: str) -> None:
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".playit-secret-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(secret.strip())
            handle.flush()
            os.fsync(handle.fileno())
        Path(name).replace(path)
    finally:
        Path(name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------


def read_playit_log_tail(server_dir: Path, line_count: int = 80) -> str:
    """Return the last *line_count* lines of the playit log."""
    log_path = server_dir / ".msm.playit.log"
    if not log_path.exists():
        return ""
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 65536))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-line_count:])


# ---------------------------------------------------------------------------
# Status inspection
# ---------------------------------------------------------------------------


def _matching_mapping(tunnel: dict, config: dict) -> bool:
    origin = tunnel.get("origin") or {}
    data = (
        origin.get("data") or origin.get("details") or {}
        if isinstance(origin, dict)
        else {}
    )
    port = tunnel.get("local_port", data.get("local_port"))
    host = tunnel.get("local_ip", data.get("local_ip"))
    protocol = tunnel.get("proto") or tunnel.get("port_type")
    target_id = config.get("playit_tunnel_id")
    if target_id and str(tunnel.get("id")) != str(target_id):
        return False
    return (
        not tunnel.get("disabled")
        and tunnel.get("enabled", True)
        and str(port) == str(config.get("local_port", 25565))
        and host == config.get("local_host", "127.0.0.1")
        and protocol in (config.get("protocol", "tcp"), "both")
    )


def playit_socket_path(server_dir: Path) -> Path:
    import hashlib
    import os
    import tempfile

    token = hashlib.sha256(str(server_dir.resolve()).encode()).hexdigest()[:16]
    if os.name == "nt":
        return Path("\\\\.\\pipe\\msm-playit-" + token)
    # Unix-domain socket paths are limited to 108 bytes on Linux/Android.
    return Path(tempfile.gettempdir()) / f"msm-playit-{token}.sock"


def inspect_playit_status(
    server_dir: Path, tunnel_config: dict | None = None
) -> TunnelStatus:
    state = tunnel_state(server_dir, "playit")
    if not state:
        saved = get_saved_playit_endpoint(server_dir)
        return TunnelStatus(
            "playit",
            TUNNEL_STATUS_NOT_RUNNING,
            f"Not running (last endpoint: {saved})" if saved else "Not running",
            endpoint=saved,
        )
    config = {**state, **(tunnel_config or {})}
    tail = ANSI_ESCAPE_PATTERN.sub("", read_playit_log_tail(server_dir))
    claim_url = extract_playit_claim_url(tail)
    error = None
    connected = False
    for line in tail.splitlines():
        if any(
            token in line.lower()
            for token in (
                "invalidagentkey",
                "no longer valid",
                "waiting for frontend secret",
                "failed to load agent data",
                "sessionnotsetup",
                "session timed out",
                "connection lost",
                "disconnected",
            )
        ):
            error, connected = line.strip(), False
        elif any(
            token in line.lower()
            for token in (
                "playit connected",
                "tunnel running",
                "agent registered",
                "udp session details received",
            )
        ):
            connected, error = True, None
    if error:
        fatal = any(
            token in error.lower()
            for token in (
                "invalidagentkey",
                "no longer valid",
                "waiting for frontend secret",
            )
        )
        return TunnelStatus(
            "playit",
            TUNNEL_STATUS_FAILED if fatal else TUNNEL_STATUS_PROCESS_RUNNING,
            f"Playit: {error}",
            pid=state["pid"],
            claim_url=claim_url,
        )
    secret = extract_playit_secret_from_file(server_dir / PLAYIT_SECRET_FILE_NAME)
    if not secret:
        return TunnelStatus(
            "playit",
            TUNNEL_STATUS_SECRET_MISSING,
            "Playit agent needs account linking",
            pid=state["pid"],
            claim_url=claim_url,
        )
    if connected:
        from utils.playit_api import (
            PlayitApiClient,
            PlayitApiError,
            extract_tunnel_endpoint,
        )

        try:
            with PlayitApiClient(agent_secret=secret, timeout=2) as client:
                data = client.agent_rundata()
            mappings = [
                t
                for t in data.get("tunnels", [])
                if isinstance(t, dict) and _matching_mapping(t, config)
            ]
            if len(mappings) == 1:
                endpoint = extract_tunnel_endpoint(mappings[0])
                if endpoint:
                    save_playit_endpoint(server_dir, endpoint)
                    return TunnelStatus(
                        "playit",
                        TUNNEL_STATUS_READY,
                        f"Tunnel ready: {endpoint}",
                        endpoint=endpoint,
                        pid=state["pid"],
                    )
            target_id = config.get("playit_tunnel_id")
            if target_id and any(
                str(t.get("id")) == str(target_id)
                for t in data.get("pending", [])
                if isinstance(t, dict)
            ):
                with PlayitApiClient(agent_secret=secret, timeout=2) as client:
                    pending = client.tunnels_list(agent_id=data.get("agent_id"))
                for item in pending:
                    if str(item.get("id")) == str(target_id):
                        allocation = item.get("alloc") or {}
                        reason = item.get("disabled_reason") or (
                            allocation.get("data") or {}
                        ).get("reason")
                        if reason:
                            return TunnelStatus(
                                "playit",
                                TUNNEL_STATUS_FAILED,
                                f"Playit disabled this mapping: {reason}. "
                                "Check available ports in your Playit dashboard.",
                                pid=state["pid"],
                            )
                return TunnelStatus(
                    "playit",
                    TUNNEL_STATUS_PROCESS_RUNNING,
                    "Playit mapping is waiting for provider allocation",
                    pid=state["pid"],
                )
            return TunnelStatus(
                "playit",
                TUNNEL_STATUS_MAPPING_MISSING,
                "No unique active Playit mapping matches this server's address and protocol.",
                pid=state["pid"],
            )
        except PlayitApiError as exc:
            return TunnelStatus(
                "playit",
                TUNNEL_STATUS_FAILED,
                f"Cannot verify Playit mapping: {exc}",
                pid=state["pid"],
            )
    return TunnelStatus(
        "playit",
        TUNNEL_STATUS_PROCESS_RUNNING,
        "Playit is connecting; public endpoint is not confirmed",
        claim_url=claim_url,
        pid=state["pid"],
    )


def start_playit_agent(
    server_dir: Path,
    binary_path: str,
    secret_file: Path,
    logger,
    tunnel_config: dict | None = None,
):
    resolved = resolve_playit_binary(binary_path)
    if not resolved:
        return (
            TunnelStatus(
                "playit",
                TUNNEL_STATUS_BINARY_MISSING,
                f"Playit daemon '{binary_path}' not found. "
                "Install playitd or a standalone Playit agent.",
            ),
            None,
        )
    if not extract_playit_secret_from_file(secret_file):
        return (
            TunnelStatus(
                "playit",
                TUNNEL_STATUS_SECRET_MISSING,
                "Playit secret is missing; run the setup wizard.",
            ),
            None,
        )
    if tunnel_state(server_dir, "playit"):
        return inspect_playit_status(server_dir, tunnel_config), None
    if tunnel_state(server_dir):
        return (
            TunnelStatus(
                "playit",
                TUNNEL_STATUS_FAILED,
                "Stop the existing tunnel before changing providers.",
            ),
            None,
        )
    server_dir.mkdir(parents=True, exist_ok=True)
    process = None
    try:
        help_result = subprocess.run(
            [resolved, "--help"], capture_output=True, text=True, timeout=5, check=False
        )
        help_text = help_result.stdout + help_result.stderr
        if "--secret-path" not in help_text and "--secret_path" not in help_text:
            raise ValueError(
                "This executable is a controller, not a Playit daemon. Install/select playitd."
            )
        socket_file = playit_socket_path(server_dir)
        command = build_playit_start_command(
            resolved,
            secret_path=secret_file,
            socket_path=socket_file if "--socket-path" in help_text else None,
            legacy="--secret_path" in help_text,
        )
        with (server_dir / ".msm.playit.log").open("w", encoding="utf-8") as handle:
            process = subprocess.Popen(
                command,
                cwd=server_dir,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        config = dict(tunnel_config or {})
        record_tunnel(
            server_dir,
            "playit",
            process,
            **{
                k: config.get(k, v)
                for k, v in (
                    ("local_host", "127.0.0.1"),
                    ("local_port", 25565),
                    ("protocol", "tcp"),
                    ("playit_tunnel_id", None),
                )
            },
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if process.poll() is not None:
                status = TunnelStatus(
                    "playit",
                    TUNNEL_STATUS_FAILED,
                    f"Playit exited (code {process.returncode}): "
                    f"{read_playit_log_tail(server_dir, 8)}",
                )
                stop_tunnel_process(server_dir, process)
                return status, None
            status = inspect_playit_status(server_dir, config)
            if status.state == TUNNEL_STATUS_FAILED:
                stop_tunnel_process(server_dir, process)
                return status, None
            if status.state in (TUNNEL_STATUS_READY, TUNNEL_STATUS_MAPPING_MISSING):
                return status, process
            time.sleep(0.2)
        return inspect_playit_status(server_dir, config), process
    except (OSError, ValueError, psutil.Error, subprocess.TimeoutExpired) as exc:
        stop_tunnel_process(server_dir, process)
        return (
            TunnelStatus(
                "playit", TUNNEL_STATUS_FAILED, f"Could not start Playit: {exc}"
            ),
            None,
        )


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

    status = inspect_playit_status(
        server_dir, {**tunnel_config, "local_port": local_port}
    )
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
