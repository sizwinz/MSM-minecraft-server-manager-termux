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
