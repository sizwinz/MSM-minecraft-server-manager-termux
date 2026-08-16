from __future__ import annotations

import pytest

from utils.system import (
    detect_php_runtime,
    get_java_path,
    is_java_version_compatible,
)


def test_java_version_compatibility_policy():
    # Exact matches
    assert is_java_version_compatible("8", "8")[0] is True
    assert is_java_version_compatible("17", "17")[0] is True
    assert is_java_version_compatible("21", "21")[0] is True
    assert is_java_version_compatible("25", "25")[0] is True

    # Permitted fallbacks
    assert is_java_version_compatible("17", "21")[0] is True
    assert is_java_version_compatible("21", "25")[0] is True

    # Rejected incompatible combinations
    # 1. Java 8 required (1.16 and older) MUST NOT run on Java 17/21/25
    compat8_17, reason8_17 = is_java_version_compatible("8", "17")
    assert compat8_17 is False
    assert "requires Java 8" in reason8_17

    compat8_21, reason8_21 = is_java_version_compatible("8", "21")
    assert compat8_21 is False

    # 2. Older Java running newer server
    compat21_17, reason21_17 = is_java_version_compatible("21", "17")
    assert compat21_17 is False
    assert "too old" in reason21_17


def test_get_java_path_prefers_exact_over_fallback(monkeypatch: pytest.MonkeyPatch):
    def fake_which(cmd):
        if cmd in ("java-17", "/mock/bin/java-17"):
            return "/mock/bin/java-17"
        if cmd in ("java-21", "/mock/bin/java-21"):
            return "/mock/bin/java-21"
        return None

    def fake_detect(candidate, logger=None):
        if candidate == "/mock/bin/java-17":
            return "17"
        if candidate == "/mock/bin/java-21":
            return "21"
        return None

    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("utils.system.detect_java_version", fake_detect)
    monkeypatch.setattr("utils.system.COMMON_JAVA_HOME_BASES", [])

    # Minecraft 1.20.4 requires Java 17
    result = get_java_path("1.20.4", {})
    assert result == "/mock/bin/java-17"


def test_get_java_path_rejects_java17_for_minecraft_1_16(
    monkeypatch: pytest.MonkeyPatch,
):
    def fake_which(cmd):
        if cmd in ("java", "java-17", "/mock/bin/java-17"):
            return "/mock/bin/java-17"
        return None

    def fake_detect(candidate, logger=None):
        return "17"

    monkeypatch.setattr("shutil.which", fake_which)
    monkeypatch.setattr("utils.system.detect_java_version", fake_detect)
    monkeypatch.setattr("utils.system.COMMON_JAVA_HOME_BASES", [])

    # 1.16.5 requires Java 8; Java 17 must be rejected
    result = get_java_path("1.16.5", {})
    assert result is None


def test_detect_php_runtime_recognizes_zts_and_pmmpthread(
    monkeypatch: pytest.MonkeyPatch,
):
    import subprocess

    def fake_run(command, *args, **kwargs):
        cmd_str = " ".join(command)
        if "-v" in cmd_str:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="PHP 8.2.14 (cli) (built: Dec 20 2023) ( ZTS visualc )",
                stderr="",
            )
        if "-m" in cmd_str:
            return subprocess.CompletedProcess(
                command, 0, stdout="Core\npmmpthread\nyaml\n", stderr=""
            )
        return None

    monkeypatch.setattr("utils.system.run_command", fake_run)
    monkeypatch.setattr("pathlib.Path.is_file", lambda self: True)

    info = detect_php_runtime("/mock/bin/php")
    assert info["exists"] is True
    assert info["zts"] is True
    assert info["has_pmmpthread"] is True
    assert info["compatible"] is True
