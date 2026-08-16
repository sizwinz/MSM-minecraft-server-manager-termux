from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from core.config import ConfigManager
from ui.colors import ColorScheme
from utils.logging_utils import EnhancedLogger


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
