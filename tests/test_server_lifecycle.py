from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.config import ConfigManager
from core.runtime import RuntimeManager
from core.server import ServerInstance
from db.manager import DatabaseManager
from utils.logging_utils import EnhancedLogger


@pytest.fixture
def test_env(tmp_path: Path):
    config_file = tmp_path / "config.json"
    db_file = tmp_path / "msm.db"
    log_file = tmp_path / "msm.log"
    logger = EnhancedLogger(log_file)
    config_manager = ConfigManager(config_file, logger)
    db_manager = DatabaseManager(db_file)
    runtime = RuntimeManager(config_manager, db_manager, logger)
    return config_manager, db_manager, logger, runtime, tmp_path


def test_server_instance_stop_regression_self_pid(test_env):
    config_manager, db_manager, logger, runtime, tmp_path = test_env
    config_manager.ensure_server("srv_stop_test")

    instance = ServerInstance("srv_stop_test", config_manager, db_manager, logger)
    # When server is not running, stop() must cleanly return without AttributeError on self.pid
    assert instance.stop(force=False) is False
    assert instance.stop(force=True) is False


def test_runtime_manager_get_instance_deduplication(test_env):
    config_manager, db_manager, logger, runtime, tmp_path = test_env
    config_manager.ensure_server("dedup_test")

    inst1 = runtime.get_instance("dedup_test")
    inst2 = runtime.get_instance("dedup_test")
    assert inst1 is inst2


def test_ensure_local_rcon_configured_on_apply(test_env):
    config_manager, db_manager, logger, runtime, tmp_path = test_env
    config_manager.ensure_server("rcon_test")

    instance = ServerInstance("rcon_test", config_manager, db_manager, logger)
    # Ensure server files and check that local RCON credentials were generated securely
    instance.apply_server_files()
    _cfg, server_cfg = instance.refresh_config()
    rcon_cfg = server_cfg.get("rcon", {})
    assert rcon_cfg.get("enabled") is True
    assert len(rcon_cfg.get("password", "")) > 10
    assert rcon_cfg.get("host") == "127.0.0.1"


def test_server_instance_unified_launch_and_restart(
    test_env, monkeypatch: pytest.MonkeyPatch
):
    config_manager, db_manager, logger, runtime, tmp_path = test_env
    config_manager.ensure_server("launch_test")

    instance = ServerInstance("launch_test", config_manager, db_manager, logger)

    # Configure server
    def updater(cfg):
        s = cfg["servers"]["launch_test"]
        s["server_flavor"] = "paper"
        s["server_version"] = "1.20.4"
        s["server_dir"] = str(tmp_path / "minecraft-launch_test")

    config_manager.mutate(updater)

    instance.server_dir.mkdir(parents=True, exist_ok=True)
    (instance.server_dir / "server.jar").write_bytes(b"dummy jar content" * 1000)

    # Mock Java binary and process backend start
    monkeypatch.setattr(
        "utils.system.get_java_path", lambda ver, cfg, logger=None: "python"
    )

    mock_backend = MagicMock()
    mock_backend.start.return_value = MagicMock(pid=os.getpid())
    mock_backend.is_running.side_effect = [False, True, True, True]
    mock_backend.get_pid.return_value = os.getpid()

    monkeypatch.setattr(instance, "get_backend", lambda: mock_backend)

    started = instance.start()
    assert started is True
    assert mock_backend.start.called
