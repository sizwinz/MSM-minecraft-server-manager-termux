"""Paper, Folia, and Purpur server flavor adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.adapters.base import ServerFlavorAdapter
from utils.network import (
    download_server_binary,
    get_paper_like_versions,
    get_purpur_versions,
)
from utils.system import get_java_path


class PaperAdapter(ServerFlavorAdapter):
    """Adapter for PaperMC server software."""

    @property
    def flavor(self) -> str:
        return "paper"

    @property
    def name(self) -> str:
        return "PaperMC"

    @property
    def description(self) -> str:
        return "High-performance server with plugin support"

    @property
    def runtime_type(self) -> str:
        return "java"

    def list_versions(
        self, include_snapshots: bool = False, logger=None
    ) -> dict[str, Any]:
        return get_paper_like_versions(
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
            return False, f"Paper artifact {artifact} not found in {server_dir}"
        if target.stat().st_size < 10000:
            return False, f"Paper artifact {artifact} appears incomplete or corrupted"
        return True, "Valid Paper installation"

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
            for j in server_dir.glob("paper-*.jar")
            if not j.name.endswith(".tmp")
        ]
        if jars:
            return sorted(jars)[0]

        all_jars = [
            j.name
            for j in server_dir.glob("*.jar")
            if not j.name.endswith(".tmp") and "installer" not in j.name.lower()
        ]
        if all_jars:
            return sorted(all_jars)[0]

        raise RuntimeError(f"No Paper server JAR found in {server_dir}")

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


class FoliaAdapter(PaperAdapter):
    """Adapter for Folia multi-threaded server software."""

    @property
    def flavor(self) -> str:
        return "folia"

    @property
    def name(self) -> str:
        return "Folia"

    @property
    def description(self) -> str:
        return "Regionized multi-threaded Paper fork"

    @property
    def min_ram(self) -> int:
        return 1024

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
            for j in server_dir.glob("folia-*.jar")
            if not j.name.endswith(".tmp")
        ]
        if jars:
            return sorted(jars)[0]

        all_jars = [
            j.name
            for j in server_dir.glob("*.jar")
            if not j.name.endswith(".tmp") and "installer" not in j.name.lower()
        ]
        if all_jars:
            return sorted(all_jars)[0]

        raise RuntimeError(f"No Folia server JAR found in {server_dir}")


class PurpurAdapter(PaperAdapter):
    """Adapter for Purpur server software."""

    @property
    def flavor(self) -> str:
        return "purpur"

    @property
    def name(self) -> str:
        return "Purpur"

    @property
    def description(self) -> str:
        return "Paper fork with extra gameplay controls"

    def list_versions(
        self, include_snapshots: bool = False, logger=None
    ) -> dict[str, Any]:
        return get_purpur_versions(
            self.flavor, include_snapshots=include_snapshots, logger=logger
        )

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
            for j in server_dir.glob("purpur-*.jar")
            if not j.name.endswith(".tmp")
        ]
        if jars:
            return sorted(jars)[0]

        all_jars = [
            j.name
            for j in server_dir.glob("*.jar")
            if not j.name.endswith(".tmp") and "installer" not in j.name.lower()
        ]
        if all_jars:
            return sorted(all_jars)[0]

        raise RuntimeError(f"No Purpur server JAR found in {server_dir}")
