"""Backup creation and safe restore helpers."""

from __future__ import annotations

import os
import stat
import shutil
import tempfile
import tarfile
import zipfile
from pathlib import Path

from core.constants import (
    BACKUP_COMPRESSION,
    BACKUP_COMPRESSION_LEVEL,
    WORLD_SUFFIX_PATTERN,
)
from utils.properties import load_properties


def discover_world_directories(server_dir: str | Path) -> list[Path]:
    base_dir = Path(server_dir)
    properties = load_properties(base_dir / "server.properties")
    level_name = properties.get("level-name", "world")
    preferred = [
        base_dir / level_name,
        base_dir / f"{level_name}_nether",
        base_dir / f"{level_name}_the_end",
    ]
    for path in preferred:
        if path.is_symlink() or not path.resolve().is_relative_to(base_dir.resolve()):
            raise ValueError("World directory must stay inside the server directory.")
    world_dirs = [path for path in preferred if path.is_dir()]
    if world_dirs:
        return world_dirs
    if (base_dir / "worlds").is_dir():
        if (base_dir / "worlds").is_symlink():
            raise ValueError("World directory cannot be a symlink.")
        return [base_dir / "worlds"]
    return [
        child
        for child in base_dir.iterdir()
        if child.is_dir() and WORLD_SUFFIX_PATTERN.match(child.name)
    ]


def create_backup_archive(
    server_dir: str | Path,
    backup_path: str | Path,
    world_dirs: list[Path],
) -> int:
    server_path = Path(server_dir)
    archive_path = Path(backup_path)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_suffix(archive_path.suffix + ".tmp")
    try:
        _write_backup(server_path, temporary, world_dirs)
        temporary.replace(archive_path)
    finally:
        temporary.unlink(missing_ok=True)
    return archive_path.stat().st_size


def _write_backup(
    server_path: Path, archive_path: Path, world_dirs: list[Path]
) -> None:
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=BACKUP_COMPRESSION,
        compresslevel=BACKUP_COMPRESSION_LEVEL,
    ) as archive:
        for world_dir in world_dirs:
            if world_dir.is_symlink() or not world_dir.resolve().is_relative_to(
                server_path.resolve()
            ):
                raise ValueError(
                    "World directory must stay inside the server directory."
                )
            archive.write(world_dir, world_dir.relative_to(server_path))
            for file_path in world_dir.rglob("*"):
                if file_path.is_symlink():
                    raise ValueError(
                        f"Cannot back up a world containing a symlink: {file_path.name}"
                    )
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(server_path))


def restore_world_backup(zip_path: str | Path, server_dir: str | Path) -> None:
    """Validate in staging, swap complete worlds, and roll back if a swap fails."""
    server = Path(server_dir).resolve()
    with tempfile.TemporaryDirectory(prefix=".msm-restore-", dir=server) as scratch:
        stage = Path(scratch) / "new"
        stage.mkdir()
        safe_extract_zip(zip_path, stage)
        worlds = list(stage.iterdir())
        if not worlds or any(not path.is_dir() for path in worlds):
            raise ValueError("Backup must contain world directories, not server files.")
        allowed = {path.name for path in discover_world_directories(server)}
        properties = load_properties(server / "server.properties")
        level = properties.get("level-name", "world")
        allowed.update((level, f"{level}_nether", f"{level}_the_end", "worlds"))
        if any(
            path.name not in allowed and not WORLD_SUFFIX_PATTERN.match(path.name)
            for path in worlds
        ):
            raise ValueError(
                "Backup contains directories that are not Minecraft worlds."
            )
        previous = Path(scratch) / "old"
        previous.mkdir()
        moved = []
        installed = []
        try:
            for world in worlds:
                target = server / world.name
                if target.is_symlink():
                    raise ValueError("Cannot restore over a symlink.")
                if target.exists():
                    target.replace(previous / world.name)
                    moved.append(world.name)
                world.replace(target)
                installed.append(world.name)
        except Exception:
            for name in reversed(installed):
                shutil.rmtree(server / name)
            for name in reversed(moved):
                (previous / name).replace(server / name)
            raise


def safe_extract_zip(zip_path: str | Path, destination_dir: str | Path) -> None:
    destination = Path(destination_dir).resolve()
    with zipfile.ZipFile(zip_path, "r") as archive:
        for member in archive.infolist():
            member_path = destination / member.filename
            resolved_path = member_path.resolve(strict=False)
            if os.path.commonpath([destination, resolved_path]) != str(destination):
                raise ValueError(f"Blocked unsafe archive member: {member.filename}")
            unix_mode = member.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise ValueError(f"Blocked symlink in archive: {member.filename}")
        archive.extractall(destination)


def safe_extract_tar(tar_path: str | Path, destination_dir: str | Path) -> None:
    destination = Path(destination_dir).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "r:*") as archive:
        for member in archive.getmembers():
            if member.isdev() or member.isfifo():
                raise ValueError(f"Blocked special file in archive: {member.name}")
            member_path = destination / member.name
            resolved_path = member_path.resolve(strict=False)
            if os.path.commonpath([destination, resolved_path]) != str(destination):
                raise ValueError(f"Blocked unsafe archive member: {member.name}")
            if member.islnk() or member.issym():
                link_base = destination if member.islnk() else member_path.parent
                target_path = (link_base / member.linkname).resolve(strict=False)
                if os.path.commonpath([destination, target_path]) != str(destination):
                    raise ValueError(
                        f"Blocked unsafe link in archive: {member.name} -> {member.linkname}"
                    )
        archive.extractall(destination)
