from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import zipfile

import psutil
import pytest

from core.config import ConfigManager
from core.server import ServerInstance
from db.manager import DatabaseManager
from utils import network
from utils.archive import create_backup_archive, restore_world_backup
from utils.logging_utils import EnhancedLogger
from utils.ngrok import _extract_endpoint_or_error_from_log, inspect_ngrok_status
from utils.playit import _matching_mapping, inspect_playit_status, playit_socket_path
from utils.system import get_system_info
from utils.tunnel_process import record_tunnel, stop_tunnel_process, tunnel_state


@pytest.fixture
def instance(tmp_path):
    logger = EnhancedLogger(tmp_path / "msm.log")
    config = ConfigManager(tmp_path / "config.json", logger)
    config.ensure_server("test")

    def setup(cfg):
        cfg["servers"]["test"].update(
            server_dir=str(tmp_path / "server"), server_flavor="vanilla"
        )
        cfg["servers"]["test"]["server_settings"]["port"] = 26565

    config.mutate(setup)
    server = ServerInstance(
        "test", config, DatabaseManager(tmp_path / "msm.db"), logger
    )
    server.ensure_server_files()
    return server


def test_java_configured_port_is_applied(instance):
    instance.apply_server_files()
    text = (instance.server_dir / "server.properties").read_text()
    assert "server-port=26565" in text
    assert "\nport=" not in text


def test_cpu_restriction_does_not_discard_memory(monkeypatch):
    monkeypatch.setattr(
        "psutil.virtual_memory",
        lambda: SimpleNamespace(total=6 * 1024**3, available=4 * 1024**3),
    )
    monkeypatch.setattr("psutil.cpu_percent", MagicMock(side_effect=PermissionError))
    info = get_system_info()
    assert info["total_ram_mb"] == 6144
    assert info["available_ram_mb"] == 4096
    assert info["cpu_usage"] is None
    assert info["platform"] != "unknown"


def test_restore_replaces_whole_world_without_touching_server_files(tmp_path):
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"original")
    (tmp_path / "server.jar").write_bytes(b"keep")
    backup = tmp_path / "backup.zip"
    create_backup_archive(tmp_path, backup, [world])
    (world / "level.dat").write_bytes(b"changed")
    (world / "obsolete.mca").write_bytes(b"remove")
    restore_world_backup(backup, tmp_path)
    assert (world / "level.dat").read_bytes() == b"original"
    assert not (world / "obsolete.mca").exists()
    assert (tmp_path / "server.jar").read_bytes() == b"keep"


@pytest.mark.parametrize("name", ["../outside", "server.jar", "plugins/evil.py"])
def test_unsafe_restore_keeps_existing_world(tmp_path, name):
    world = tmp_path / "world"
    world.mkdir()
    (world / "level.dat").write_bytes(b"untouched")
    backup = tmp_path / "bad.zip"
    with zipfile.ZipFile(backup, "w") as archive:
        archive.writestr("world/level.dat", b"bad")
        archive.writestr(name, b"bad")
    with pytest.raises(ValueError):
        restore_world_backup(backup, tmp_path)
    assert (world / "level.dat").read_bytes() == b"untouched"


def test_restore_rolls_back_if_second_world_swap_fails(tmp_path, monkeypatch):
    worlds = [tmp_path / "world", tmp_path / "world_nether"]
    for world in worlds:
        world.mkdir()
        (world / "level.dat").write_text("before")
    backup = tmp_path / "backup.zip"
    create_backup_archive(tmp_path, backup, worlds)
    for world in worlds:
        (world / "level.dat").write_text("current")
    replace = Path.replace

    def fail(path, target):
        if path.parent.name == "new" and path.name == "world_nether":
            raise OSError("simulated rename failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError):
        restore_world_backup(backup, tmp_path)
    assert all((world / "level.dat").read_text() == "current" for world in worlds)


def test_failed_live_backup_resumes_saving(instance, monkeypatch):
    instance.apply_server_files()
    (instance.server_dir / "world").mkdir()
    monkeypatch.setattr(instance, "is_running", lambda: True)
    client = MagicMock()
    client.__enter__.return_value = client
    client.command.side_effect = [
        "Automatic saving is now disabled",
        OSError("lost connection"),
    ]
    monkeypatch.setattr("core.server.RCONClient", lambda *a, **k: client)
    resume = MagicMock(return_value=True)
    monkeypatch.setattr(instance, "send_command", resume)
    with pytest.raises(OSError):
        instance.create_backup()
    resume.assert_called_once_with("save-on")
    assert not list(instance.backup_dir.glob("*.zip"))


def test_interrupted_download_preserves_installed_jar(tmp_path, monkeypatch):
    target = tmp_path / "server.jar"
    target.write_bytes(b"working server")
    monkeypatch.setattr(
        network,
        "_determine_download",
        lambda *a, **k: ("https://example.invalid/server.jar", "server.jar"),
    )
    monkeypatch.setattr(network, "create_robust_session", MagicMock)

    def chunks(**kwargs):
        yield b"partial"
        raise OSError("interrupted")

    response = SimpleNamespace(iter_content=chunks)
    monkeypatch.setattr(network, "safe_request", lambda *a, **k: response)
    with pytest.raises(OSError):
        network.download_server_binary("vanilla", "1.21.1", {}, tmp_path)
    assert target.read_bytes() == b"working server"
    assert not list(tmp_path.glob("*.download"))


def test_hash_mismatch_preserves_installed_jar(tmp_path, monkeypatch):
    target = tmp_path / "server.jar"
    target.write_bytes(b"working server")
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "test")
    monkeypatch.setattr(
        network,
        "_determine_download",
        lambda *a, **k: ("https://example.invalid/server.jar", "server.jar"),
    )
    monkeypatch.setattr(network, "create_robust_session", MagicMock)
    monkeypatch.setattr(
        network,
        "safe_request",
        lambda *a, **k: SimpleNamespace(iter_content=lambda **kw: [content.getvalue()]),
    )
    with pytest.raises(RuntimeError, match="sha256"):
        network.download_server_binary(
            "paper", "1.21.1", {"sha256": "0" * 64}, tmp_path
        )
    assert target.read_bytes() == b"working server"


