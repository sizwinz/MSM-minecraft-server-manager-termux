"""Quilt modded server flavor adapter with multi-stage installer support."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.adapters.base import ServerFlavorAdapter
from utils.network import (
    create_robust_session,
    get_quilt_versions,
    safe_request,
)
from utils.system import get_java_path, run_command


class QuiltAdapter(ServerFlavorAdapter):
    """Adapter for Quilt modern modded server platform with multi-stage installation."""

    @property
    def flavor(self) -> str:
        return "quilt"

    @property
    def name(self) -> str:
        return "Quilt"

    @property
    def description(self) -> str:
        return "Fabric-compatible modern fork"

    @property
    def runtime_type(self) -> str:
        return "java"

    @property
    def min_ram(self) -> int:
        return 768

    def list_versions(
        self, include_snapshots: bool = False, logger=None
    ) -> dict[str, Any]:
        return get_quilt_versions(
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
        inst_v = version_info["installer"]
        maven_fallback = (
            "https://maven.quiltmc.org/repository/release/org/quiltmc/"
            f"quilt-installer/{inst_v}/quilt-installer-{inst_v}.jar"
        )
        installer_url = version_info.get("installer_url") or maven_fallback
        installer_path = server_dir / f".quilt-installer-{inst_v}.jar"

        if logger:
            logger.log("INFO", f"Downloading Quilt installer from {installer_url}...")

        session = create_robust_session()
        try:
            resp = safe_request(
                session, "GET", installer_url, logger=logger, stream=True
            )
            if not resp:
                raise RuntimeError(
                    f"Failed to download Quilt installer from {installer_url}"
                )
            with installer_path.open("wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        handle.write(chunk)
        finally:
            session.close()

        # Execute Quilt installer to generate libraries and launch jar
        java_binary = get_java_path(version, config or {}, logger=logger)
        if not java_binary:
            raise RuntimeError(
                "Compatible Java runtime required to execute Quilt server installer."
            )

        if logger:
            logger.log("INFO", f"Executing Quilt installer for Minecraft {version}...")

        install_cmd = [
            java_binary,
            "-jar",
            str(installer_path),
            "install",
            "server",
            version,
            "--download-server",
        ]
        result = run_command(
            install_cmd,
            logger=logger,
            cwd=server_dir,
            check=False,
            capture_output=True,
            timeout=180,
        )
        if not result or result.returncode != 0:
            err_msg = result.stderr if result else "Execution failed"
            raise RuntimeError(f"Quilt installer failed: {err_msg}")

        launch_artifact = server_dir / "quilt-server-launch.jar"
        if not launch_artifact.exists():
            # Check if alternative launch jar or server.jar was created
            server_jar = server_dir / "server.jar"
            if not server_jar.exists():
                raise RuntimeError(
                    "Quilt installer completed but quilt-server-launch.jar was not produced."
                )

        # Remove or isolate installer JAR so it is never launched as server
        installer_path.unlink(missing_ok=True)

        target_artifact = (
            launch_artifact if launch_artifact.exists() else server_dir / "server.jar"
        )
        metadata = {
            "artifact": target_artifact.name,
            "flavor": self.flavor,
            "version": version,
            "runtime_type": self.runtime_type,
            "installer_version": inst_v,
            "loader_version": version_info.get("loader"),
            "install_schema_version": 1,
        }
        return target_artifact, metadata

    def validate_installation(
        self,
        server_dir: Path,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        try:
            artifact = self.resolve_artifact(server_dir, runtime_metadata)
        except Exception as exc:
            return False, str(exc)

        if "installer" in artifact.lower():
            return (
                False,
                f"Installer JAR ({artifact}) cannot be used as launch artifact.",
            )

        target = server_dir / artifact
        if not target.exists():
            return False, f"Quilt launch artifact {artifact} not found in {server_dir}"

        if (
            not (server_dir / "libraries").exists()
            and not (server_dir / "server.jar").exists()
        ):
            return (
                False,
                "Quilt server installation missing required libraries or server.jar",
            )

        return True, "Valid Quilt installation"

    def resolve_artifact(
        self,
        server_dir: Path,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> str:
        if runtime_metadata and runtime_metadata.get("artifact"):
            art_name = runtime_metadata["artifact"]
            if "installer" not in art_name.lower():
                candidate = server_dir / art_name
                if candidate.exists():
                    return candidate.name

        preferred = server_dir / "quilt-server-launch.jar"
        if preferred.exists():
            return preferred.name

        jars = [
            j.name
            for j in server_dir.glob("quilt-*.jar")
            if not j.name.endswith(".tmp") and "installer" not in j.name.lower()
        ]
        if jars:
            return sorted(jars)[0]

        explicit = server_dir / "server.jar"
        if explicit.exists():
            return explicit.name

        all_jars = [
            j.name
            for j in server_dir.glob("*.jar")
            if not j.name.endswith(".tmp") and "installer" not in j.name.lower()
        ]
        if all_jars:
            return sorted(all_jars)[0]

        raise RuntimeError(f"No valid Quilt launch JAR found in {server_dir}")

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
