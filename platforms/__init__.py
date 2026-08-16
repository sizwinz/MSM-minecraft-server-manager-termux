"""Platforms package for MSM cross-platform runtime foundation."""

from platforms.base import (
    Architecture,
    EnvironmentVariant,
    OSType,
    PlatformCapabilities,
    PlatformDescriptor,
)
from platforms.detector import (
    detect_platform,
    detect_wsl,
    is_running_on_termux,
    normalize_architecture,
)
from platforms.paths import PathService, get_path_service

__all__ = [
    "Architecture",
    "EnvironmentVariant",
    "OSType",
    "PlatformCapabilities",
    "PlatformDescriptor",
    "detect_platform",
    "detect_wsl",
    "is_running_on_termux",
    "normalize_architecture",
    "PathService",
    "get_path_service",
]
