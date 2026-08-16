"""Backup creation and safe restore helpers."""

from __future__ import annotations

import os
import stat
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
    world_dirs = [path for path in preferred if path.is_dir()]
    if world_dirs:
        return world_dirs
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
    with zipfile.ZipFile(
        archive_path,
        "w",
        compression=BACKUP_COMPRESSION,
        compresslevel=BACKUP_COMPRESSION_LEVEL,
    ) as archive:
        for world_dir in world_dirs:
            for file_path in world_dir.rglob("*"):
                if file_path.is_file():
                    archive.write(file_path, file_path.relative_to(server_path))
    return archive_path.stat().st_size


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
            member_path = destination / member.name
            resolved_path = member_path.resolve(strict=False)
            if os.path.commonpath([destination, resolved_path]) != str(destination):
                raise ValueError(f"Blocked unsafe archive member: {member.name}")
            if member.islnk() or member.issym():
                target_path = (member_path.parent / member.linkname).resolve(
                    strict=False
                )
                if os.path.commonpath([destination, target_path]) != str(destination):
                    raise ValueError(
                        f"Blocked unsafe link in archive: {member.name} -> {member.linkname}"
                    )
        archive.extractall(destination)
