"""Configuration loading, migrations, and persistence."""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path
from typing import Any

from core.constants import DEFAULT_BACKUP_INTERVAL_HOURS

DEFAULT_CONFIG = {
    "current_server": None,
    "java_homes": {},
    "php_path": None,
    "tunnel_defaults": {
        "provider": "playit",
        "binary_path": "playit-cli",
        "autostart": False,
        "protocol": "tcp",
        "local_host": "127.0.0.1",
    },
    "servers": {},
}

DEFAULT_SERVER_CONFIG = {
    "server_flavor": None,
    "server_version": None,
    "eula_accepted": True,
    "ram_mb": 2048,
    "php_path": None,
    "auto_restart": False,
    "backup_settings": {
        "enabled": False,
        "interval_hours": DEFAULT_BACKUP_INTERVAL_HOURS,
    },
    "tunnel": {
        "enabled": False,
        "provider": "playit",
        "binary_path": "playit-cli",
        "autostart": False,
        "protocol": "tcp",
        "local_host": "127.0.0.1",
        "local_port": None,
        "playit_tunnel_id": None,
        "last_endpoint": None,
    },
    "rcon": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 25575,
        "password": "",
    },
    "server_settings": {
        "motd": "A Minecraft Server",
        "port": 25565,
        "max-players": 20,
        "online-mode": "true",
        "enable-rcon": "false",
        "rcon.port": 25575,
    },
}


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


class ConfigManager:
    """Centralized config manager with recursive default migrations."""

    def __init__(self, path: str | Path, logger):
        self.path = Path(path)
        self.logger = logger
        self._lock = threading.RLock()
        self._config = self._load_from_disk()

    def _default_server(self, server_name: str | None = None) -> dict[str, Any]:
        config = copy.deepcopy(DEFAULT_SERVER_CONFIG)
        if server_name:
            config["server_settings"]["motd"] = f"{server_name} Server"
        return config

    def _normalize(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = _deep_merge(DEFAULT_CONFIG, config)
        normalized["servers"] = normalized.get("servers", {}) or {}
        for server_name, server_config in list(normalized["servers"].items()):
            normalized["servers"][server_name] = _deep_merge(
                self._default_server(server_name),
                server_config or {},
            )
        current_server = normalized.get("current_server")
        if current_server not in normalized["servers"]:
            normalized["current_server"] = next(iter(normalized["servers"]), None)
        return normalized

    def _load_from_disk(self) -> dict[str, Any]:
        if not self.path.exists():
            return copy.deepcopy(DEFAULT_CONFIG)
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except json.JSONDecodeError:
            backup_path = self.path.with_suffix(
                f"{self.path.suffix}.bak_{int(time.time())}"
            )
            self.path.replace(backup_path)
            self.logger.log(
                "ERROR",
                f"Config file was corrupted and has been backed up to {backup_path}",
            )
            return copy.deepcopy(DEFAULT_CONFIG)
        return self._normalize(raw)

    def load(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._config)

    def reload(self) -> dict[str, Any]:
        with self._lock:
            self._config = self._load_from_disk()
            return copy.deepcopy(self._config)

    def save(self, config: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalize(config)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=4, sort_keys=True)
        tmp_path.replace(self.path)
        with self._lock:
            self._config = copy.deepcopy(normalized)
        return copy.deepcopy(normalized)

    def mutate(self, updater) -> dict[str, Any]:
        with self._lock:
            config = copy.deepcopy(self._config)
            updater(config)
            return self.save(config)

    def ensure_server(self, server_name: str) -> dict[str, Any]:
        def updater(config: dict[str, Any]) -> None:
            config.setdefault("servers", {})
            config["servers"].setdefault(server_name, self._default_server(server_name))
            config["current_server"] = server_name

        return self.mutate(updater)
