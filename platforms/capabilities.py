"""Platform capability resolver."""

from __future__ import annotations

import shutil
from platforms.base import (
    Architecture,
    EnvironmentVariant,
    OSType,
    PlatformCapabilities,
)


def build_capabilities(
    os_type: OSType,
    variant: EnvironmentVariant,
    architecture: Architecture,
    is_windows: bool,
    is_macos: bool,
    is_termux: bool,
    is_linux: bool,
    is_freebsd: bool,
    screen_available: bool | None = None,
) -> PlatformCapabilities:
    """Evaluate and build concrete capabilities based on system detection."""
    has_screen = (
        shutil.which("screen") is not None
        if screen_available is None
        else screen_available
    )
    supported_backends: list[str] = []
    notes: list[str] = []

    if is_windows:
        supported_backends.append("windows")
        default_backend = "windows"
        supports_posix_signals = False
        supports_windows_process_groups = True
        supports_job_objects = True
        supports_file_permissions = False
        supports_console_attachment = False
        supports_pocketmine_binary = architecture in (
            Architecture.X86_64,
            Architecture.X86,
        )
        supports_java_provisioning = True
        notes.append("Windows native process backend active.")
        if not has_screen:
            notes.append("Console attach unsupported on Windows without screen.")
    else:
        # POSIX systems
        supports_posix_signals = True
        supports_windows_process_groups = False
        supports_job_objects = False
        supports_file_permissions = True

        if has_screen:
            supported_backends.append("screen")
        supported_backends.append("native_posix")

        if is_termux or is_linux:
            default_backend = "screen" if has_screen else "native_posix"
        else:
            default_backend = "native_posix"

        supports_console_attachment = has_screen

        if is_termux:
            supports_pocketmine_binary = True
            supports_java_provisioning = True
            notes.append("Termux environment: pkg-based Java & PMMP provisioning.")
        elif is_macos:
            supports_pocketmine_binary = architecture in (
                Architecture.ARM64,
                Architecture.X86_64,
            )
            supports_java_provisioning = True
            notes.append("macOS environment: native posix backend preferred.")
        elif is_freebsd:
            supports_pocketmine_binary = False
            supports_java_provisioning = False
            notes.append("FreeBSD: standard Java packages supported.")
        else:
            supports_pocketmine_binary = architecture in (
                Architecture.X86_64,
                Architecture.ARM64,
            )
            supports_java_provisioning = True

    return PlatformCapabilities(
        supported_backends=supported_backends,
        default_backend=default_backend,
        supports_screen=has_screen,
        supports_posix_signals=supports_posix_signals,
        supports_windows_process_groups=supports_windows_process_groups,
        supports_job_objects=supports_job_objects,
        supports_java_provisioning=supports_java_provisioning,
        supports_pocketmine_binary=supports_pocketmine_binary,
        supports_file_permissions=supports_file_permissions,
        supports_rcon=True,
        supported_tunnels=["playit", "ngrok"],
        supports_console_attachment=supports_console_attachment,
        notes=notes,
    )
