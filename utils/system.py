"""System, process, and filesystem helpers."""

from __future__ import annotations

import ipaddress
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

from core.constants import (
    ALLOWED_FILENAME_CHARS,
    COLLAPSE_DOTS_PATTERN,
    COMMON_JAVA_HOME_BASES,
    COMMON_PHP_BASES,
    INVALID_FILENAME_CHARS,
    MAX_FILENAME_LENGTH,
    MAX_RAM_PERCENTAGE,
    PHP_DIR,
)


def sanitize_input(value: str, max_length: int = MAX_FILENAME_LENGTH) -> str:
    """Constrain user input to a safe filename-like token."""
    if not value or not isinstance(value, str):
        return str(uuid.uuid4())[:8]
    value = os.path.basename(value.strip())
    if len(value) > max_length:
        value = value[:max_length]
    if not ALLOWED_FILENAME_CHARS.match(value):
        value = INVALID_FILENAME_CHARS.sub("_", value)
    value = COLLAPSE_DOTS_PATTERN.sub(".", value).strip(".-")
    return value or str(uuid.uuid4())[:8]


def run_command(
    command: list[str] | str,
    logger=None,
    check: bool = True,
    capture_output: bool = False,
    timeout: int | None = None,
    cwd: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str] | None:
    """Execute a subprocess without shell expansion."""
    if isinstance(command, str):
        command = shlex.split(command)
    exec_env = os.environ.copy()
    if env:
        exec_env.update(env)
    if running_on_termux():
        termux_tmp = os.environ.get("TMPDIR") or "/data/data/com.termux/files/usr/tmp"
        try:
            Path(termux_tmp).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        exec_env.setdefault("TMPDIR", termux_tmp)
        exec_env.setdefault("SCREENDIR", termux_tmp)
    try:
        if logger:
            logger.log(
                "DEBUG",
                "Executing command",
                command=command,
                cwd=str(cwd or Path.cwd()),
            )
        return subprocess.run(
            command,
            check=check,
            shell=False,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=exec_env,
        )
    except subprocess.CalledProcessError as exc:
        if logger:
            logger.log(
                "ERROR", "Command failed", command=command, returncode=exc.returncode
            )
        return None
    except subprocess.TimeoutExpired:
        if logger:
            logger.log("ERROR", "Command timed out", command=command, timeout=timeout)
        return None
    except FileNotFoundError:
        if logger:
            logger.log("ERROR", "Command not found", command=command)
        return None


def check_disk_space(
    path: str | os.PathLike[str], required_mb: int = 1000, logger=None
) -> bool:
    try:
        free_mb = shutil.disk_usage(path).free // (1024 * 1024)
    except OSError as exc:
        if logger:
            logger.log("ERROR", f"Could not inspect disk space: {exc}")
        return False
    if free_mb < required_mb:
        if logger:
            logger.log(
                "ERROR",
                "Insufficient disk space",
                free_mb=free_mb,
                required_mb=required_mb,
            )
        return False
    return True


def get_system_info() -> dict[str, Any]:
    """Best-effort host system summary without blocking on CPU sampling."""
    try:
        memory = psutil.virtual_memory()
        total_ram_mb = memory.total // (1024 * 1024)
        available_ram_mb = memory.available // (1024 * 1024)
        cpu_count = psutil.cpu_count(logical=True) or os.cpu_count() or 2
        cpu_usage = psutil.cpu_percent(interval=None)
        max_safe_ram_mb = min(
            int(total_ram_mb * MAX_RAM_PERCENTAGE / 100),
            (
                available_ram_mb - 512
                if available_ram_mb > 1024
                else available_ram_mb - 256
            ),
        )
        return {
            "total_ram_mb": total_ram_mb,
            "available_ram_mb": available_ram_mb,
            "max_safe_ram_mb": max(max_safe_ram_mb, 512),
            "cpu_count": cpu_count,
            "cpu_usage": cpu_usage,
            "platform": sys.platform,
        }
    except Exception:
        return {
            "total_ram_mb": 4096,
            "available_ram_mb": 2048,
            "max_safe_ram_mb": 3072,
            "cpu_count": 2,
            "cpu_usage": 0.0,
            "platform": "unknown",
        }


def get_server_dir(server_name: str) -> Path:
    return Path.home() / f"minecraft-{sanitize_input(server_name)}"


def get_screen_name(server_name: str) -> str:
    return f"mc_{sanitize_input(server_name)}"


