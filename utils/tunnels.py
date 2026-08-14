"""Tunnel log parsing helpers."""

from __future__ import annotations

import re
from pathlib import Path

PLAYIT_TUNNEL_ADDRESS_PATTERN = re.compile(
    r"tunnel_address=(?P<endpoint>[^\s,]+)",
    re.IGNORECASE,
)
PLAYIT_ENDPOINT_PATTERN = re.compile(
    (
        r"\b(?P<endpoint>"
        r"(?:[a-z0-9-]+\.)+(?:playit\.gg|ply\.gg)(?::\d+)?"
        r"|(?:\d{1,3}\.){3}\d{1,3}:\d+"
        r")\b"
    ),
    re.IGNORECASE,
)
PLAYIT_CLAIM_URL_PATTERN = re.compile(
    (
        r"(?P<url>"
        r"https?://"
        r"(?:[a-z0-9-]+\.)*playit\.gg"
        r"/claim/"
        r"[A-Za-z0-9_-]+"
        r"/?"
        r")"
    ),
    re.IGNORECASE,
)


ANSI_ESCAPE_PATTERN = re.compile(r"\x1B(?:\[[0-9;]*[a-zA-Z]|\(B|\)0|#\d|[=>]|7|8)")


def extract_last_non_empty_line(text: str) -> str | None:
    text = ANSI_ESCAPE_PATTERN.sub("", text)
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def build_playit_claim_generate_command(
    binary_path: str | Path, socket_path: str | Path | None = None
) -> list[str]:
    cmd = [str(binary_path)]
    if socket_path and Path(binary_path).name == "playit-cli":
        cmd.extend(["--socket-path", str(socket_path)])
    cmd.extend(["claim", "generate"])
    return cmd


def build_playit_claim_url_command(
    binary_path: str | Path, claim_code: str, socket_path: str | Path | None = None
) -> list[str]:
    cmd = [str(binary_path)]
    if socket_path and Path(binary_path).name == "playit-cli":
        cmd.extend(["--socket-path", str(socket_path)])
    cmd.extend(["claim", "url", claim_code])
    return cmd


def build_playit_claim_exchange_command(
    binary_path: str | Path,
    claim_code: str,
    secret_path: str | Path | None = None,
    socket_path: str | Path | None = None,
) -> list[str]:
    command = [str(binary_path)]
    if socket_path and Path(binary_path).name == "playit-cli":
        command.extend(["--socket-path", str(socket_path)])
    # In 1.0.6+, playit-cli does not accept --secret-path. The daemon manages it via IPC.
    # We still accept secret_path for backwards compatibility with older playit versions if needed,
    # but playit 0.15 uses --secret_path while 1.0.6 uses --socket-path
    if secret_path and not socket_path:
        command.extend(["--secret_path", str(secret_path)])
    command.extend(["claim", "exchange", claim_code])
    return command


def build_playit_setup_command(
    binary_path: str | Path,
    socket_path: str | Path | None = None,
) -> list[str]:
    command = [str(binary_path)]
    if socket_path and Path(binary_path).name == "playit-cli":
        command.extend(["--socket-path", str(socket_path)])
    command.append("setup")
    return command


def build_playit_start_command(
    binary_path: str | Path,
    secret_path: str | Path | None = None,
    socket_path: str | Path | None = None,
) -> list[str]:
    bin_name = Path(binary_path).name
    command = [str(binary_path)]
    if secret_path:
        command.extend(["--secret-path", str(secret_path)])
    if socket_path and bin_name == "playitd":
        command.extend(["--socket-path", str(socket_path)])
    return command


def extract_playit_public_endpoint(log_text: str) -> str | None:
    for line in reversed(log_text.splitlines()):
        tunnel_match = PLAYIT_TUNNEL_ADDRESS_PATTERN.search(line)
        if tunnel_match:
            return tunnel_match.group("endpoint")
        endpoint_match = PLAYIT_ENDPOINT_PATTERN.search(line)
        if endpoint_match:
            return endpoint_match.group("endpoint")
    return None


def extract_playit_claim_url(log_text: str) -> str | None:
    for line in reversed(log_text.splitlines()):
        match = PLAYIT_CLAIM_URL_PATTERN.search(line)
        if match:
            return match.group("url")
    return None