def test_24_hour_metrics_exclude_old_iso_timestamp(tmp_path, monkeypatch):
    now = datetime(2026, 10, 15, 14)

    class Clock:
        @staticmethod
        def now():
            return now

    monkeypatch.setattr("db.manager.datetime", Clock)
    db = DatabaseManager(tmp_path / "db.sqlite")
    with db.get_connection() as conn:
        for age, ram in ((30, 99), (1, 7)):
            conn.execute(
                "INSERT INTO performance_metrics(server_name,timestamp,ram_usage,cpu_usage) "
                "VALUES (?,?,?,?)",
                ("test", (now - timedelta(hours=age)).isoformat(), ram, 1),
            )
    assert db.get_server_statistics("test")["avg_ram_usage_24h"] == 7


def test_owned_tunnel_can_be_stopped_and_pid_recycling_is_rejected(tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path
    )
    try:
        record_tunnel(
            tmp_path, "ngrok", child, api_url="http://127.0.0.1:1/api/tunnels"
        )
        assert tunnel_state(tmp_path, "ngrok")["pid"] == child.pid
        path = tmp_path / ".msm.tunnel.json"
        state = json.loads(path.read_text())
        state["cmdline"] = ["another process"]
        path.write_text(json.dumps(state))
        assert tunnel_state(tmp_path) is None
        assert child.poll() is None
        assert stop_tunnel_process(tmp_path, child)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_record_tunnel_handles_transient_empty_cmdline_during_spawn(tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path
    )
    try:
        calls = []
        real_cmdline = psutil.Process.cmdline

        def transient_cmdline(self):
            if not calls:
                calls.append(1)
                return []
            return real_cmdline(self)

        with patch.object(psutil.Process, "cmdline", transient_cmdline):
            record_tunnel(tmp_path, "ngrok", child)
            state = tunnel_state(tmp_path, "ngrok")
            assert state is not None
            assert state["pid"] == child.pid
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()


def test_ngrok_cache_does_not_make_running_agent_ready(tmp_path, monkeypatch):
    (tmp_path / ".msm.ngrok.endpoint").write_text("tcp://stale.example:123")
    monkeypatch.setattr(
        "utils.ngrok.tunnel_state",
        lambda *a: {"pid": os.getpid(), "api_url": "http://127.0.0.1:1/api/tunnels"},
    )
    monkeypatch.setattr("utils.ngrok.get_ngrok_public_url", lambda *a, **k: None)
    status = inspect_ngrok_status(tmp_path, 25565)
    assert status.state != "ready"
    assert status.endpoint is None


def test_ngrok_android_hardware_warning_is_not_fatal(tmp_path):
    (tmp_path / ".msm.ngrok.log").write_text(
        json.dumps({"lvl": "eror", "msg": "no usable hardware address found"})
    )
    assert _extract_endpoint_or_error_from_log(tmp_path) == (None, None)


def test_ngrok_latest_error_overrides_previous_endpoint(tmp_path):
    lines = [
        {"url": "tcp://old.example:1"},
        {"lvl": "eror", "err": "ERR_NGROK_108: session limit", "msg": "failed"},
    ]
    (tmp_path / ".msm.ngrok.log").write_text("\n".join(map(json.dumps, lines)))
    endpoint, error = _extract_endpoint_or_error_from_log(tmp_path)
    assert endpoint is None
    assert "ERR_NGROK_108" in error


def test_playit_never_uses_cached_endpoint_as_ready(tmp_path, monkeypatch):
    (tmp_path / ".msm.playit.endpoint").write_text("old.ply.gg:123")
    monkeypatch.setattr("utils.playit.tunnel_state", lambda *a: {"pid": 123})
    status = inspect_playit_status(tmp_path)
    assert status.state != "ready"
    assert status.endpoint is None


