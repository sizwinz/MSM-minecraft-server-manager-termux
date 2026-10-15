"""Tests for the ngrok provider module (utils/ngrok.py)."""

from __future__ import annotations


from unittest.mock import patch

import pytest

from core.constants import (
    TUNNEL_STATUS_BINARY_MISSING,
    TUNNEL_STATUS_NOT_RUNNING,
)
from utils.ngrok import (
    diagnose_ngrok,
    get_saved_ngrok_endpoint,
    inspect_ngrok_status,
    resolve_ngrok_binary,
    save_ngrok_endpoint,
    start_ngrok_agent,
)


@pytest.fixture
def tmp_server_dir(tmp_path):
    return tmp_path


def test_resolve_ngrok_binary_returns_none_when_nothing_found():
    with patch("shutil.which", return_value=None), patch(
        "pathlib.Path.is_file", return_value=False
    ):
        assert resolve_ngrok_binary("nonexistent") is None


def test_resolve_ngrok_binary_custom_path(tmp_path):
    fake_bin = tmp_path / "custom-ngrok"
    fake_bin.write_text("#!/bin/sh\necho ok")
    with patch("shutil.which", return_value=str(fake_bin)):
        result = resolve_ngrok_binary(str(fake_bin))
    assert result == str(fake_bin)


def test_resolve_downloaded_windows_ngrok_outside_path(tmp_path, monkeypatch):
    from pathlib import Path

    binary = tmp_path / "bin/ngrok.exe"
    binary.parent.mkdir()
    binary.touch()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda _: None)
    monkeypatch.setattr("utils.ngrok.os.access", lambda *args: True)
    assert resolve_ngrok_binary() == str(binary)


def test_save_and_get_ngrok_endpoint(tmp_server_dir):
    assert get_saved_ngrok_endpoint(tmp_server_dir) is None
    save_ngrok_endpoint(tmp_server_dir, "tcp://0.tcp.ngrok.io:12345")
    assert get_saved_ngrok_endpoint(tmp_server_dir) == "tcp://0.tcp.ngrok.io:12345"


def test_inspect_ngrok_status_not_running(tmp_server_dir):
    status = inspect_ngrok_status(tmp_server_dir, 25565)
    assert status.provider == "ngrok"
    assert status.state == TUNNEL_STATUS_NOT_RUNNING
    assert status.endpoint is None


def test_inspect_ngrok_status_with_saved_endpoint(tmp_server_dir):
    save_ngrok_endpoint(tmp_server_dir, "tcp://saved.ngrok.io:9999")
    status = inspect_ngrok_status(tmp_server_dir, 25565)
    assert status.state == TUNNEL_STATUS_NOT_RUNNING
    assert status.endpoint == "tcp://saved.ngrok.io:9999"
    assert "last endpoint" in status.message


def test_start_ngrok_agent_missing_binary(tmp_server_dir):
    with patch("utils.ngrok.resolve_ngrok_binary", return_value=None):
        status, _ = start_ngrok_agent(tmp_server_dir, "nonexistent", 25565, None)
    assert status.state == TUNNEL_STATUS_BINARY_MISSING


def test_diagnose_ngrok_binary_missing(tmp_server_dir):
    config = {"binary_path": "nonexistent", "protocol": "tcp"}
    with patch("utils.ngrok.resolve_ngrok_binary", return_value=None):
        checks = diagnose_ngrok(tmp_server_dir, config, 25565)
    binary_check = next(c for c in checks if c.name == "Ngrok binary")
    assert binary_check.ok is False


def test_diagnose_ngrok_wrong_protocol(tmp_server_dir):
    config = {"binary_path": "ngrok", "protocol": "udp"}
    with patch("utils.ngrok.resolve_ngrok_binary", return_value="/usr/bin/ngrok"):
        checks = diagnose_ngrok(tmp_server_dir, config, 25565)
    proto_check = next(c for c in checks if c.name == "Protocol")
    assert proto_check.ok is False
    assert "TCP only" in proto_check.detail


def test_diagnose_ngrok_pocketmine_incompatible(tmp_server_dir):
    config = {"binary_path": "ngrok", "protocol": "tcp"}
    with patch("utils.ngrok.resolve_ngrok_binary", return_value="/usr/bin/ngrok"):
        checks = diagnose_ngrok(
            tmp_server_dir, config, 19132, server_flavor="pocketmine"
        )
    pmmp_check = next(c for c in checks if c.name == "PocketMine compatibility")
    assert pmmp_check.ok is False
    assert "UDP" in pmmp_check.detail
