"""Centralized platform path service with backward-compatible legacy discovery."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from platforms.base import PlatformDescriptor
from platforms.detector import detect_platform


class PathService:
    """Provides platform-native directories with full backward-compatibility."""

    def __init__(self, platform_desc: PlatformDescriptor | None = None):
        self.platform = platform_desc or detect_platform()
        self._init_paths()

    def _init_paths(self) -> None:
        home = Path.home()
        self.legacy_config_dir = home / ".config" / "msm"
        self.legacy_config_file = self.legacy_config_dir / "config.json"
        self.legacy_database_file = self.legacy_config_dir / "msm.db"
        self.legacy_log_file = self.legacy_config_dir / "msm.log"

        if self.platform.is_termux:
            # Termux: keep legacy standard ~/.config/msm
            self.config_dir = self.legacy_config_dir
            self.data_dir = self.legacy_config_dir
            self.cache_dir = self.legacy_config_dir / "cache"
            self.logs_dir = self.legacy_config_dir
            self.runtime_dir = Path(
                os.environ.get("TMPDIR", "/data/data/com.termux/files/usr/tmp")
            )
            self.servers_dir = home
            self.use_legacy_servers = True
        elif self.platform.is_windows:
            appdata = os.environ.get("APPDATA")
            localappdata = os.environ.get("LOCALAPPDATA")
            roaming_base = Path(appdata) if appdata else home / "AppData" / "Roaming"
            local_base = (
                Path(localappdata) if localappdata else home / "AppData" / "Local"
            )

            # If legacy ~/.config/msm/config.json exists and roaming does not, prefer legacy
            if (
                self.legacy_config_file.exists()
                and not (roaming_base / "MSM" / "config.json").exists()
            ):
                self.config_dir = self.legacy_config_dir
                self.data_dir = self.legacy_config_dir
                self.cache_dir = self.legacy_config_dir / "cache"
                self.logs_dir = self.legacy_config_dir
                self.runtime_dir = self.legacy_config_dir / "runtime"
                self.servers_dir = home
                self.use_legacy_servers = True
            else:
                self.config_dir = roaming_base / "MSM"
                self.data_dir = local_base / "MSM"
                self.cache_dir = local_base / "MSM" / "cache"
                self.logs_dir = local_base / "MSM" / "logs"
                self.runtime_dir = local_base / "MSM" / "runtime"
                self.servers_dir = self.data_dir / "servers"
                self.use_legacy_servers = False
        elif self.platform.is_macos:
            mac_app_support = home / "Library" / "Application Support" / "MSM"
            mac_caches = home / "Library" / "Caches" / "MSM"
            mac_logs = home / "Library" / "Logs" / "MSM"

            if (
                self.legacy_config_file.exists()
                and not (mac_app_support / "config.json").exists()
            ):
                self.config_dir = self.legacy_config_dir
                self.data_dir = self.legacy_config_dir
                self.cache_dir = self.legacy_config_dir / "cache"
                self.logs_dir = self.legacy_config_dir
                self.runtime_dir = self.legacy_config_dir / "runtime"
                self.servers_dir = home
                self.use_legacy_servers = True
            else:
                self.config_dir = mac_app_support
                self.data_dir = mac_app_support
                self.cache_dir = mac_caches
                self.logs_dir = mac_logs
                self.runtime_dir = mac_app_support / "runtime"
                self.servers_dir = self.data_dir / "servers"
                self.use_legacy_servers = False
        else:
            # Standard Linux / FreeBSD / WSL
            xdg_config = os.environ.get("XDG_CONFIG_HOME")
            xdg_data = os.environ.get("XDG_DATA_HOME")
            xdg_cache = os.environ.get("XDG_CACHE_HOME")
            xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")

            self.config_dir = (
                Path(xdg_config) / "msm" if xdg_config else self.legacy_config_dir
            )
            self.data_dir = (
                Path(xdg_data) / "msm"
                if xdg_data
                else home / ".local" / "share" / "msm"
            )
            self.cache_dir = (
                Path(xdg_cache) / "msm" if xdg_cache else home / ".cache" / "msm"
            )
            self.logs_dir = self.data_dir / "logs"
            self.runtime_dir = (
                Path(xdg_runtime) / "msm"
                if xdg_runtime
                else Path(
                    f"/tmp/msm-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
                )
            )
            self.servers_dir = self.data_dir / "servers"
            self.use_legacy_servers = True

    @property
    def config_file(self) -> Path:
        if (
            self.legacy_config_file.exists()
            and not (self.config_dir / "config.json").exists()
        ):
            return self.legacy_config_file
        return self.config_dir / "config.json"

    @property
    def database_file(self) -> Path:
        if (
            self.legacy_database_file.exists()
            and not (self.data_dir / "msm.db").exists()
        ):
            return self.legacy_database_file
        return self.data_dir / "msm.db"

    @property
    def log_file(self) -> Path:
        if self.legacy_log_file.exists() and not (self.logs_dir / "msm.log").exists():
            return self.legacy_log_file
        return self.logs_dir / "msm.log"

    @property
    def php_dir(self) -> Path:
        return self.config_dir / "php"

    @property
    def java_dir(self) -> Path:
        return self.config_dir / "java"

    def get_legacy_server_dir(self, server_name: str) -> Path:
        """Return the legacy ~/minecraft-<name> path."""
        from utils.system import sanitize_input

        return Path.home() / f"minecraft-{sanitize_input(server_name)}"

    def resolve_server_dir(
        self,
        server_name: str,
        config: dict[str, Any] | None = None,
    ) -> Path:
        """Resolve server working directory respecting explicit config,
        legacy paths, and defaults."""
        from utils.system import sanitize_input

        safe_name = sanitize_input(server_name)

        # 1. Explicit server_dir in configuration
        if config:
            server_cfg = config.get("servers", {}).get(server_name, {})
            custom_dir = server_cfg.get("server_dir")
            if custom_dir:
                return Path(custom_dir)

        # 2. If legacy path ~/minecraft-<name> exists, preserve it!
        legacy_dir = self.get_legacy_server_dir(safe_name)
        if legacy_dir.exists():
            return legacy_dir

        # 3. If native data servers dir exists, use that
        native_dir = self.servers_dir / safe_name
        if native_dir.exists():
            return native_dir

        # 4. Default: on Termux or when legacy servers mode is set, use legacy ~/minecraft-<name>
        if self.use_legacy_servers or self.platform.is_termux:
            return legacy_dir

        # Otherwise use native path
        return native_dir if self.servers_dir != Path.home() else legacy_dir

    def ensure_directories(self) -> None:
        """Ensure standard directories exist."""
        for d in (self.config_dir, self.data_dir, self.cache_dir, self.logs_dir):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass


_GLOBAL_PATH_SERVICE: PathService | None = None


def get_path_service() -> PathService:
    """Return the global PathService instance."""
    global _GLOBAL_PATH_SERVICE
    if _GLOBAL_PATH_SERVICE is None:
        _GLOBAL_PATH_SERVICE = PathService()
    return _GLOBAL_PATH_SERVICE
