from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.adapters import get_flavor_adapter, list_supported_flavors
from core.adapters.quilt import QuiltAdapter


def test_flavor_adapter_registry():
    flavors = list_supported_flavors()
    assert "paper" in flavors
    assert "folia" in flavors
    assert "purpur" in flavors
    assert "vanilla" in flavors
    assert "fabric" in flavors
    assert "quilt" in flavors
    assert "pocketmine" in flavors

    with pytest.raises(ValueError, match="Unsupported server flavor"):
        get_flavor_adapter("nonexistent_flavor")


def test_deterministic_artifact_resolution(tmp_path: Path):
    server_dir = tmp_path / "server"
    server_dir.mkdir()

    # Create multiple JARs
    (server_dir / "a_random_installer.jar").write_bytes(b"bad")
    (server_dir / "paper-1.20.4-496.jar").write_bytes(b"good" * 3000)

    adapter = get_flavor_adapter("paper")
    # Must pick paper jar, not the installer jar!
    artifact = adapter.resolve_artifact(server_dir)
    assert artifact == "paper-1.20.4-496.jar"


def test_quilt_multi_stage_install_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    server_dir = tmp_path / "quilt_srv"
    server_dir.mkdir()

    # Mock safe_request for installer download
    mock_resp = MagicMock()
    mock_resp.iter_content.return_value = [b"mock installer content"]
    monkeypatch.setattr(
        "core.adapters.quilt.safe_request", lambda *args, **kwargs: mock_resp
    )

    # Mock Java path
    monkeypatch.setattr(
        "core.adapters.quilt.get_java_path", lambda ver, cfg, logger=None: "java"
    )

    # Mock running installer: creates quilt-server-launch.jar and libraries
    def fake_run(command, *args, **kwargs):
        (server_dir / "quilt-server-launch.jar").write_bytes(b"launch jar" * 2000)
        (server_dir / "libraries").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(command, 0, stdout="installed", stderr="")

    monkeypatch.setattr("core.adapters.quilt.run_command", fake_run)

    adapter = QuiltAdapter()
    version_info = {
        "installer": "0.11.0",
        "loader": "0.25.0",
    }
    artifact, metadata = adapter.install("1.20.4", version_info, server_dir)

    assert artifact.name == "quilt-server-launch.jar"
    assert metadata["artifact"] == "quilt-server-launch.jar"

    valid, reason = adapter.validate_installation(server_dir, metadata)
    assert valid is True

    # Ensure installer JAR is not the launch artifact
    assert "installer" not in adapter.resolve_artifact(server_dir, metadata).lower()


def test_quilt_incomplete_install_validation_failure(tmp_path: Path):
    server_dir = tmp_path / "incomplete_quilt"
    server_dir.mkdir()

    adapter = QuiltAdapter()
    # No launch artifact or libraries created
    valid, reason = adapter.validate_installation(
        server_dir, {"artifact": "quilt-installer.jar"}
    )
    assert valid is False
    assert (
        "Installer JAR" in reason
        or "not found" in reason
        or "No valid Quilt launch JAR" in reason
    )
