from __future__ import annotations

from pathlib import Path
from platforms.base import (
    Architecture,
    EnvironmentVariant,
    OSType,
    PlatformDescriptor,
)
from platforms.paths import PathService


def test_path_service_termux_preserves_legacy_paths(tmp_path: Path):
    termux_desc = PlatformDescriptor(
        system="Linux",
        os_type=OSType.TERMUX,
        variant=EnvironmentVariant.TERMUX,
        architecture=Architecture.ARM64,
        raw_arch="aarch64",
        release="12",
        python_version="3.11.0",
        is_termux=True,
    )
    svc = PathService(termux_desc)
    assert svc.use_legacy_servers is True
    assert svc.config_dir == Path.home() / ".config" / "msm"


def test_path_service_server_dir_resolution(tmp_path: Path):
    svc = PathService()

    # 1. Custom server_dir in config
    custom_dir = tmp_path / "custom_server"
    resolved = svc.resolve_server_dir(
        "my_server",
        config={"servers": {"my_server": {"server_dir": str(custom_dir)}}},
    )
    assert resolved == custom_dir

    # 2. Legacy server dir
    legacy_dir = Path.home() / "minecraft-test_legacy_srv"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    try:
        resolved_legacy = svc.resolve_server_dir("test_legacy_srv")
        assert resolved_legacy == legacy_dir
    finally:
        legacy_dir.rmdir()


def test_path_service_ensure_directories(tmp_path: Path):
    desc = PlatformDescriptor(
        system="Linux",
        os_type=OSType.LINUX,
        variant=EnvironmentVariant.STANDARD,
        architecture=Architecture.X86_64,
        raw_arch="x86_64",
        release="6.1",
        python_version="3.11.0",
        is_linux=True,
    )
    svc = PathService(desc)
    svc.config_dir = tmp_path / "config"
    svc.data_dir = tmp_path / "data"
    svc.cache_dir = tmp_path / "cache"
    svc.logs_dir = tmp_path / "logs"

    svc.ensure_directories()
    assert svc.config_dir.exists()
    assert svc.data_dir.exists()
    assert svc.cache_dir.exists()
    assert svc.logs_dir.exists()
