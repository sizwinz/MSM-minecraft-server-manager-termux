"""PocketMine-MP Bedrock Edition server flavor adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.adapters.base import ServerFlavorAdapter
from utils.network import download_server_binary, get_pocketmine_versions
from utils.system import detect_php_runtime, get_php_path


class PocketMineAdapter(ServerFlavorAdapter):
    """Adapter for PocketMine-MP Minecraft Bedrock server software."""

    @property
    def flavor(self) -> str:
        return "pocketmine"

    @property
    def name(self) -> str:
        return "PocketMine-MP"

    @property
    def description(self) -> str:
        return "Bedrock Edition server software"

    @property
    def runtime_type(self) -> str:
        return "php"

    @property
    def default_port(self) -> int:
        return 19132

    @property
    def default_protocol(self) -> str:
        return "udp"

    @property
    def min_ram(self) -> int:
        return 256

    def list_versions(
        self, include_snapshots: bool = False, logger=None
    ) -> dict[str, Any]:
        return get_pocketmine_versions(
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
        # Ensure PHP is provisioned or checked
        php_binary = get_php_path(
            config or {}, server_dir, auto_install=True, logger=logger
        )
        metadata = {
            "artifact": artifact.name,
            "flavor": self.flavor,
            "version": version,
            "runtime_type": self.runtime_type,
            "php_binary": str(php_binary) if php_binary else None,
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
            return False, f"PocketMine PHAR {artifact} not found in {server_dir}"
        return True, "Valid PocketMine installation"

    def resolve_artifact(
        self,
        server_dir: Path,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> str:
        if runtime_metadata and runtime_metadata.get("artifact"):
            candidate = server_dir / runtime_metadata["artifact"]
            if candidate.exists():
                return candidate.name

        preferred = server_dir / "PocketMine-MP.phar"
        if preferred.exists():
            return preferred.name

        phars = [
            p.name for p in server_dir.glob("*.phar") if not p.name.endswith(".tmp")
        ]
        if phars:
            return sorted(phars)[0]

        raise RuntimeError(f"No PocketMine PHAR found in {server_dir}")

    def build_startup_command(
        self,
        server_dir: Path,
        server_config: dict[str, Any],
        global_config: dict[str, Any],
        logger=None,
    ) -> list[str]:
        ram_mb = int(server_config.get("ram_mb", 512))
        php_binary = get_php_path(
            global_config,
            server_dir,
            auto_install=True,
            logger=logger,
        )
        if not php_binary:
            raise RuntimeError(
                "A compatible PocketMine PHP runtime (ZTS + pmmpthread) could not be located."
            )

        php_info = detect_php_runtime(php_binary, logger=logger)
        runner_prefix = php_info.get("runner_prefix", [])
        artifact = self.resolve_artifact(server_dir, server_config.get("runtime"))

        return runner_prefix + [
            str(php_binary),
            f"-dmemory_limit={ram_mb}M",
            artifact,
            "--no-wizard",
        ]
