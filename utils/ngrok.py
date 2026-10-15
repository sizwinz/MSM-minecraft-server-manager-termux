"""Isolated ngrok agents with verified process ownership and current status."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time

import psutil
from pathlib import Path

from core.constants import (
    NGROK_ENDPOINT_FILE_NAME,
    NGROK_TIMEOUT,
    TUNNEL_STATUS_BINARY_MISSING,
    TUNNEL_STATUS_FAILED,
    TUNNEL_STATUS_NOT_RUNNING,
    TUNNEL_STATUS_PROCESS_RUNNING,
    TUNNEL_STATUS_READY,
)
from utils.network import get_ngrok_public_url
from utils.system import read_text_file, write_text_file
from utils.tunnel_models import TunnelCheck, TunnelStatus
from utils.tunnel_process import record_tunnel, tunnel_state, stop_tunnel_process


def resolve_ngrok_binary(binary_path: str | None = None) -> str | None:
    candidates = [binary_path] if binary_path else []
    candidates.extend(["ngrok", "termux-ngrok"])
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
        expanded = Path(candidate).expanduser()
        for path in (
            expanded,
            Path.home() / "bin" / candidate,
            Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
            / "bin"
            / candidate,
            Path.home() / "termux-ngrok" / candidate,
        ):
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    return None


def get_saved_ngrok_endpoint(server_dir: Path) -> str | None:
    return read_text_file(server_dir / NGROK_ENDPOINT_FILE_NAME)


def save_ngrok_endpoint(server_dir: Path, endpoint: str) -> None:
    write_text_file(server_dir / NGROK_ENDPOINT_FILE_NAME, endpoint)


def _read_ngrok_log_tail(server_dir: Path, line_count: int = 30) -> str:
    try:
        with (server_dir / ".msm.ngrok.log").open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 32768))
            return "\n".join(
                handle.read()
                .decode("utf-8", errors="replace")
                .splitlines()[-line_count:]
            )
    except OSError:
        return ""


def _extract_endpoint_or_error_from_log(
    server_dir: Path,
) -> tuple[str | None, str | None]:
    endpoint = None
    error = None
    for line in _read_ngrok_log_tail(server_dir, 150).splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            entry = {}
        if isinstance(entry, dict) and entry:
            message = str(entry.get("msg", ""))
            if "no usable hardware address" in message.lower():
                continue
            if (
                entry.get("lvl") in ("eror", "error", "crit", "fatal")
                or "ERR_NGROK_" in line
            ):
                error = str(entry.get("err") or message)
                endpoint = None
            elif "reconnect" in message.lower() or "session closed" in message.lower():
                endpoint = None
                error = message
            elif message in ("client session established", "tunnel session started"):
                error = None
            elif str(entry.get("url", "")).startswith("tcp://"):
                endpoint = entry["url"]
                error = None
        else:
            code = re.search(r"ERR_NGROK_[0-9]+[^\n]*", line)
            if code:
                error, endpoint = code.group(0), None
            elif re.search(r"lvl=(eror|error|crit)", line):
                error, endpoint = line, None
            else:
                match = re.search(r"tcp://[a-zA-Z0-9.-]+:[0-9]+", line)
                if match:
                    endpoint, error = match.group(0), None
    return endpoint, error


def inspect_ngrok_status(server_dir: Path, port: int, logger=None) -> TunnelStatus:
    state = tunnel_state(server_dir, "ngrok")
    if not state:
        saved = get_saved_ngrok_endpoint(server_dir)
        return TunnelStatus(
            "ngrok",
            TUNNEL_STATUS_NOT_RUNNING,
            f"Not running (last endpoint: {saved})" if saved else "Not running",
            endpoint=saved,
        )
    _, error = _extract_endpoint_or_error_from_log(server_dir)
    if error:
        return TunnelStatus(
            "ngrok", TUNNEL_STATUS_FAILED, f"Ngrok: {error}", pid=state["pid"]
        )
    # Only query the inspection API belonging to this launch. Never use another
    # server's API or promote a historical cached URL to live readiness.
    endpoint = get_ngrok_public_url(
        port,
        timeout=1,
        api_url=state["api_url"],
        local_host=state.get("local_host", "127.0.0.1"),
    )
    if endpoint:
        save_ngrok_endpoint(server_dir, endpoint)
        return TunnelStatus(
            "ngrok",
            TUNNEL_STATUS_READY,
            f"Tunnel ready: {endpoint}",
            endpoint=endpoint,
            pid=state["pid"],
        )
    return TunnelStatus(
        "ngrok",
        TUNNEL_STATUS_PROCESS_RUNNING,
        "Ngrok is running; waiting for a current public TCP endpoint",
        pid=state["pid"],
    )


def _ngrok_config(server_dir: Path, config_path: str | None) -> tuple[list[str], str]:
    candidates = (
        [Path(config_path).expanduser()]
        if config_path
        else [
            Path.home() / ".config/ngrok/ngrok.yml",
            Path.home() / ".ngrok2/ngrok.yml",
            Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))
            / "ngrok/ngrok.yml",
            Path.home() / "Library/Application Support/ngrok/ngrok.yml",
        ]
    )
    base = next((p for p in candidates if p.is_file()), None)
    if config_path and base is None:
        raise ValueError("Configured ngrok configuration file does not exist")
    version = "3"
    if base:
        match = re.search(
            r"^version:\s*[\"\']?([23])", base.read_text(encoding="utf-8"), re.MULTILINE
        )
        if not match:
            raise ValueError("ngrok configuration must specify version 2 or 3")
        version = match.group(1)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    address = f"127.0.0.1:{port}"
    overlay = server_dir / ".msm.ngrok.yml"
    indent = "  " if version == "3" else ""
    text = f'version: "{version}"\n' + ("agent:\n" if version == "3" else "")
    text += f'{indent}web_addr: "{address}"\n'
    overlay.write_text(text, encoding="utf-8")
    args = ["--config", str(base)] if base else []
    args.extend(["--config", str(overlay)])
    return args, f"http://{address}/api/tunnels"


def start_ngrok_agent(
    server_dir: Path,
    binary_path: str,
    port: int,
    logger,
    local_host: str = "127.0.0.1",
    config_path: str | None = None,
):
    resolved = resolve_ngrok_binary(binary_path)
    if not resolved:
        return (
            TunnelStatus(
                "ngrok",
                TUNNEL_STATUS_BINARY_MISSING,
                f"Ngrok binary '{binary_path}' was not found.",
            ),
            None,
        )
    if tunnel_state(server_dir, "ngrok"):
        return inspect_ngrok_status(server_dir, port, logger), None
    if tunnel_state(server_dir):
        return (
            TunnelStatus(
                "ngrok",
                TUNNEL_STATUS_FAILED,
                "Stop the existing tunnel before changing providers.",
            ),
            None,
        )
    if (
        not 1 <= int(port) <= 65535
        or not local_host
        or any(c.isspace() for c in local_host)
    ):
        return (
            TunnelStatus(
                "ngrok", TUNNEL_STATUS_FAILED, "Invalid local tunnel address."
            ),
            None,
        )
    server_dir.mkdir(parents=True, exist_ok=True)
    process = None
    try:
        config_args, api_url = _ngrok_config(server_dir, config_path)
        host = f"[{local_host}]" if ":" in local_host else local_host
        command = [
            resolved,
            "tcp",
            f"{host}:{port}",
            "--log",
            "stdout",
            "--log-format",
            "json",
            *config_args,
        ]
        # Installed Termux wrappers already arrange their own runtime. Avoid nesting chroots.
        with (server_dir / ".msm.ngrok.log").open("w", encoding="utf-8") as handle:
            process = subprocess.Popen(
                command,
                cwd=server_dir,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        record_tunnel(
            server_dir,
            "ngrok",
            process,
            api_url=api_url,
            local_host=local_host,
            local_port=port,
        )
        deadline = time.monotonic() + min(NGROK_TIMEOUT, 15)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                _, error = _extract_endpoint_or_error_from_log(server_dir)
                stop_tunnel_process(server_dir, process)
                return (
                    TunnelStatus(
                        "ngrok",
                        TUNNEL_STATUS_FAILED,
                        error
                        or _read_ngrok_log_tail(server_dir)
                        or f"Ngrok exited (code {process.returncode})",
                    ),
                    None,
                )
            status = inspect_ngrok_status(server_dir, port, logger)
            if status.state == TUNNEL_STATUS_FAILED:
                stop_tunnel_process(server_dir, process)
                return status, None
            if status.state == TUNNEL_STATUS_READY:
                return status, process
            time.sleep(0.2)
        return inspect_ngrok_status(server_dir, port, logger), process
    except (OSError, ValueError, psutil.Error) as exc:
        stop_tunnel_process(server_dir, process)
        return (
            TunnelStatus(
                "ngrok", TUNNEL_STATUS_FAILED, f"Could not start ngrok: {exc}"
            ),
            None,
        )


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
