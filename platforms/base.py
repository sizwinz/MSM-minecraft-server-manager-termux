"""Base definitions, enums, and dataclasses for platform abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class OSType(str, Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    TERMUX = "termux"
    FREEBSD = "freebsd"
    UNKNOWN = "unknown"


class EnvironmentVariant(str, Enum):
    STANDARD = "standard"
    TERMUX = "termux"
    WSL1 = "wsl1"
    WSL2 = "wsl2"
    DOCKER = "docker"
    UNKNOWN = "unknown"


class Architecture(str, Enum):
    X86_64 = "x86_64"
    ARM64 = "arm64"
    ARMV7 = "armv7"
    X86 = "x86"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PlatformCapabilities:
    """Explicit runtime capabilities supported by the current platform."""

    supported_backends: list[str] = field(default_factory=list)
    default_backend: str = "native_posix"
    supports_screen: bool = False
    supports_posix_signals: bool = True
    supports_windows_process_groups: bool = False
    supports_job_objects: bool = False
    supports_java_provisioning: bool = False
    supports_pocketmine_binary: bool = False
    supports_file_permissions: bool = True
    supports_rcon: bool = True
    supported_tunnels: list[str] = field(default_factory=lambda: ["playit", "ngrok"])
    supports_console_attachment: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "supported_backends": list(self.supported_backends),
            "default_backend": self.default_backend,
            "supports_screen": self.supports_screen,
            "supports_posix_signals": self.supports_posix_signals,
            "supports_windows_process_groups": self.supports_windows_process_groups,
            "supports_job_objects": self.supports_job_objects,
            "supports_java_provisioning": self.supports_java_provisioning,
            "supports_pocketmine_binary": self.supports_pocketmine_binary,
            "supports_file_permissions": self.supports_file_permissions,
            "supports_rcon": self.supports_rcon,
            "supported_tunnels": list(self.supported_tunnels),
            "supports_console_attachment": self.supports_console_attachment,
            "notes": list(self.notes),
        }


@dataclass
class PlatformDescriptor:
    """Structured platform description detailing system identity and capabilities."""

    system: str
    os_type: OSType
    variant: EnvironmentVariant
    architecture: Architecture
    raw_arch: str
    release: str
    python_version: str
    is_wsl: bool = False
    wsl_version: int | None = None
    is_termux: bool = False
    is_windows: bool = False
    is_macos: bool = False
    is_linux: bool = False
    is_freebsd: bool = False
    package_manager: str | None = None
    terminal_interactive: bool = False
    supports_ansi_colors: bool = True
    supports_unicode_glyphs: bool = True
    terminal_width: int = 60
    capabilities: PlatformCapabilities = field(default_factory=PlatformCapabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "os_type": self.os_type.value,
            "variant": self.variant.value,
            "architecture": self.architecture.value,
            "raw_arch": self.raw_arch,
            "release": self.release,
            "python_version": self.python_version,
            "is_wsl": self.is_wsl,
            "wsl_version": self.wsl_version,
            "is_termux": self.is_termux,
            "is_windows": self.is_windows,
            "is_macos": self.is_macos,
            "is_linux": self.is_linux,
            "is_freebsd": self.is_freebsd,
            "package_manager": self.package_manager,
            "terminal_interactive": self.terminal_interactive,
            "supports_ansi_colors": self.supports_ansi_colors,
            "supports_unicode_glyphs": self.supports_unicode_glyphs,
            "terminal_width": self.terminal_width,
            "capabilities": self.capabilities.to_dict(),
        }
