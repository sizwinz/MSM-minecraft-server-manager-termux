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
        builds = response.json().get("builds", [])
        if not builds:
            return None
        latest = builds[-1]
        application = latest.get("downloads", {}).get("application", {})
        return (
            version,
            {
                "latest_build": latest.get("build"),
                "download_name": application.get("name"),
                "sha256": application.get("sha256"),
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
        latest_installer = installer_response.json()[0]["version"]
        versions: dict[str, Any] = {}
        for entry in game_response.json():
            version = entry["version"]
            snapshot = is_snapshot_version(version)
            if include_snapshots or not snapshot:
                versions[version] = {
                    "loader": latest_loader,
                    "installer": latest_installer,
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
            build = version_info["latest_build"]
            filename = version_info["download_name"]
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
            return (
                "https://meta.quiltmc.org/v3/versions/loader/"
                f"{version}/{version_info['loader']}/{version_info['installer']}/server/jar",
                target_filename,
            )
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
        for tunnel in response.json().get("tunnels", []):
            address = tunnel.get("config", {}).get("addr", "")
            if address.endswith(f":{port}") or address.endswith(str(port)):
                return tunnel.get("public_url")
        return None
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
