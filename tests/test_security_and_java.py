from __future__ import annotations

import importlib
import shutil
import zipfile
from pathlib import Path

import pytest

import core.constants as constants
from utils.archive import safe_extract_zip
from utils.system import get_required_java


def test_safe_extract_zip_blocks_path_traversal():
    temp_path = Path(".test_tmp") / "zip-slip"
    if temp_path.exists():
        shutil.rmtree(temp_path, ignore_errors=True)
    temp_path.mkdir(parents=True, exist_ok=True)
    try:
        archive_path = temp_path / "bad.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("../escape.txt", "owned")

        with pytest.raises(ValueError, match="Blocked unsafe archive member"):
            safe_extract_zip(archive_path, temp_path / "server")
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


def test_get_required_java_handles_1_20_5_and_older_releases():
    assert get_required_java("26.2") == "25"
    assert get_required_java("26.1") == "25"
    assert get_required_java("1.21.11") == "21"
    assert get_required_java("1.20.5") == "21"
    assert get_required_java("1.20.4") == "17"
    assert get_required_java("1.16.5") == "8"


def test_get_java_path_resolves_versioned_candidates(
    monkeypatch: pytest.MonkeyPatch,
):
    from utils.system import get_java_path

    def fake_which(cmd):
        if cmd in ("java-25", "java25", "/mock/bin/java-25"):
            return "/mock/bin/java-25"
        return None

    def fake_detect(candidate, logger=None):
        if candidate == "/mock/bin/java-25":
            return "25"
        return None

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("utils.system.detect_java_version", fake_detect)

    result = get_java_path("26.2", {})
    assert result == "/mock/bin/java-25"


def test_common_java_home_bases_skips_empty_java_home(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("JAVA_HOME", raising=False)
    reloaded = importlib.reload(constants)
    try:
        assert Path("") not in reloaded.COMMON_JAVA_HOME_BASES
    finally:
        importlib.reload(constants)
