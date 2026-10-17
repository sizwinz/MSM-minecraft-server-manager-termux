from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from core.config import ConfigManager
from ui.colors import ColorScheme
from utils.logging_utils import EnhancedLogger


def test_playit_wizard_polls_without_blocking_for_terminal_input(
    tmp_path, monkeypatch, capsys
):
    from unittest.mock import MagicMock
    from ui import cli

    instance = MagicMock()
    instance.tunnel_process.poll.return_value = None
    instance.playit_secret_file = tmp_path / ".msm.playit.secret"
    instance.playit_endpoint_file = tmp_path / ".msm.playit.endpoint"
    runtime = MagicMock()
    runtime.get_instance.return_value = instance
    config = MagicMock()
    config.load.return_value = {"servers": {"test": {"tunnel": {}}}}
    client = MagicMock()
    client.__enter__.return_value = client
    client.wait_for_claim.return_value = "new-agent-secret"
    monkeypatch.setattr(cli, "PlayitApiClient", lambda **kwargs: client)
    monkeypatch.setattr(cli, "resolve_playit_binary", lambda _: "playitd")
    save_config = MagicMock()

    def configure_mapping(*args):
        save_config.assert_called_once()
        return True

    monkeypatch.setattr(cli, "configure_playit_api_tunnel", configure_mapping)
    monkeypatch.setattr(cli, "save_tunnel_config", save_config)
    monkeypatch.setattr(cli, "pause", lambda: None)

    def unexpected_input(*args):
        pytest.fail(
            "New-agent claiming must poll while the browser is open, not wait on input"
        )

    monkeypatch.setattr("builtins.input", unexpected_input)
    cli.playit_setup_wizard(runtime, config, "test", MagicMock())
    client.wait_for_claim.assert_called_once()
    client.prepare_claim.assert_not_called()
    assert instance.playit_secret_file.read_text() == "new-agent-secret"
    assert "Keep MSM running" in capsys.readouterr().out
    save_config.assert_called_once()


@pytest.mark.parametrize("daemon_running", [True, False])
def test_playit_setup_retry_keeps_linked_credentials(
    tmp_path, monkeypatch, daemon_running
):
    from unittest.mock import MagicMock
    from ui import cli

    instance = MagicMock()
    instance.playit_secret_file = tmp_path / "secret"
    instance.playit_secret_file.write_text("existing-secret")
    instance.tunnel_process = MagicMock() if daemon_running else None
    if daemon_running:
        instance.tunnel_process.poll.return_value = None
    runtime = MagicMock()
    runtime.get_instance.return_value = instance
    config = MagicMock()
    config.load.return_value = {"servers": {"test": {"tunnel": {}}}}
    client = MagicMock()
    client.__enter__.return_value = client
    monkeypatch.setattr(cli, "PlayitApiClient", lambda **kwargs: client)
    monkeypatch.setattr(cli, "resolve_playit_binary", lambda _: "playitd")
    monkeypatch.setattr("builtins.input", lambda _: "n")
    monkeypatch.setattr(cli, "pause", lambda: None)
    save_config = MagicMock()
    mapping = MagicMock(return_value=False)
    monkeypatch.setattr(cli, "save_tunnel_config", save_config)
    monkeypatch.setattr(cli, "configure_playit_api_tunnel", mapping)

    cli.playit_setup_wizard(runtime, config, "test", MagicMock())

    save_config.assert_called_once()
    assert mapping.call_count == int(daemon_running)
    client.wait_for_claim.assert_not_called()
    assert instance.playit_secret_file.read_text() == "existing-secret"