@pytest.mark.parametrize(
    "change",
    [
        {"local_port": 25566},
        {"proto": "udp"},
        {"disabled": "by-user"},
        {"local_ip": "192.0.2.1"},
        {"id": "other"},
    ],
)
def test_playit_rejects_other_or_disabled_mappings(change):
    mapping = {
        "id": "mine",
        "local_ip": "127.0.0.1",
        "local_port": 25565,
        "proto": "tcp",
    }
    mapping.update(change)
    assert not _matching_mapping(
        mapping, {"playit_tunnel_id": "mine", "local_port": 25565}
    )


def test_playit_socket_path_stays_short_for_long_server_paths(tmp_path):
    socket_path = playit_socket_path(tmp_path / ("long" * 35))
    if os.name != "nt":
        assert len(os.fsencode(socket_path)) < 108


@pytest.mark.parametrize("address", ["127.0.0.1:125565", "127.0.0.2:25565"])
def test_ngrok_does_not_return_other_servers_endpoint(monkeypatch, address):
    response = MagicMock()
    response.json.return_value = {
        "tunnels": [
            {
                "proto": "tcp",
                "config": {"addr": address},
                "public_url": "tcp://wrong.example:123",
            }
        ]
    }
    session = MagicMock()
    session.__enter__.return_value = session
    session.get.return_value = response
    monkeypatch.setattr(network.requests, "Session", lambda: session)
    assert network.get_ngrok_public_url(25565) is None
    assert session.get.call_args.kwargs["timeout"] == network.NGROK_TIMEOUT


def test_playit_handshake_reset_stays_connecting(tmp_path, monkeypatch):
    (tmp_path / ".msm.playit.log").write_text("session expired reason=SessionNotSetup")
    monkeypatch.setattr("utils.playit.tunnel_state", lambda *a: {"pid": 123})
    status = inspect_playit_status(tmp_path)
    assert status.state == "process_running"
    assert status.endpoint is None


def test_playit_invalid_key_is_actionable_failure(tmp_path, monkeypatch):
    (tmp_path / ".msm.playit.log").write_text(
        "configured agent secret is no longer valid error=InvalidAgentKey"
    )
    monkeypatch.setattr("utils.playit.tunnel_state", lambda *a: {"pid": 123})
    status = inspect_playit_status(tmp_path)
    assert status.state == "failed"
    assert "InvalidAgentKey" in status.message


def test_playit_correct_mapping_is_ready_and_others_are_ignored(tmp_path, monkeypatch):
    (tmp_path / ".msm.playit.secret").write_text("test-secret")
    (tmp_path / ".msm.playit.log").write_text("playit connected; tunnels loaded")
    monkeypatch.setattr(
        "utils.playit.tunnel_state",
        lambda *a: {"pid": 123, "local_port": 26565, "protocol": "udp"},
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.agent_rundata.return_value = {
        "tunnels": [
            {
                "id": "wrong",
                "local_port": 25565,
                "local_ip": "127.0.0.1",
                "proto": "tcp",
                "assigned_domain": "wrong.ply.gg",
                "port": {"from": 123},
            },
            {
                "id": "correct",
                "local_port": 26565,
                "local_ip": "127.0.0.1",
                "proto": "udp",
                "assigned_domain": "right.ply.gg",
                "port": {"from": 456},
            },
        ]
    }
    monkeypatch.setattr("utils.playit_api.PlayitApiClient", lambda **kw: client)
    status = inspect_playit_status(tmp_path)
    assert status.state == "ready"
    assert status.endpoint == "right.ply.gg:456"


def test_unowned_pid_file_cannot_stop_another_process(tmp_path, monkeypatch):
    (tmp_path / ".msm.tunnel.pid").write_text(str(os.getpid()))
    terminate = MagicMock(
        side_effect=AssertionError("must not terminate arbitrary PID")
    )
    monkeypatch.setattr("psutil.Process.terminate", terminate)
    assert stop_tunnel_process(tmp_path)
    terminate.assert_not_called()


def test_playit_reports_provider_port_limit(tmp_path, monkeypatch):
    (tmp_path / ".msm.playit.secret").write_text("test-secret")
    (tmp_path / ".msm.playit.log").write_text("playit connected; tunnels loaded")
    monkeypatch.setattr(
        "utils.playit.tunnel_state", lambda *a: {"pid": 123, "playit_tunnel_id": "mine"}
    )
    client = MagicMock()
    client.__enter__.return_value = client
    client.agent_rundata.return_value = {
        "agent_id": "agent",
        "tunnels": [],
        "pending": [{"id": "mine"}],
    }
    client.tunnels_list.return_value = [
        {"id": "mine", "disabled_reason": "over-port-limit"}
    ]
    monkeypatch.setattr("utils.playit_api.PlayitApiClient", lambda **kw: client)
    status = inspect_playit_status(tmp_path)
    assert status.state == "failed"
    assert "over-port-limit" in status.message
    assert status.endpoint is None


def test_restore_rejects_path_outside_backup_directory(instance):
    with pytest.raises(ValueError):
        instance.restore_backup("../config.zip")
