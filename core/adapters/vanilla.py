"""Vanilla Minecraft server flavor adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.adapters.base import ServerFlavorAdapter
from utils.network import download_server_binary, get_vanilla_versions
from utils.system import get_java_path


class VanillaAdapter(ServerFlavorAdapter):
    """Adapter for official Mojang Vanilla Minecraft server."""

    @property
    def flavor(self) -> str:
        return "vanilla"

    @property
    def name(self) -> str:
        return "Vanilla Minecraft"

    @property
    def description(self) -> str:
        return "Official Mojang server"

    @property
    def runtime_type(self) -> str:
        return "java"

    def list_versions(
        self, include_snapshots: bool = False, logger=None
    ) -> dict[str, Any]:
        return get_vanilla_versions(
            self.flavor, include_snapshots=include_snapshots, logger=logger
        )

    def install(
        self,
        version: str,
        version_info: dict[str, Any],
        server_dir: Path,
        config: dict[str, Any] | None = None,
        logger=None,
    ) -> tuple[Path, dict[str, Any]]:
        artifact = download_server_binary(
            self.flavor, version, version_info, server_dir, logger=logger
        )
        metadata = {
            "artifact": artifact.name,
            "flavor": self.flavor,
            "version": version,
            "runtime_type": self.runtime_type,
            "install_schema_version": 1,
        }
        return artifact, metadata

    def validate_installation(
        self,
        server_dir: Path,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        artifact = self.resolve_artifact(server_dir, runtime_metadata)
        target = server_dir / artifact
        if not target.exists():
            return False, f"Vanilla artifact {artifact} not found in {server_dir}"
        if target.stat().st_size < 10000:
            return False, f"Vanilla artifact {artifact} appears incomplete or corrupted"
        return True, "Valid Vanilla installation"

    def resolve_artifact(
        self,
        server_dir: Path,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> str:
        if runtime_metadata and runtime_metadata.get("artifact"):
            candidate = server_dir / runtime_metadata["artifact"]
            if candidate.exists():
                return candidate.name

        explicit = server_dir / "server.jar"
        if explicit.exists():
            return explicit.name

        jars = [
            j.name
            for j in server_dir.glob("*.jar")
            if not j.name.endswith(".tmp") and "installer" not in j.name.lower()
        ]
        if jars:
            return sorted(jars)[0]

        raise RuntimeError(f"No Vanilla server JAR found in {server_dir}")

    def build_startup_command(
        self,
        server_dir: Path,
        server_config: dict[str, Any],
        global_config: dict[str, Any],
        logger=None,
    ) -> list[str]:
        version = server_config.get("server_version")
        ram_mb = int(server_config.get("ram_mb", 1024))
        java_binary = get_java_path(version, global_config, logger=logger)
        if not java_binary:
            raise RuntimeError("A compatible Java runtime could not be located.")

        artifact = self.resolve_artifact(server_dir, server_config.get("runtime"))
        xms_mb = max(256, min(ram_mb // 2, 1024))
        extra_args = server_config.get("runtime", {}).get("launch_arguments", [])
        return [
            java_binary,
            f"-Xmx{ram_mb}M",
            f"-Xms{xms_mb}M",
            *extra_args,
            "-jar",
            artifact,
            "nogui",
        ]