@pytest.mark.parametrize("failure", ["registration_delay", "outdated", "other"])
def test_playit_mapping_retries_only_version_registration(
    tmp_path, monkeypatch, failure
):
    from unittest.mock import MagicMock
    from ui import cli

    instance = MagicMock()
    instance.playit_secret_file = tmp_path / "secret"
    instance.playit_secret_file.write_text("existing-secret")
    instance.playit_endpoint_file = tmp_path / "endpoint"
    instance.get_server_port.return_value = 25565
    config = MagicMock()
    saved = {"servers": {"test": {"tunnel": {}}}}
    config.load.return_value = saved
    config.mutate.side_effect = lambda update: update(saved)
    client = MagicMock()
    client.__enter__.return_value = client
    client.agent_rundata.return_value = {"agent_id": "agent-id"}
    version_error = cli.PlayitApiError("AgentVersionTooOld")
    if failure == "registration_delay":
        client.create_or_update_tunnel.side_effect = [
            version_error,
            version_error,
            ("tunnel-id", "test.ply.gg:1234"),
        ]
    else:
        client.create_or_update_tunnel.side_effect = (
            version_error
            if failure == "outdated"
            else cli.PlayitApiError("OverPortLimit")
        )
    monkeypatch.setattr(cli, "PlayitApiClient", lambda **kwargs: client)
    session = MagicMock(return_value=None)
    monkeypatch.setattr(cli, "load_playit_session", session)
    sleep = MagicMock()
    monkeypatch.setattr(cli.time, "sleep", sleep)
    logger = MagicMock()

    result = cli.configure_playit_api_tunnel(instance, config, "test", logger)

    assert result is (failure == "registration_delay")
    assert (
        client.create_or_update_tunnel.call_count
        == {"registration_delay": 3, "outdated": 5, "other": 1}[failure]
    )
    assert (
        sleep.call_count
        == {"registration_delay": 2, "outdated": 4, "other": 0}[failure]
    )
    assert session.call_count == int(failure == "other")
    if result:
        assert saved["servers"]["test"]["tunnel"]["playit_tunnel_id"] == "tunnel-id"
        assert instance.playit_endpoint_file.read_text() == "test.ply.gg:1234"
    else:
        config.mutate.assert_not_called()
        if failure == "outdated":
            assert "re-linking is not required" in logger.log.call_args.args[1]
    assert instance.playit_secret_file.read_text() == "existing-secret"


