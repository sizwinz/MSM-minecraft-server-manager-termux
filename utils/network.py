"""HTTP helpers, version catalogs, and downloads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from core.constants import (
    DOWNLOAD_CHUNK_SIZE,
    MAX_RETRIES,
    NGROK_TIMEOUT,
    PAPER_VERSION_LOOKBACK,
    PHP_BINARIES_API,
    PHP_DIR,
    REQUEST_TIMEOUT,
    RETRY_BACKOFF,
    SERVER_FLAVORS,
)


def create_robust_session() -> requests.Session:
    session = requests.Session()
    retry_strategy = Retry(
        total=MAX_RETRIES,
        status_forcelist=[429, 500, 502, 503, 504],
        backoff_factor=RETRY_BACKOFF,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "MSM/6.0 "
                "(+https://github.com/sizwinz/MSM-minecraft-server-manager-termux)"
            ),
            "Accept": "application/json",
        }
    )
    return session


def safe_request(
    session: requests.Session,
    method: str,
    url: str,
    logger=None,
    **kwargs: Any,
) -> requests.Response | None:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    try:
        response = session.request(method=method, url=url, **kwargs)
        if 200 <= response.status_code < 300:
            return response
        if logger:
            logger.log("WARNING", f"HTTP {response.status_code} for {url}")
        return None
    except requests.RequestException as exc:
        if logger:
            logger.log("ERROR", f"Request failed for {url}: {exc}")
        return None


def is_snapshot_version(version: str) -> bool:
    lowered = version.lower()
    # Minecraft release versions are strictly numeric and dots (e.g., 1.20, 1.20.1).
    # Snapshots, pre-releases, and release candidates contain alphabetic characters
    # (e.g., 24w14a, 1.20-pre1, 1.21.11-rc3).
    return any(c.isalpha() for c in lowered)


def _fetch_paper_build(
    api_base: str,
    version: str,
    logger=None,
) -> tuple[str, dict[str, Any]] | None:
    session = create_robust_session()
    try:
        response = safe_request(
            session,
            "GET",
            f"{api_base}/versions/{version}/builds",
            logger=logger,
        )
        if not response:
            return None
        data = response.json()
        builds = data if isinstance(data, list) else data.get("builds", [])
        if not builds:
            return None
        # V3 returns newest first ([0]), V2 returned oldest first ([-1])
        latest = (
            builds[0]
            if isinstance(data, list) or (builds and "id" in builds[0])
            else builds[-1]
        )
        downloads = latest.get("downloads", {})
        server_dl = (
            downloads.get("server:default")
            or downloads.get("application")
            or (next(iter(downloads.values())) if downloads else {})
        )
        checksums = server_dl.get("checksums", {})
        sha256 = checksums.get("sha256") or server_dl.get("sha256")
        download_name = server_dl.get("name") or f"paper-{version}.jar"
        download_url = server_dl.get("url")

        return (
            version,
            {
                "latest_build": latest.get("id") or latest.get("build"),
                "download_name": download_name,
                "download_url": download_url,
                "sha256": sha256,
                "is_snapshot": is_snapshot_version(version),
            },
        )
    finally:
        session.close()


def _fetch_purpur_build(
    api_base: str,
    version: str,
    logger=None,
) -> tuple[str, dict[str, Any]] | None:
    session = create_robust_session()
    try:
        response = safe_request(session, "GET", f"{api_base}/{version}", logger=logger)
        if not response:
            return None
        latest = response.json().get("builds", {}).get("latest")
        if latest is None:
            return None
        return (
            version,
            {
                "latest_build": latest,
                "download_url": f"{api_base}/{version}/{latest}/download",
                "is_snapshot": is_snapshot_version(version),
            },
        )
    finally:
        session.close()


def get_paper_like_versions(
    flavor: str,
    include_snapshots: bool = False,
    logger=None,
) -> dict[str, Any]:
    api_base = SERVER_FLAVORS[flavor]["api_base"]
    session = create_robust_session()
    try:
        project = safe_request(session, "GET", api_base, logger=logger)
        if not project:
            return {}
        data = project.json()
        raw_versions = data.get("versions", [])
        versions: list[str] = []
        if isinstance(raw_versions, dict):
            # Fill v3 format: {"26.2": ["26.2", ...], "1.21": ["1.21.11", ...]}
            for v_list in raw_versions.values():
                if isinstance(v_list, list):
                    versions.extend(v_list)
        elif isinstance(raw_versions, list):
            # V2 or flat list: ["1.20.5", "1.20.6"]
            # If list is ascending, reverse to show newest first
            versions = list(reversed(raw_versions))

        if not include_snapshots:
            versions = [
                version for version in versions if not is_snapshot_version(version)
            ]
        selected_versions = versions[:PAPER_VERSION_LOOKBACK]
        results: dict[str, Any] = {}
        with ThreadPoolExecutor(
            max_workers=min(8, len(selected_versions) or 1)
        ) as executor:
            futures = {
                executor.submit(_fetch_paper_build, api_base, version, logger): version
                for version in selected_versions
            }
            for future in as_completed(futures):
                payload = future.result()
                if payload:
                    version, version_info = payload
                    results[version] = version_info
        return {
            version: results[version]
            for version in selected_versions
            if version in results
        }
    finally:
        session.close()


def get_purpur_versions(
    flavor: str,
    include_snapshots: bool = False,
    logger=None,
) -> dict[str, Any]:
    api_base = SERVER_FLAVORS[flavor]["api_base"]
    session = create_robust_session()
    try:
        project = safe_request(session, "GET", api_base, logger=logger)
        if not project:
            return {}
        versions = project.json().get("versions", [])
        if not include_snapshots:
            versions = [
                version for version in versions if not is_snapshot_version(version)
            ]
        selected_versions = list(reversed(versions[-PAPER_VERSION_LOOKBACK:]))
        results: dict[str, Any] = {}
        with ThreadPoolExecutor(
            max_workers=min(8, len(selected_versions) or 1)
        ) as executor:
            futures = {
                executor.submit(_fetch_purpur_build, api_base, version, logger): version
                for version in selected_versions
            }
            for future in as_completed(futures):
                payload = future.result()
                if payload:
                    version, version_info = payload
                    results[version] = version_info
        return {
            version: results[version]
            for version in selected_versions
            if version in results
        }
    finally:
        session.close()


def get_vanilla_versions(
    flavor: str,
    include_snapshots: bool = False,
    logger=None,
) -> dict[str, Any]:
    session = create_robust_session()
    try:
        response = safe_request(
            session,
            "GET",
            SERVER_FLAVORS[flavor]["api_base"],
            logger=logger,
        )
        if not response:
            return {}
        versions: dict[str, Any] = {}
        for entry in response.json().get("versions", []):
            version = entry["id"]
            is_snapshot = entry.get("type") != "release"
            if include_snapshots or not is_snapshot:
                versions[version] = {"url": entry["url"], "is_snapshot": is_snapshot}
        return versions
    finally:
        session.close()


def get_fabric_versions(
    flavor: str,
    include_snapshots: bool = False,
    logger=None,
) -> dict[str, Any]:
    api_base = SERVER_FLAVORS[flavor]["api_base"]
    session = create_robust_session()
    try:
        game_response = safe_request(session, "GET", f"{api_base}/game", logger=logger)
        loader_response = safe_request(
            session, "GET", f"{api_base}/loader", logger=logger
        )
        installer_response = safe_request(
            session, "GET", f"{api_base}/installer", logger=logger
        )
        if not all([game_response, loader_response, installer_response]):
            return {}
        latest_loader = loader_response.json()[0]["version"]
        latest_installer = installer_response.json()[0]["version"]
        versions: dict[str, Any] = {}
        for entry in game_response.json():
            version = entry["version"]
            is_snapshot = not entry["stable"]
            if include_snapshots or not is_snapshot:
                versions[version] = {
                    "loader": latest_loader,
                    "installer": latest_installer,
                    "is_snapshot": is_snapshot,
                }
        return versions
    finally:
        session.close()


def get_quilt_versions(
    flavor: str, include_snapshots: bool = False, logger=None
) -> dict[str, Any]:
    api_base = SERVER_FLAVORS[flavor]["api_base"]
    session = create_robust_session()
    try:
        game_response = safe_request(session, "GET", f"{api_base}/game", logger=logger)
        loader_response = safe_request(
            session, "GET", f"{api_base}/loader", logger=logger
        )
        installer_response = safe_request(
            session, "GET", f"{api_base}/installer", logger=logger
        )
        if not all([game_response, loader_response, installer_response]):
            return {}
        latest_loader = loader_response.json()[0]["version"]
        installer_data = installer_response.json()[0]
        latest_installer = installer_data["version"]
        installer_url = installer_data.get("url")
        versions: dict[str, Any] = {}
        for entry in game_response.json():
            version = entry["version"]
            snapshot = is_snapshot_version(version)
            if include_snapshots or not snapshot:
                versions[version] = {
                    "loader": latest_loader,
                    "installer": latest_installer,
                    "installer_url": installer_url,
                    "is_snapshot": snapshot,
                }
        return versions
    finally:
        session.close()


def get_pocketmine_versions(
    flavor: str,
    include_snapshots: bool = False,
    logger=None,
) -> dict[str, Any]:
    session = create_robust_session()
    try:
        response = safe_request(
            session,
            "GET",
            SERVER_FLAVORS[flavor]["api_base"],
            logger=logger,
        )
        if not response:
            return {}
        versions: dict[str, Any] = {}
        for release in response.json():
            if release.get("draft"):
                continue
            snapshot = bool(release.get("prerelease"))
            if not include_snapshots and snapshot:
                continue
            for asset in release.get("assets", []):
                if asset["name"].endswith(".phar"):
                    versions[release["tag_name"]] = {
                        "download_url": asset["browser_download_url"],
                        "filename": asset["name"],
                        "is_snapshot": snapshot,
                    }
                    break
        return versions
    finally:
        session.close()


def get_versions_for_flavor(
    flavor: str,
    include_snapshots: bool = False,
    logger=None,
) -> dict[str, Any]:
    fetchers = {
        "paper": get_paper_like_versions,
        "folia": get_paper_like_versions,
        "purpur": get_purpur_versions,
        "vanilla": get_vanilla_versions,
        "fabric": get_fabric_versions,
        "quilt": get_quilt_versions,
        "pocketmine": get_pocketmine_versions,
    }
    fetcher = fetchers.get(flavor)
    return fetcher(flavor, include_snapshots, logger=logger) if fetcher else {}


def _determine_download(
    flavor: str,
    version: str,
    version_info: dict[str, Any],
    logger=None,
) -> tuple[str, str]:
    target_filename = "server.jar"
    session = create_robust_session()
    try:
        if flavor in {"paper", "folia"}:
            if version_info.get("download_url"):
                return version_info["download_url"], target_filename
            build = version_info["latest_build"]
            filename = version_info.get("download_name", f"{flavor}-{version}.jar")
            api_base = SERVER_FLAVORS[flavor]["api_base"]
            return (
                f"{api_base}/versions/{version}/builds/{build}/downloads/{filename}",
                target_filename,
            )
        if flavor == "purpur":
            return version_info["download_url"], target_filename
        if flavor == "vanilla":
            response = safe_request(session, "GET", version_info["url"], logger=logger)
            if not response:
                raise RuntimeError("Failed to resolve vanilla download URL")
            return response.json()["downloads"]["server"]["url"], target_filename
        if flavor == "fabric":
            return (
                "https://meta.fabricmc.net/v2/versions/loader/"
                f"{version}/{version_info['loader']}/{version_info['installer']}/server/jar",
                target_filename,
            )
        if flavor == "quilt":
            inst_v = version_info["installer"]
            maven_fallback = (
                "https://maven.quiltmc.org/repository/release/org/quiltmc/"
                f"quilt-installer/{inst_v}/quilt-installer-{inst_v}.jar"
            )
            installer_url = version_info.get("installer_url") or maven_fallback
            return installer_url, "quilt-installer.jar"
        if flavor == "pocketmine":
            return version_info["download_url"], version_info["filename"]
        raise RuntimeError(f"Unsupported server flavor: {flavor}")
    finally:
        session.close()


def download_server_binary(
    flavor: str,
    version: str,
    version_info: dict[str, Any],
    server_dir: str | Path,
    logger=None,
) -> Path:
    download_url, target_filename = _determine_download(
        flavor,
        version,
        version_info,
        logger=logger,
    )
    target_path = Path(server_dir) / target_filename
    session = create_robust_session()
    try:
        response = safe_request(
            session, "GET", download_url, logger=logger, stream=True
        )
        if not response:
            raise RuntimeError(f"Download failed for {download_url}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with target_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    handle.write(chunk)
        return target_path
    finally:
        session.close()


def get_ngrok_public_url(
    port: int,
    logger=None,
    timeout: int | float = NGROK_TIMEOUT,
) -> str | None:
    session = create_robust_session()
    try:
        response = safe_request(
            session,
            "GET",
            "http://127.0.0.1:4040/api/tunnels",
            logger=logger,
            timeout=timeout,
        )
        if not response:
            return None
        tunnels = response.json().get("tunnels", [])
        if not tunnels:
            return None
        port_str = str(port)
        for tunnel in tunnels:
            address = str(tunnel.get("config", {}).get("addr", ""))
            if port_str in address or address.endswith(f":{port}"):
                return tunnel.get("public_url")
        for tunnel in tunnels:
            if tunnel.get("proto") == "tcp" and tunnel.get("public_url"):
                return tunnel.get("public_url")
        return tunnels[0].get("public_url")
    finally:
        session.close()


def download_ngrok_binary(logger=None) -> Path | None:
    import platform
    import tarfile

    arch = platform.machine().lower()
    if arch in ("aarch64", "arm64"):
        ngrok_arch = "arm64"
    elif arch in ("x86_64", "amd64"):
        ngrok_arch = "amd64"
    else:
        if logger:
            logger.log(
                "ERROR", f"Unsupported architecture for ngrok auto-download: {arch}"
            )
        return None

    url = f"https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-{ngrok_arch}.tgz"

    bin_dir = Path.home() / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    tar_path = bin_dir / "ngrok.tgz"

    session = create_robust_session()
    try:
        response = safe_request(session, "GET", url, logger=logger, stream=True)
        if not response:
            return None
        with tar_path.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                handle.write(chunk)

        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(path=bin_dir)

        ngrok_bin = bin_dir / "ngrok"
        if ngrok_bin.exists():
            ngrok_bin.chmod(0o755)

        tar_path.unlink(missing_ok=True)
        return ngrok_bin
    except Exception as exc:
        if logger:
            logger.log("ERROR", f"Failed to download ngrok: {exc}")
        return None
    finally:
        session.close()


def get_system_arch_and_os() -> tuple[str, str]:
    import platform
    import sys
    from utils.system import running_on_termux

    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64", "armv8", "armv8l"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("armv7l", "armv7", "armhf", "arm"):
        arch = "arm"
    elif machine in ("x86", "i386", "i686"):
        arch = "x86"
    else:
        arch = machine

    if (
        running_on_termux()
        or "android" in sys.platform.lower()
        or Path("/data/data/com.termux").exists()
    ):
        os_name = "Android"
    elif sys.platform.startswith("linux"):
        os_name = "Linux"
    elif sys.platform.startswith("darwin"):
        os_name = "MacOS"
    elif sys.platform.startswith("win"):
        os_name = "Windows"
    else:
        os_name = sys.platform
    return os_name, arch


def download_php_binary(
    target_dir: str | Path | None = None,
    logger=None,
) -> Path | None:
    from utils.archive import safe_extract_tar, safe_extract_zip

    dest_dir = Path(target_dir) if target_dir else PHP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    os_name, arch = get_system_arch_and_os()
    session = create_robust_session()
    try:
        response = safe_request(session, "GET", PHP_BINARIES_API, logger=logger)
        if not response:
            if logger:
                logger.log("ERROR", "Failed to query PocketMine PHP binary releases.")
            return None

        releases = response.json()
        if not isinstance(releases, list):
            return None

        matched_asset = None
        for release in releases:
            if release.get("draft"):
                continue
            for asset in release.get("assets", []):
                name = asset.get("name", "")
                name_lower = name.lower()
                if (
                    name_lower.startswith("z-")
                    or "debug" in name_lower
                    or "symbols" in name_lower
                ):
                    continue

                if os_name == "Android" and arch == "arm64":
                    if "android" in name_lower and (
                        "arm64" in name_lower or "aarch64" in name_lower
                    ):
                        matched_asset = asset
                        break
                elif os_name == "Linux" and arch == "x86_64":
                    if "linux" in name_lower and (
                        "x86_64" in name_lower
                        or "x64" in name_lower
                        or "amd64" in name_lower
                    ):
                        matched_asset = asset
                        break
                elif os_name == "Linux" and arch == "arm64":
                    if "linux" in name_lower and (
                        "arm64" in name_lower or "aarch64" in name_lower
                    ):
                        matched_asset = asset
                        break
                elif os_name == "Windows":
                    if "windows" in name_lower and (
                        "x64" in name_lower or "x86_64" in name_lower
                    ):
                        matched_asset = asset
                        break
                elif os_name == "MacOS":
                    if "macos" in name_lower or "darwin" in name_lower:
                        if arch == "arm64" and (
                            "arm64" in name_lower or "aarch64" in name_lower
                        ):
                            matched_asset = asset
                            break
                        elif arch == "x86_64" and (
                            "x86_64" in name_lower or "x64" in name_lower
                        ):
                            matched_asset = asset
                            break
            if matched_asset:
                break

        if not matched_asset:
            if logger:
                logger.log(
                    "ERROR",
                    f"No prebuilt PMMP PHP binary available for {os_name} ({arch}).",
                )
            return None

        download_url = matched_asset.get("browser_download_url")
        asset_name = matched_asset.get("name", "php-binary.tar.gz")
        archive_path = dest_dir / asset_name

        if logger:
            logger.log("INFO", f"Downloading PHP binary for PocketMine: {asset_name}")

        dl_resp = safe_request(session, "GET", download_url, logger=logger, stream=True)
        if not dl_resp:
            if logger:
                logger.log(
                    "ERROR", f"Failed to download PHP binary from {download_url}"
                )
            return None

        with archive_path.open("wb") as handle:
            for chunk in dl_resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                if chunk:
                    handle.write(chunk)

        if asset_name.endswith(".zip"):
            safe_extract_zip(archive_path, dest_dir)
        else:
            safe_extract_tar(archive_path, dest_dir)

        archive_path.unlink(missing_ok=True)

        for candidate_rel in (
            Path("bin/php7/bin/php"),
            Path("bin/php/bin/php"),
            Path("bin/php"),
            Path("php"),
            Path("bin/php.exe"),
            Path("php.exe"),
        ):
            cand_path = dest_dir / candidate_rel
            if cand_path.is_file():
                try:
                    cand_path.chmod(0o755)
                except OSError:
                    pass
                return cand_path

        for found_file in dest_dir.rglob("php*"):
            if found_file.is_file() and found_file.name in ("php", "php.exe"):
                try:
                    found_file.chmod(0o755)
                except OSError:
                    pass
                return found_file

        return None
    except Exception as exc:
        if logger:
            logger.log("ERROR", f"Error provisioning PocketMine PHP binary: {exc}")
        return None
    finally:
        session.close()
