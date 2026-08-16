from __future__ import annotations

import io
import shutil
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.server import ServerInstance
from utils.archive import safe_extract_tar
from utils.network import download_php_binary
from utils.system import detect_php_runtime, get_php_path


def test_safe_extract_tar_blocks_path_traversal():
    temp_path = Path(".test_tmp") / "tar-slip"
    if temp_path.exists():
        shutil.rmtree(temp_path, ignore_errors=True)
    temp_path.mkdir(parents=True, exist_ok=True)
    try:
        archive_path = temp_path / "bad.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            tarinfo = tarfile.TarInfo(name="../escape.txt")
            data = b"malicious"
            tarinfo.size = len(data)
            archive.addfile(tarinfo, io.BytesIO(data))

        with pytest.raises(ValueError, match="Blocked unsafe archive member"):
            safe_extract_tar(archive_path, temp_path / "target")
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


def test_safe_extract_tar_extracts_valid_archive(tmp_path: Path):
    archive_path = tmp_path / "valid.tar.gz"
    dest_path = tmp_path / "extracted"
    dest_path.mkdir()

    with tarfile.open(archive_path, "w:gz") as archive:
        tarinfo = tarfile.TarInfo(name="bin/php")
        data = b"#!/bin/sh\necho php\n"
        tarinfo.size = len(data)
        tarinfo.mode = 0o755
        archive.addfile(tarinfo, io.BytesIO(data))

    safe_extract_tar(archive_path, dest_path)
    extracted_bin = dest_path / "bin" / "php"
    assert extracted_bin.exists()
    assert extracted_bin.read_text(encoding="utf-8") == "#!/bin/sh\necho php\n"


