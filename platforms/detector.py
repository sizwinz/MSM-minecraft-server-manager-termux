"""Platform and environment detection service."""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

from platforms.base import (
    Architecture,
    EnvironmentVariant,
    OSType,
    PlatformDescriptor,
)
from platforms.capabilities import build_capabilities


def normalize_architecture(machine_str: str | None = None) -> tuple[Architecture, str]:
    """Normalize system machine/architecture string."""
    raw = (machine_str or platform.machine() or sys.platform).lower().strip()
    if raw in ("aarch64", "arm64", "armv8", "armv8l", "arm64-v8a"):
        return Architecture.ARM64, raw
    if raw in ("x86_64", "amd64", "x64", "em64t"):
        return Architecture.X86_64, raw
    if raw in ("armv7l", "armv7", "armhf", "arm", "armeabi-v7a"):
        return Architecture.ARMV7, raw
    if raw in ("x86", "i386", "i486", "i586", "i686"):
        return Architecture.X86, raw
    return Architecture.UNKNOWN, raw


def is_running_on_termux() -> bool:
    """Detect if running inside Android Termux environment."""
    if os.environ.get("TERMUX_VERSION") or os.environ.get("TERMUX_APP_PID"):
        return True
    prefix = os.environ.get("PREFIX", "")
    if "termux" in prefix.lower():
        return True
    return Path("/data/data/com.termux").exists()


def detect_wsl() -> tuple[bool, int | None]:
    """Detect if running inside Windows Subsystem for Linux (WSL1 or WSL2)."""
    if not sys.platform.startswith("linux"):
        return False, None

    if "WSL_DISTRO_NAME" in os.environ or "WSL_INTEROP" in os.environ:
        # Check kernel release for WSL2 vs WSL1
        proc_ver_path = Path("/proc/version")
        if proc_ver_path.exists():
            try:
                proc_ver = proc_ver_path.read_text(encoding="utf-8").lower()
                if "microsoft-standard" in proc_ver or "wsl2" in proc_ver:
                    return True, 2
                if "microsoft" in proc_ver:
                    return True, 1
            except OSError:
                pass
        return True, 2 if "WSL_INTEROP" in os.environ else 1

    proc_ver_path = Path("/proc/version")
    if proc_ver_path.exists():
        try:
            content = proc_ver_path.read_text(encoding="utf-8").lower()
            if "microsoft" in content:
                if "microsoft-standard" in content or "wsl2" in content:
                    return True, 2
                return True, 1
        except OSError:
            pass

    return False, None


def detect_package_manager(os_type: OSType, is_termux_env: bool) -> str | None:
    """Detect available system package manager."""
    if is_termux_env:
        return "pkg" if shutil.which("pkg") else "apt"

    if os_type == OSType.WINDOWS:
        for pm in ("winget", "choco", "scoop"):
            if shutil.which(pm):
                return pm
        return None

    if os_type == OSType.MACOS:
        return "brew" if shutil.which("brew") else None

    if os_type == OSType.FREEBSD:
        return "pkg" if shutil.which("pkg") else None

    # Linux / WSL
    for pm in ("apt-get", "pacman", "dnf", "yum", "apk", "zypper", "xbps-install"):
        if shutil.which(pm):
            return pm
    return None


