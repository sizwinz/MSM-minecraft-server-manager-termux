"""Tests for the playit provider module (utils/playit.py)."""

from __future__ import annotations


from unittest.mock import patch

import pytest

from core.constants import (
    PLAYIT_SECRET_FILE_NAME,
    TUNNEL_STATUS_BINARY_MISSING,
    TUNNEL_STATUS_NOT_RUNNING,
    TUNNEL_STATUS_SECRET_MISSING,
)
from utils.playit import (
    build_playit_mapping_hint,
    diagnose_playit,
    get_saved_playit_endpoint,
    inspect_playit_status,
    resolve_playit_binary,
    save_playit_endpoint,
    start_playit_agent,
)


@pytest.fixture
def tmp_server_dir(tmp_path):
    return tmp_path


def test_resolve_playit_binary_returns_none_when_nothing_found():
    with patch("shutil.which", return_value=None):
        assert resolve_playit_binary("nonexistent") is None


def test_resolve_playit_binary_custom_path_takes_priority(tmp_path):
    fake_bin = tmp_path / "custom-playit"
    fake_bin.write_text("#!/bin/sh\necho ok")
    with patch("shutil.which", return_value=str(fake_bin)):
        result = resolve_playit_binary(str(fake_bin))
    assert result == str(fake_bin)


def test_resolve_windows_playit_controller_uses_sibling_daemon(tmp_path, monkeypatch):
    cli = tmp_path / "playit-cli.exe"
    daemon = tmp_path / "playitd.exe"
    cli.touch()
    daemon.touch()
    monkeypatch.setattr("utils.playit.shutil.which", lambda _: None)
    assert resolve_playit_binary(str(cli)) == str(daemon)


def test_save_and_get_playit_endpoint(tmp_server_dir):
    assert get_saved_playit_endpoint(tmp_server_dir) is None
    save_playit_endpoint(tmp_server_dir, "mc.example.playit.gg:12345")
    assert get_saved_playit_endpoint(tmp_server_dir) == "mc.example.playit.gg:12345"


def test_inspect_playit_status_not_running(tmp_server_dir):
    status = inspect_playit_status(tmp_server_dir)
    assert status.provider == "playit"
    assert status.state == TUNNEL_STATUS_NOT_RUNNING
    assert status.endpoint is None


def test_inspect_playit_status_with_saved_endpoint(tmp_server_dir):
    save_playit_endpoint(tmp_server_dir, "saved.playit.gg:9999")
    status = inspect_playit_status(tmp_server_dir)
    assert status.state == TUNNEL_STATUS_NOT_RUNNING
    assert status.endpoint == "saved.playit.gg:9999"
    assert "last endpoint" in status.message


def test_start_playit_agent_missing_binary(tmp_server_dir):
    secret = tmp_server_dir / PLAYIT_SECRET_FILE_NAME
    secret.write_text("secret-data")
    with patch("utils.playit.resolve_playit_binary", return_value=None):
        status, _ = start_playit_agent(tmp_server_dir, "nonexistent", secret, None)
    assert status.state == TUNNEL_STATUS_BINARY_MISSING


def test_start_playit_agent_missing_secret(tmp_server_dir):
    secret = tmp_server_dir / PLAYIT_SECRET_FILE_NAME
    with patch("utils.playit.resolve_playit_binary", return_value="/usr/bin/playit"):
        status, _ = start_playit_agent(tmp_server_dir, "playit", secret, None)
    assert status.state == TUNNEL_STATUS_SECRET_MISSING


def test_build_playit_mapping_hint_formats_correctly():
    hint = build_playit_mapping_hint("tcp", "127.0.0.1", 25565)
    assert "TCP" in hint
    assert "127.0.0.1" in hint
    assert "25565" in hint


def test_diagnose_playit_binary_missing(tmp_server_dir):
    config = {"binary_path": "nonexistent", "protocol": "tcp"}
    with patch("utils.playit.resolve_playit_binary", return_value=None):
        checks = diagnose_playit(tmp_server_dir, config, 25565)
    binary_check = next(c for c in checks if c.name == "Playit binary")
    assert binary_check.ok is False


def test_diagnose_playit_pocketmine_wrong_protocol(tmp_server_dir):
    config = {"binary_path": "playit", "protocol": "tcp"}
    with patch("utils.playit.resolve_playit_binary", return_value="/usr/bin/playit"):
        checks = diagnose_playit(
            tmp_server_dir, config, 19132, server_flavor="pocketmine"
        )
    pmmp_check = next(c for c in checks if c.name == "PocketMine protocol")
    assert pmmp_check.ok is False
    assert "UDP" in pmmp_check.detail