def test_detect_php_runtime_identifies_zts_and_pmmpthread(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_run(cmd, **kwargs):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        if "-v" in cmd:
            mock_proc.stdout = "PHP 8.2.15 (cli) (built: Jan 1 2025) ( ZTS )\n"
        elif "-m" in cmd:
            mock_proc.stdout = "Core\npmmpthread\nyaml\nopenssl\n"
        return mock_proc

    monkeypatch.setattr("utils.system.run_command", fake_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/mock/bin/php")

    info = detect_php_runtime("/mock/bin/php")
    assert info["exists"] is True
    assert info["zts"] is True
    assert info["has_pmmpthread"] is True
    assert info["has_yaml"] is True
    assert info["compatible"] is True


def test_detect_php_runtime_handles_nts_incompatible(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_run(cmd, **kwargs):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        if "-v" in cmd:
            mock_proc.stdout = "PHP 8.2.15 (cli) (built: Jan 1 2025) ( NTS )\n"
        elif "-m" in cmd:
            mock_proc.stdout = "Core\ndate\nlibxml\n"
        return mock_proc

    monkeypatch.setattr("utils.system.run_command", fake_run)
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/php")

    info = detect_php_runtime("/usr/bin/php")
    assert info["exists"] is True
    assert info["zts"] is False
    assert info["has_pmmpthread"] is False
    assert info["compatible"] is False


def test_get_php_path_resolves_custom_and_server_binaries(tmp_path: Path):
    custom_bin = tmp_path / "custom_php"
    custom_bin.write_text("#!/bin/sh\n", encoding="utf-8")

    server_dir = tmp_path / "server"
    local_bin = server_dir / "bin" / "php7" / "bin" / "php"
    local_bin.parent.mkdir(parents=True, exist_ok=True)
    local_bin.write_text("#!/bin/sh\n", encoding="utf-8")

    # 1. Config override
    assert get_php_path(
        config={"php_path": str(custom_bin)},
        server_dir=server_dir,
        auto_install=False,
    ) == str(custom_bin)

    # 2. Server dir local binary
    assert get_php_path(
        config={},
        server_dir=server_dir,
        auto_install=False,
    ) == str(local_bin)


def test_get_php_path_skips_incompatible_system_php_and_triggers_auto_install(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    installed_php = tmp_path / "installed_php"
    installed_php.write_text("#!/bin/sh\n", encoding="utf-8")

    # Simulate system PHP is NTS
    monkeypatch.setattr("shutil.which", lambda cmd: "/usr/bin/php")
    monkeypatch.setattr(
        "utils.system.detect_php_runtime",
        lambda *args, **kwargs: {"exists": True, "compatible": False},
    )
    monkeypatch.setattr(
        "utils.network.download_php_binary",
        lambda *args, **kwargs: installed_php,
    )

    result = get_php_path(
        config={}, server_dir=tmp_path / "empty_srv", auto_install=True
    )
    assert result == str(installed_php)


def test_download_php_binary_matches_android_arm64_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    class DummyResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

        def iter_content(self, chunk_size=1024):
            # Create a mock tar.gz archive in-memory
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                ti = tarfile.TarInfo(name="bin/php7/bin/php")
                data = b"#!/bin/sh\n"
                ti.size = len(data)
                ti.mode = 0o755
                tar.addfile(ti, io.BytesIO(data))
            buf.seek(0)
            yield buf.read()

    releases_payload = [
        {
            "tag_name": "pm5-php-8.4-latest",
            "draft": False,
            "assets": [
                {
                    "name": "Z-PHP-8.4-Android-arm64-PM5-debugging-symbols.tar.gz",
                    "browser_download_url": "https://example/debug.tar.gz",
                },
                {
                    "name": "PHP-8.4-Linux-x86_64-PM5.tar.gz",
                    "browser_download_url": "https://example/linux.tar.gz",
                },
                {
                    "name": "PHP-8.4-Android-arm64-PM5.tar.gz",
                    "browser_download_url": "https://example/android.tar.gz",
                },
            ],
        }
    ]

    def fake_request(_session, _method, url, logger=None, **kwargs):
        if url.endswith("/releases"):
            return DummyResponse(releases_payload)
        return DummyResponse(None)

    monkeypatch.setattr("utils.network.safe_request", fake_request)
    monkeypatch.setattr(
        "utils.network.get_system_arch_and_os", lambda: ("Android", "arm64")
    )

    dest_dir = tmp_path / "msm_php"
    php_bin = download_php_binary(target_dir=dest_dir)
    assert php_bin is not None
    assert php_bin.exists()
    assert "php" in php_bin.name


def test_download_php_binary_fallback_for_android_x86_64(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    class DummyResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

        def iter_content(self, chunk_size=1024):
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w:gz") as tar:
                ti = tarfile.TarInfo(name="bin/php7/bin/php")
                data = b"#!/bin/sh\n"
                ti.size = len(data)
                ti.mode = 0o755
                tar.addfile(ti, io.BytesIO(data))
            buf.seek(0)
            yield buf.read()

    releases_payload = [
        {
            "tag_name": "pm5-php-8.4-latest",
            "draft": False,
            "assets": [
                {
                    "name": "PHP-8.4-Linux-x86_64-PM5.tar.gz",
                    "browser_download_url": "https://example/linux.tar.gz",
                },
                {
                    "name": "PHP-8.4-Android-arm64-PM5.tar.gz",
                    "browser_download_url": "https://example/android.tar.gz",
                },
            ],
        }
    ]

    def fake_request(_session, _method, url, logger=None, **kwargs):
        if url.endswith("/releases"):
            return DummyResponse(releases_payload)
        return DummyResponse(None)

    monkeypatch.setattr("utils.network.safe_request", fake_request)
    monkeypatch.setattr(
        "utils.network.get_system_arch_and_os", lambda: ("Android", "x86_64")
    )

    dest_dir = tmp_path / "msm_php_x86_64"
    php_bin = download_php_binary(target_dir=dest_dir)
    assert php_bin is not None
    assert php_bin.exists()
    assert "php" in php_bin.name


def test_pocketmine_startup_command_and_server_properties(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    server_dir = tmp_path / "minecraft-test-pmmp"
    server_dir.mkdir(parents=True, exist_ok=True)
    phar_file = server_dir / "PocketMine-MP.phar"
    phar_file.write_text("phar", encoding="utf-8")

    php_executable = tmp_path / "php_bin"
    php_executable.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr("core.server.get_server_dir", lambda name: server_dir)
    monkeypatch.setattr(
        "core.server.get_php_path", lambda *args, **kwargs: str(php_executable)
    )

    mock_config_mgr = MagicMock()
    mock_config_mgr.load.return_value = {
        "servers": {
            "test-pmmp": {
                "server_flavor": "pocketmine",
                "server_version": "5.12.0",
                "ram_mb": 1536,
                "server_settings": {"port": 19132, "motd": "Bedrock Server"},
            }
        }
    }
    mock_db_mgr = MagicMock()
    mock_logger = MagicMock()

    instance = ServerInstance("test-pmmp", mock_config_mgr, mock_db_mgr, mock_logger)

    # 1. Test startup command
    cmd = instance.build_startup_command()
    assert cmd == [
        str(php_executable),
        "-dmemory_limit=1536M",
        "PocketMine-MP.phar",
        "--no-wizard",
    ]

    # 2. Test server properties apply
    instance.apply_server_files()
    props_path = server_dir / "server.properties"
    assert props_path.exists()
    content = props_path.read_text(encoding="utf-8")
    assert "server-port=19132" in content
    assert "server-portv4=19132" in content
    assert "server-portv6=19133" in content