def detect_terminal_capabilities() -> tuple[bool, bool, bool, int]:
    """Detect interactive TTY, ANSI color support, Unicode glyph support, and terminal width."""
    interactive = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False

    # NO_COLOR standard (https://no-color.org/)
    no_color = "NO_COLOR" in os.environ and os.environ["NO_COLOR"] != ""
    term = os.environ.get("TERM", "").lower()

    if no_color or term == "dumb":
        ansi_colors = False
    elif sys.platform == "win32":
        # Windows Terminal, ConEmu, ANSICON or Win10+ console supports VT100
        ansi_colors = bool(
            os.environ.get("WT_SESSION")
            or os.environ.get("ConEmuPID")
            or os.environ.get("ANSICON")
            or "TERM_PROGRAM" in os.environ
            or interactive
        )
    else:
        ansi_colors = interactive or bool(
            os.environ.get("COLORTERM") or "color" in term
        )

    # Unicode glyph support
    encoding = (getattr(sys.stdout, "encoding", "") or "utf-8").lower()
    supports_unicode = "utf" in encoding

    # Fallback check for legacy Windows cmd without UTF-8 codepage
    if sys.platform == "win32" and not os.environ.get("WT_SESSION"):
        if encoding in ("cp437", "cp1252", "ascii"):
            supports_unicode = False

    cols = shutil.get_terminal_size((60, 24)).columns
    terminal_width = max(36, min(cols, 80))

    return interactive, ansi_colors, supports_unicode, terminal_width


def detect_platform() -> PlatformDescriptor:
    """Inspect host system and return a structured PlatformDescriptor."""
    system_raw = platform.system()
    norm_arch, raw_arch = normalize_architecture()
    release = platform.release()
    python_ver = platform.python_version()

    termux_env = is_running_on_termux()
    is_wsl_flag, wsl_ver = detect_wsl()

    if termux_env:
        os_type = OSType.TERMUX
        variant = EnvironmentVariant.TERMUX
        is_windows = False
        is_macos = False
        is_termux = True
        is_linux = False
        is_freebsd = False
    elif sys.platform == "win32" or system_raw == "Windows":
        os_type = OSType.WINDOWS
        variant = EnvironmentVariant.STANDARD
        is_windows = True
        is_macos = False
        is_termux = False
        is_linux = False
        is_freebsd = False
    elif sys.platform == "darwin" or system_raw == "Darwin":
        os_type = OSType.MACOS
        variant = EnvironmentVariant.STANDARD
        is_windows = False
        is_macos = True
        is_termux = False
        is_linux = False
        is_freebsd = False
    elif sys.platform.startswith("freebsd") or "freebsd" in system_raw.lower():
        os_type = OSType.FREEBSD
        variant = EnvironmentVariant.STANDARD
        is_windows = False
        is_macos = False
        is_termux = False
        is_linux = False
        is_freebsd = True
    elif sys.platform.startswith("linux") or system_raw == "Linux":
        os_type = OSType.LINUX
        if is_wsl_flag:
            variant = (
                EnvironmentVariant.WSL2 if wsl_ver == 2 else EnvironmentVariant.WSL1
            )
        else:
            variant = EnvironmentVariant.STANDARD
        is_windows = False
        is_macos = False
        is_termux = False
        is_linux = True
        is_freebsd = False
    else:
        os_type = OSType.UNKNOWN
        variant = EnvironmentVariant.UNKNOWN
        is_windows = False
        is_macos = False
        is_termux = False
        is_linux = False
        is_freebsd = False

    package_mgr = detect_package_manager(os_type, termux_env)
    interactive, ansi, unicode_glyphs, width = detect_terminal_capabilities()

    caps = build_capabilities(
        os_type=os_type,
        variant=variant,
        architecture=norm_arch,
        is_windows=is_windows,
        is_macos=is_macos,
        is_termux=is_termux,
        is_linux=is_linux,
        is_freebsd=is_freebsd,
    )

    return PlatformDescriptor(
        system=system_raw,
        os_type=os_type,
        variant=variant,
        architecture=norm_arch,
        raw_arch=raw_arch,
        release=release,
        python_version=python_ver,
        is_wsl=is_wsl_flag,
        wsl_version=wsl_ver,
        is_termux=is_termux,
        is_windows=is_windows,
        is_macos=is_macos,
        is_linux=is_linux,
        is_freebsd=is_freebsd,
        package_manager=package_mgr,
        terminal_interactive=interactive,
        supports_ansi_colors=ansi,
        supports_unicode_glyphs=unicode_glyphs,
        terminal_width=width,
        capabilities=caps,
    )
