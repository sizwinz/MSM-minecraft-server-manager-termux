"""Abstract server flavor adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ServerFlavorAdapter(ABC):
    """Encapsulates version fetching, installation, validation,
    and launch command building for a server flavor."""

    @property
    @abstractmethod
    def flavor(self) -> str: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def runtime_type(self) -> str:
        """'java' or 'php'."""
        ...

    @property
    def default_port(self) -> int:
        return 25565

    @property
    def default_protocol(self) -> str:
        return "tcp"

    @property
    def min_ram(self) -> int:
        return 512

    @abstractmethod
    def list_versions(
        self,
        include_snapshots: bool = False,
        logger=None,
    ) -> dict[str, Any]:
        """Fetch and return catalog of available versions mapped to build metadata."""
        ...

    @abstractmethod
    def install(
        self,
        version: str,
        version_info: dict[str, Any],
        server_dir: Path,
        config: dict[str, Any] | None = None,
        logger=None,
    ) -> tuple[Path, dict[str, Any]]:
        """Download and install the server, returning (launch_artifact_path, runtime_metadata)."""
        ...

    @abstractmethod
    def validate_installation(
        self,
        server_dir: Path,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Verify that installed binaries and libraries are present and valid."""
        ...

    @abstractmethod
    def resolve_artifact(
        self,
        server_dir: Path,
        runtime_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Deterministically resolve the server launch artifact filename."""
        ...

    @abstractmethod
    def build_startup_command(
        self,
        server_dir: Path,
        server_config: dict[str, Any],
        global_config: dict[str, Any],
        logger=None,
    ) -> list[str]:
        """Construct the executable command list for launching the server."""
        ...

    def required_runtime(self, version: str | None) -> str:
        """Return required runtime major version string (e.g. '8', '17', '21', '25', or '8.2')."""
        if self.runtime_type == "php":
            return "8.2"
        from utils.system import get_required_java

        return get_required_java(version)