def test_cancelled_playit_relink_preserves_existing_credentials(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    from ui import cli

    instance = MagicMock()
    instance.playit_secret_file = tmp_path / ".msm.playit.secret"
    instance.playit_secret_file.write_text("existing-agent-secret")
    runtime = MagicMock()
    runtime.get_instance.return_value = instance
    config = MagicMock()
    config.load.return_value = {"servers": {"test": {"tunnel": {}}}}
    client = MagicMock()
    client.__enter__.return_value = client
    client.wait_for_claim.side_effect = KeyboardInterrupt
    monkeypatch.setattr(cli, "PlayitApiClient", lambda **kwargs: client)
    monkeypatch.setattr(cli, "resolve_playit_binary", lambda _: "playitd")
    monkeypatch.setattr("builtins.input", lambda _: "y")
    cli.playit_setup_wizard(runtime, config, "test", MagicMock())
    assert instance.playit_secret_file.read_text() == "existing-agent-secret"
    instance.stop_tunnel.assert_not_called()
    config.mutate.assert_not_called()


def test_colors_no_color_support(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NO_COLOR", "1")
    scheme = ColorScheme()
    scheme.auto_configure()
    assert scheme.RED == ""
    assert scheme.GREEN == ""
    assert scheme.CYAN == ""
    assert scheme.BOLD == ""


def test_colors_ascii_fallback_mode():
    scheme = ColorScheme()
    scheme.enable_ascii_mode()
    assert scheme.BOX_TL == "+"
    assert scheme.BOX_H == "-"
    assert scheme.CHECK == "[OK]"
    assert scheme.CROSS == "[X]"


def test_cli_diagnostics_flag():
    repo_root = Path(__file__).resolve().parents[1]
    res = subprocess.run(
        [sys.executable, str(repo_root / "msm.py"), "--diagnostics"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "capabilities" in res.stdout
    assert "os_type" in res.stdout


def test_corrupted_config_recovery(tmp_path: Path):
    config_file = tmp_path / "config.json"
    config_file.write_text("{ this is invalid json content :", encoding="utf-8")

    logger = EnhancedLogger(tmp_path / "msm.log")
    mgr = ConfigManager(config_file, logger)
    loaded = mgr.load()

    # Loaded must fallback to default config
    assert "servers" in loaded
    # Corrupted file must be backed up
    bak_files = list(tmp_path.glob("config.json.bak_*"))
    assert len(bak_files) >= 1


def test_select_current_server_displays_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    from core.runtime import RuntimeManager
    from db.manager import DatabaseManager
    from ui.cli import select_current_server

    config_file = tmp_path / "config.json"
    logger = EnhancedLogger(tmp_path / "msm.log")
    cfg_mgr = ConfigManager(config_file, logger)
    db_mgr = DatabaseManager(tmp_path / "msm.db")
    runtime = RuntimeManager(cfg_mgr, db_mgr, logger)

    cfg_mgr.ensure_server("srv_alpha")
    cfg_mgr.ensure_server("srv_beta")

    # Select server 1
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")
    chosen = select_current_server(cfg_mgr, logger, runtime=runtime)

    assert chosen == "srv_alpha"
    captured = capsys.readouterr().out
    assert "srv_alpha" in captured
    assert "srv_beta" in captured
    assert "Directory:" in captured


def test_startup_server_picker_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
):
    from core.runtime import RuntimeManager
    from db.manager import DatabaseManager
    from ui.cli import startup_server_picker

    config_file = tmp_path / "config.json"
    logger = EnhancedLogger(tmp_path / "msm.log")
    cfg_mgr = ConfigManager(config_file, logger)
    db_mgr = DatabaseManager(tmp_path / "msm.db")
    runtime = RuntimeManager(cfg_mgr, db_mgr, logger)

    cfg_mgr.ensure_server("srv_pick_test")

    monkeypatch.setattr("ui.cli.clear_screen", lambda: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "1")

    selected = startup_server_picker(runtime, cfg_mgr, logger)
    assert selected == "srv_pick_test"

    captured = capsys.readouterr().out
    assert "Select Server to Manage" in captured
    assert "Directory:" in captured


def test_debug_logging_silenced_by_default_and_toggleable(tmp_path: Path, capsys):
    logger = EnhancedLogger(tmp_path / "msm.log")

    # 1. By default, DEBUG log is written to file but NOT printed to terminal
    logger.log("DEBUG", "Hidden debug trace message")
    captured = capsys.readouterr().out
    assert "Hidden debug trace message" not in captured

    # INFO / SUCCESS / WARNING should still print
    logger.log("INFO", "Visible info message")
    captured_info = capsys.readouterr().out
    assert "Visible info message" in captured_info

    # 2. When debug is toggled ON, DEBUG log is printed to terminal
    logger.set_debug(True)
    logger.log("DEBUG", "Visible debug trace message")
    captured_dbg = capsys.readouterr().out
    assert "Visible debug trace message" in captured_dbg

    # 3. toggle_debug flips state
    new_state = logger.toggle_debug()
    assert new_state is False
    assert logger.is_debug_enabled() is False


def test_visible_len_strips_ansi():
    from ui.colors import visible_len

    raw = "\033[38;5;208m›\033[0m [1] Start / Stop"
    assert visible_len(raw) == len("› [1] Start / Stop")
    plain = "Clean ASCII string"
    assert visible_len(plain) == len(plain)


def test_colors_palette_and_geometric_glyphs():
    from ui.colors import ColorScheme

    scheme = ColorScheme()
    assert "\033[38;5;208m" in scheme.ACCENT
    assert "\033[97m" in scheme.FG
    assert "\033[90m" in scheme.DIM
    assert "\033[38;5;78m" in scheme.OK
    assert "\033[38;5;214m" in scheme.WARN
    assert "\033[38;5;203m" in scheme.ERR

    assert scheme.DOT_ON == "●"
    assert scheme.DOT_OFF == "○"
    assert scheme.POINTER == "›"
    assert scheme.SQUARE == "▪"
    assert scheme.TREE_BRANCH == "├─"
    assert scheme.TREE_CORNER == "└─"


def test_prompt_safe_eof_and_interrupt(monkeypatch: pytest.MonkeyPatch):
    from ui.cli import prompt

    def raise_eof(_prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)
    assert prompt("test: ", default="default_val") == ""

    def raise_sigint(_prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", raise_sigint)
    assert prompt("test: ", default="fallback") == ""


def test_render_server_hud(tmp_path: Path, capsys):
    from unittest.mock import MagicMock
    from core.config import ConfigManager
    from ui.cli import render_server_hud

    config_file = tmp_path / "config.json"
    logger = EnhancedLogger(tmp_path / "msm.log")
    cfg = ConfigManager(config_file, logger)
    cfg.ensure_server("hud_test")

    instance = MagicMock()
    instance.is_running.return_value = True
    instance.current_pid.return_value = 4912
    instance.get_server_port.return_value = 25565
    instance.get_connection_info.return_value = {
        "tunnel_provider": "playit",
        "tunnel_url": "test-endpoint.ply.gg",
        "tunnel_enabled": True,
        "tunnel_status": "ready",
    }
    runtime = MagicMock()
    runtime.get_instance.return_value = instance

    render_server_hud("hud_test", runtime, cfg)
    out = capsys.readouterr().out
    assert "hud_test" in out
    assert "ONLINE" in out
    assert "4912" in out
    assert "25565" in out
    assert "test-endpoint.ply.gg" in out