def screen_session_exists(screen_name: str, logger=None) -> bool:
    result = run_command(
        ["screen", "-ls", screen_name],
        logger=logger,
        check=False,
        capture_output=True,
    )
    return bool(result and result.stdout and screen_name in result.stdout)


def build_screen_launch_command(
    screen_name: str,
    startup_command: list[str],
    pid_file: str | os.PathLike[str],
    startup_log: str | os.PathLike[str] | None = None,
) -> list[str]:
    shell = shutil.which("bash") or shutil.which("sh") or "sh"
    shell_script = (
        f"echo $$ > {shlex.quote(str(pid_file))}; "
        f"exec {shlex.join(startup_command)}"
    )
    return ["screen", "-dmS", screen_name, shell, "-c", shell_script]


def wait_for_pid_file(
    pid_file: str | os.PathLike[str], timeout_seconds: int = 10
) -> int | None:
    deadline = time.time() + timeout_seconds
    pid_path = Path(pid_file)
    while time.time() < deadline:
        pid = read_pid_file(pid_path)
        if pid and is_pid_running(pid):
            return pid
        time.sleep(0.25)
    return None


def read_pid_file(path: str | os.PathLike[str]) -> int | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        return int(file_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def read_text_file(path: str | os.PathLike[str]) -> str | None:
    file_path = Path(path)
    if not file_path.exists():
        return None
    try:
        return file_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def write_text_file(path: str | os.PathLike[str], value: str) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(value, encoding="utf-8")


def remove_file(path: str | os.PathLike[str]) -> None:
    Path(path).unlink(missing_ok=True)


def is_pid_running(pid: int | None, expected_names: list[str] | None = None) -> bool:
    if not pid:
        return False
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return False
        if expected_names:
            proc_name = proc.name().lower()
            if not any(name.lower() in proc_name for name in expected_names):
                return False
        return True
    except psutil.Error:
        return False


def _parse_java_version(output: str) -> str | None:
    for token in output.replace("\n", " ").split():
        if token.startswith('"') and token.endswith('"'):
            cleaned = token.strip('"')
            if not cleaned or not cleaned[0].isdigit():
                continue
            parts = cleaned.split(".")
            # Java 8 and earlier use 1.x.y versioning
            if parts[0] == "1" and len(parts) > 1:
                return parts[1]
            return parts[0]
    return None


def detect_java_version(java_binary: str, logger=None) -> str | None:
    result = run_command(
        [java_binary, "-version"],
        logger=logger,
        check=False,
        capture_output=True,
        timeout=10,
    )
    if not result:
        return None
    combined_output = "\n".join(filter(None, [result.stdout, result.stderr]))
    return _parse_java_version(combined_output)


def get_required_java(version: str | None) -> str:
    if not version:
        return "17"
    parts = version.split(".")
    first_part = parts[0].split("-")[0]
    if first_part.isdigit():
        major = int(first_part)
        if major >= 26:
            return "25"
        if major >= 21:
            return "21"
    if len(parts) > 1 and parts[0] == "1":
        minor = int(parts[1].split("-")[0])
        patch_str = parts[2].split("-")[0] if len(parts) > 2 else ""
        patch = int(patch_str) if patch_str.isdigit() else 0
        if minor > 20 or (minor == 20 and patch >= 5):
            return "21"
        if minor >= 17:
            return "17"
        return "8"
    return "17"


def get_java_path(
    mc_version: str | None, config: dict[str, Any], logger=None
) -> str | None:
    required_version = get_required_java(mc_version)

    def _collect_candidates() -> list[str]:
        result: list[str] = []
        custom_home = config.get("java_homes", {}).get(required_version)
        if custom_home:
            result.append(str(Path(custom_home) / "bin" / "java"))

        for name in (
            f"java-{required_version}",
            f"java{required_version}",
            f"jdk-{required_version}",
            f"jdk{required_version}",
        ):
            bin_path = shutil.which(name)
            if bin_path:
                result.append(bin_path)

        java_on_path = shutil.which("java")
        if java_on_path:
            result.append(java_on_path)

        for base_path in COMMON_JAVA_HOME_BASES:
            if not base_path:
                continue
            for candidate in (
                base_path / str(required_version) / "bin" / "java",
                base_path / f"openjdk-{required_version}" / "bin" / "java",
                base_path / f"java-{required_version}-openjdk" / "bin" / "java",
                base_path / f"jdk-{required_version}" / "bin" / "java",
                base_path / "bin" / "java",
            ):
                result.append(str(candidate))
        return result

    def _find_best_match(
        candidates: list[str],
    ) -> tuple[str | None, str | None, str | None, str | None]:
        checked: set[str] = set()
        mismatch_path: str | None = None
        mismatch_version: str | None = None
        compatible_path: str | None = None
        compatible_version: str | None = None

        for candidate in candidates:
            if not candidate or candidate in checked:
                continue
            checked.add(candidate)
            if shutil.which(candidate) is None and not Path(candidate).exists():
                continue
            actual_version = detect_java_version(candidate, logger=logger)
            if actual_version == required_version:
                return candidate, None, None, None

            # Allow newer Java versions to satisfy older requirements
            if (
                actual_version
                and actual_version.isdigit()
                and required_version.isdigit()
            ):
                if int(actual_version) >= int(required_version):
                    if not compatible_path or int(actual_version) < int(
                        compatible_version
                    ):
                        compatible_path = candidate
                        compatible_version = actual_version

            if actual_version:
                mismatch_path = candidate
                mismatch_version = actual_version

        return (
            compatible_path,
            compatible_version,
            mismatch_path,
            mismatch_version,
        )

    candidates = _collect_candidates()
    match, _match_ver, mismatch_path, mismatch_ver = _find_best_match(candidates)
    if match:
        return match

    if running_on_termux():
        if logger:
            logger.log(
                "INFO",
                f"Attempting to install openjdk-{required_version} via Termux pkg...",
            )
        run_command(
            ["pkg", "install", "-y", "tur-repo"],
            logger=logger,
            check=False,
            capture_output=True,
        )
        run_command(
            ["pkg", "install", "-y", f"openjdk-{required_version}"],
            logger=logger,
            check=False,
            capture_output=True,
        )
        candidates = _collect_candidates()
        match, _match_ver, mismatch_path, mismatch_ver = _find_best_match(candidates)
        if match:
            return match

    if logger:
        if mismatch_path:
            logger.log(
                "ERROR",
                (
                    f"Java {required_version} is required but {mismatch_ver} "
                    f"was found at {mismatch_path}"
                ),
            )
        else:
            logger.log(
                "ERROR",
                f"Java {required_version} is required but no matching runtime was found.",
            )
    return None


def detect_php_runtime(php_binary: str | Path, logger=None) -> dict[str, Any]:
    """Inspect a PHP binary for version, ZTS support, and PocketMine compatibility."""
    bin_path = str(php_binary)
    if not shutil.which(bin_path) and not Path(bin_path).exists():
        return {
            "exists": False,
            "version": None,
            "zts": False,
            "has_pmmpthread": False,
            "has_yaml": False,
            "compatible": False,
        }

    result = run_command(
        [bin_path, "-v"], logger=logger, check=False, capture_output=True
    )
    if not result or result.stdout is None:
        return {
            "exists": False,
            "version": None,
            "zts": False,
            "has_pmmpthread": False,
            "has_yaml": False,
            "compatible": False,
        }

    output = result.stdout
    is_zts = bool(
        re.search(r"\bZTS\b", output)
        or "( zts" in output.lower()
        or "thread safety => enabled" in output.lower()
    )

    version_str = None
    tokens = output.split()
    if len(tokens) > 1 and tokens[0].lower() == "php":
        version_str = tokens[1]

    modules_res = run_command(
        [bin_path, "-m"], logger=logger, check=False, capture_output=True
    )
    modules_set = set()
    if modules_res and modules_res.stdout:
        modules_set = {
            m.strip().lower() for m in modules_res.stdout.splitlines() if m.strip()
        }

    has_pmmpthread = bool({"pmmpthread", "pthreads"} & modules_set)
    has_yaml = "yaml" in modules_set

    # Compatible if ZTS or pmmpthread/pthreads module is present
    compatible = is_zts or has_pmmpthread
    return {
        "exists": True,
        "version": version_str,
        "zts": is_zts,
        "has_pmmpthread": has_pmmpthread,
        "has_yaml": has_yaml,
        "compatible": compatible,
    }


def get_php_path(
    config: dict[str, Any] | None = None,
    server_dir: str | Path | None = None,
    auto_install: bool = True,
    logger=None,
) -> str | None:
    """Resolve a compatible PHP binary for PocketMine-MP."""
    cfg = config or {}

    # 1. Custom configured php_path in config
    custom_php = cfg.get("php_path")
    if custom_php:
        custom_path = Path(custom_php)
        if custom_path.exists() or shutil.which(custom_php):
            return str(custom_path)

    # 2. Server directory local binaries
    if server_dir:
        s_dir = Path(server_dir)
        for rel in (
            Path("bin/php7/bin/php"),
            Path("bin/php/bin/php"),
            Path("bin/php"),
            Path("php"),
            Path("bin/php.exe"),
            Path("php.exe"),
        ):
            candidate = s_dir / rel
            if candidate.is_file():
                return str(candidate)

    # 3. Global MSM PHP directory (~/.config/msm/php) and COMMON_PHP_BASES
    for base in COMMON_PHP_BASES:
        if not base or not base.exists():
            continue
        for rel in (
            Path("bin/php7/bin/php"),
            Path("bin/php/bin/php"),
            Path("bin/php"),
            Path("php"),
            Path("bin/php.exe"),
            Path("php.exe"),
        ):
            candidate = base / rel
            if candidate.is_file():
                return str(candidate)

    # 4. System PHP on PATH (verify compatibility)
    system_php = shutil.which("php")
    if system_php:
        info = detect_php_runtime(system_php, logger=logger)
        if info["compatible"]:
            return system_php

    # 5. Auto-install from PMMP prebuilt binaries if enabled
    if auto_install:
        if logger:
            logger.log(
                "INFO",
                "PocketMine-compatible PHP binary not found. "
                "Attempting automated download from pmmp/PHP-Binaries...",
            )
        try:
            from utils.network import download_php_binary

            installed_php = download_php_binary(PHP_DIR, logger=logger)
            if installed_php and installed_php.exists():
                return str(installed_php)
        except Exception as exc:
            if logger:
                logger.log("WARNING", f"Auto-download of PHP binary failed: {exc}")

    # 6. Fallback to system PHP with warning if available
    if system_php:
        if logger:
            logger.log(
                "WARNING",
                "Using system PHP binary. If PocketMine fails to start, "
                "install the official PMMP PHP binary compiled with ZTS and pmmpthread.",
            )
        return system_php

    if logger:
        logger.log(
            "ERROR",
            "A compatible PocketMine PHP runtime (ZTS + pmmpthread) could not be located.",
        )
    return None


def running_on_termux() -> bool:
    prefix = os.environ.get("PREFIX", "")
    return "termux" in prefix.lower() or Path("/data/data/com.termux").exists()


def check_base_dependencies(logger) -> bool:
    missing = [name for name in ["screen"] if shutil.which(name) is None]
    if missing:
        logger.log("ERROR", f"Missing required tools: {', '.join(missing)}")
        if running_on_termux():
            logger.log(
                "INFO",
                "Install them with: pkg install screen openjdk-17 openjdk-21 php",
            )
        else:
            logger.log(
                "INFO",
                "Install them with: sudo apt-get install screen openjdk-17-jre-headless"
                " (Debian/Ubuntu) or the equivalent for your distro.",
            )
        return False
    if shutil.which("java") is None:
        logger.log(
            "WARNING",
            (
                "No Java runtime is currently on PATH. Java servers will need a "
                "configured java_homes entry."
            ),
        )
    if (
        shutil.which("php") is None
        and not (PHP_DIR / "bin/php7/bin/php").exists()
        and not (PHP_DIR / "bin/php/bin/php").exists()
        and not (PHP_DIR / "bin/php").exists()
    ):
        logger.log(
            "INFO",
            "No PHP binary found. PocketMine servers will download a "
            "compatible runtime automatically on demand.",
        )
    return True


def format_bytes(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024 or unit == "TB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size_bytes}B"


def get_local_ipv4_addresses() -> list[str]:
    addresses: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_socket:
            probe_socket.connect(("8.8.8.8", 80))
            address = probe_socket.getsockname()[0]
            if address and not address.startswith("127."):
                addresses.add(address)
    except OSError:
        pass

    try:
        for interface_addresses in psutil.net_if_addrs().values():
            for address_info in interface_addresses:
                if address_info.family != socket.AF_INET:
                    continue
                address = address_info.address
                if address and not address.startswith("127."):
                    addresses.add(address)
    except Exception:
        pass

    def sort_key(address: str) -> tuple[int, str]:
        parsed = ipaddress.ip_address(address)
        return (0 if parsed.is_private else 1, address)

    return sorted(addresses, key=sort_key)
