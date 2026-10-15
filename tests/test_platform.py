from __future__ import annotations

from pathlib import Path
import pytest

from platforms.base import Architecture, EnvironmentVariant, OSType
from platforms.capabilities import build_capabilities
from platforms.detector import (
    detect_platform,
    detect_wsl,
    is_running_on_termux,
    normalize_architecture,
)


def test_normalize_architecture():
    assert normalize_architecture("x86_64")[0] == Architecture.X86_64
    assert normalize_architecture("amd64")[0] == Architecture.X86_64
    assert normalize_architecture("aarch64")[0] == Architecture.ARM64
    assert normalize_architecture("arm64")[0] == Architecture.ARM64
    assert normalize_architecture("armv7l")[0] == Architecture.ARMV7
    assert normalize_architecture("armv8l")[0] == Architecture.ARMV7
    assert normalize_architecture("i686")[0] == Architecture.X86
    assert normalize_architecture("unknown_arch")[0] == Architecture.UNKNOWN


def test_detect_termux_detection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
    assert is_running_on_termux() is True

    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    assert is_running_on_termux() is True


def test_detect_wsl_detection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/1_interop")

    is_wsl, ver = detect_wsl()
    assert is_wsl is True
    assert ver in (1, 2)


def test_build_capabilities_windows():
    caps = build_capabilities(
        os_type=OSType.WINDOWS,
        variant=EnvironmentVariant.STANDARD,
        architecture=Architecture.X86_64,
        is_windows=True,
        is_macos=False,
        is_termux=False,
        is_linux=False,
        is_freebsd=False,
        screen_available=False,
    )
    assert "windows" in caps.supported_backends
    assert caps.default_backend == "windows"
    assert caps.supports_windows_process_groups is True
    assert caps.supports_screen is False
    assert caps.supports_console_attachment is False


def test_build_capabilities_linux_with_and_without_screen():
    caps_with_screen = build_capabilities(
        os_type=OSType.LINUX,
        variant=EnvironmentVariant.STANDARD,
        architecture=Architecture.X86_64,
        is_windows=False,
        is_macos=False,
        is_termux=False,
        is_linux=True,
        is_freebsd=False,
        screen_available=True,
    )
    assert "screen" in caps_with_screen.supported_backends
    assert "native_posix" in caps_with_screen.supported_backends
    assert caps_with_screen.default_backend == "screen"
    assert caps_with_screen.supports_console_attachment is True

    caps_without_screen = build_capabilities(
        os_type=OSType.LINUX,
        variant=EnvironmentVariant.STANDARD,
        architecture=Architecture.X86_64,
        is_windows=False,
        is_macos=False,
        is_termux=False,
        is_linux=True,
        is_freebsd=False,
        screen_available=False,
    )
    assert "screen" not in caps_without_screen.supported_backends
    assert caps_without_screen.default_backend == "native_posix"
    assert caps_without_screen.supports_console_attachment is False


def test_detect_platform_returns_descriptor():
    desc = detect_platform()
    assert desc.system != ""
    assert desc.architecture in (
        Architecture.X86_64,
        Architecture.ARM64,
        Architecture.ARMV7,
        Architecture.X86,
        Architecture.UNKNOWN,
    )
    assert desc.capabilities is not None
    data = desc.to_dict()
    assert "capabilities" in data
    assert "os_type" in data


def test_windows_capabilities_do_not_claim_posix_screen_or_job_objects():
    caps = build_capabilities(
        OSType.WINDOWS,
        EnvironmentVariant.STANDARD,
        Architecture.X86,
        is_windows=True,
        is_macos=False,
        is_termux=False,
        is_linux=False,
        is_freebsd=False,
        screen_available=True,
    )
    assert caps.supports_screen is False
    assert caps.supports_job_objects is False
    assert caps.supports_pocketmine_binary is False


@pytest.mark.parametrize(
    "platform_name,system,termux,wsl_version,expected_os,expected_variant,backend",
    [
        (
            "win32",
            "Windows",
            False,
            None,
            OSType.WINDOWS,
            EnvironmentVariant.STANDARD,
            "windows",
        ),
        (
            "darwin",
            "Darwin",
            False,
            None,
            OSType.MACOS,
            EnvironmentVariant.STANDARD,
            "native_posix",
        ),
        (
            "freebsd14",
            "FreeBSD",
            False,
            None,
            OSType.FREEBSD,
            EnvironmentVariant.STANDARD,
            "native_posix",
        ),
        (
            "linux",
            "Linux",
            False,
            None,
            OSType.LINUX,
            EnvironmentVariant.STANDARD,
            "native_posix",
        ),
        (
            "linux",
            "Linux",
            False,
            1,
            OSType.LINUX,
            EnvironmentVariant.WSL1,
            "native_posix",
        ),
        (
            "linux",
            "Linux",
            False,
            2,
            OSType.LINUX,
            EnvironmentVariant.WSL2,
            "native_posix",
        ),
        (
            "linux",
            "Linux",
            True,
            None,
            OSType.TERMUX,
            EnvironmentVariant.TERMUX,
            "native_posix",
        ),
    ],
)
def test_platform_detection_and_backend_matrix(
    monkeypatch,
    platform_name,
    system,
    termux,
    wsl_version,
    expected_os,
    expected_variant,
    backend,
):
    monkeypatch.setattr("platforms.detector.sys.platform", platform_name)
    monkeypatch.setattr("platforms.detector.platform.system", lambda: system)
    monkeypatch.setattr("platforms.detector.platform.machine", lambda: "x86_64")
    monkeypatch.setattr("platforms.detector.is_running_on_termux", lambda: termux)
    monkeypatch.setattr(
        "platforms.detector.detect_wsl", lambda: (wsl_version is not None, wsl_version)
    )
    monkeypatch.setattr("platforms.detector.shutil.which", lambda _: None)
    result = detect_platform()
    assert result.os_type == expected_os
    assert result.variant == expected_variant
    assert result.capabilities.default_backend == backend
    assert result.capabilities.supported_backends == [backend]
