"""Interactive CLI for Minecraft Server Manager."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil

from core.config import ConfigManager
from core.constants import (
    DEFAULT_TUNNEL_BINARIES,
    EULA_FILE,
    LOG_RETENTION_DAYS,
    MAX_LOG_SIZE,
    SERVER_FLAVORS,
    SERVER_PROPERTIES_FILE,
    SUPPORTED_TUNNEL_PROTOCOLS,
    SUPPORTED_TUNNEL_PROVIDERS,
    VERSION,
    VERSIONS_PER_PAGE,
)
from core.runtime import RuntimeManager
from db.manager import DatabaseManager
from ui.colors import C, visible_len
from utils.logging_utils import EnhancedLogger
from utils.network import download_ngrok_binary, get_versions_for_flavor
from utils.ngrok import diagnose_ngrok, resolve_ngrok_binary
from utils.playit import (
    diagnose_playit,
    extract_playit_secret_from_file,
    resolve_playit_binary,
)
from utils.playit_api import (
    PlayitApiClient,
    PlayitApiError,
    load_playit_session,
)
from utils.properties import load_properties
from utils.system import (
    check_base_dependencies,
    detect_java_version,
    detect_php_runtime,
    format_bytes,
    get_java_path,
    get_required_java,
    get_server_dir,
    get_system_info,
    remove_file,
    run_command,
    running_on_termux,
    sanitize_input,
    write_text_file,
)

_FLASH_MESSAGE: tuple[str, str, str] | None = None


def set_flash(glyph: str, color: str, text: str) -> None:
    """Store a non-blocking flash message to display on the next render pass."""
    global _FLASH_MESSAGE
    _FLASH_MESSAGE = (glyph, color, text)


def render_flash() -> str:
    """Return formatted flash banner and clear stored message."""
    global _FLASH_MESSAGE
    if not _FLASH_MESSAGE:
        return ""
    glyph, color, text = _FLASH_MESSAGE
    _FLASH_MESSAGE = None
    return f" {color}{glyph} {text}{C.RESET}\n"


def prompt(label: str = "› ", default: str | None = None) -> str:
    """Prompt user with EOF and KeyboardInterrupt safety."""
    try:
        val = input(label).strip()
        return val if val else (default or "")
    except (EOFError, KeyboardInterrupt):
        return ""


def pause(prompt_text: str = "\nPress Enter to continue...") -> None:
    try:
        input(prompt_text)
    except (EOFError, KeyboardInterrupt):
        pass


def clear_screen() -> None:
    print("\033[H\033[2J", end="", flush=True)


def format_duration(seconds: float | int | None) -> str:
    if not seconds:
        return "N/A"
    remaining = int(seconds)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, _ = divmod(remaining, 60)
    return f"{days}d {hours}h {minutes}m"


def run_with_spinner(message: str, func, *args, **kwargs):
    state = {"done": False, "result": None, "error": None}

    def worker() -> None:
        try:
            state["result"] = func(*args, **kwargs)
        except Exception as exc:
            state["error"] = exc
        finally:
            state["done"] = True

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    spinner = ["◐", "◓", "◑", "◒"]
    index = 0
    while not state["done"]:
        print(
            f"\r {C.ACCENT}{spinner[index % len(spinner)]}{C.RESET} {message}",
            end="",
            flush=True,
        )
        time.sleep(0.08)
        index += 1
    print("\r" + (" " * (len(message) + 8)) + "\r", end="", flush=True)

    if state["error"]:
        raise state["error"]
    return state["result"]


def get_terminal_width(
    default: int = 48, min_width: int = 42, max_width: int = 64
) -> int:
    cols = shutil.get_terminal_size((default, 24)).columns
    return max(min_width, min(cols - 2, max_width))


def create_services():
    from platforms.paths import get_path_service

    path_service = get_path_service()
    path_service.ensure_directories()
    logger = EnhancedLogger(path_service.log_file, MAX_LOG_SIZE, LOG_RETENTION_DAYS)
    config_manager = ConfigManager(path_service.config_file, logger)
    db_manager = DatabaseManager(path_service.database_file)
    runtime = RuntimeManager(config_manager, db_manager, logger)
    return logger, config_manager, db_manager, runtime


def ensure_current_server(config_manager: ConfigManager) -> dict:
    config = config_manager.load()
    if not config.get("current_server") and config.get("servers"):
        config["current_server"] = next(iter(config["servers"]))
        config = config_manager.save(config)
    return config


def render_server_hud(
    current_server: str,
    runtime: RuntimeManager,
    config_manager: ConfigManager,
) -> None:
    width = get_terminal_width()
    config = config_manager.load()
    server_config = config.get("servers", {}).get(current_server, {})
    instance = runtime.get_instance(current_server)
    flavor = server_config.get("server_flavor") or "vanilla"
    flavor_name = SERVER_FLAVORS.get(flavor, {}).get("name", flavor)
    version = server_config.get("server_version") or "N/A"
    is_running = instance.is_running()
    pid = instance.current_pid() if is_running else None

    # Line 1: Header banner
    tag = f"MSM {C.BOX_H}{C.BOX_H} {current_server} [{flavor_name} {version}] "
    pad = max(2, width - visible_len(tag))
    print(f"{C.ACCENT}{tag}{C.DIM}{C.BOX_H * pad}{C.RESET}")

    # Line 2: Telemetry
    allocated_mb = int(server_config.get("ram_mb", 2048))
    allocated_g = allocated_mb / 1024.0
    if is_running and pid:
        cpu_str = "--%"
        ram_str = f"--/{allocated_g:.1f}G"
        try:
            proc = psutil.Process(pid)
            cpu_val = proc.cpu_percent(interval=None)
            if cpu_val is not None:
                cpu_str = f"{cpu_val:.0f}%"
            rss_bytes = proc.memory_info().rss
            rss_g = rss_bytes / (1024 * 1024 * 1024)
            ram_str = f"{rss_g:.1f}/{allocated_g:.1f}G"
        except (psutil.Error, OSError, PermissionError):
            pass
        status_dot = f"{C.OK}{C.DOT_ON} ONLINE{C.RESET}"
        pid_str = f"{C.DIM}(PID {pid}){C.RESET}"
        print(
            f"{status_dot} {pid_str} {C.DIM}{C.BOX_V}{C.RESET} "
            f"CPU {cpu_str} {C.DIM}{C.BOX_V}{C.RESET} RAM {ram_str}"
        )
    else:
        status_dot = f"{C.ERR}{C.DOT_OFF} OFFLINE{C.RESET}"
        print(
            f"{status_dot} {C.DIM}{C.BOX_V}{C.RESET} "
            f"CPU --% {C.DIM}{C.BOX_V}{C.RESET} RAM --/{allocated_g:.1f}G"
        )

    # Line 3: Bind & RCON
    port = instance.get_server_port()
    rcon_cfg = server_config.get("rcon", {})
    rcon_state = "enabled" if rcon_cfg.get("enabled") else "disabled"
    print(
        f"{C.DIM}{C.TREE_BRANCH}{C.RESET} Bind:   127.0.0.1:{port} "
        f"{C.DIM}·{C.RESET} RCON: {rcon_state}"
    )

    # Line 4: Tunnel
    conn = instance.get_connection_info()
    tunnel_prov = conn.get("tunnel_provider", "playit")
    tunnel_url = conn.get("tunnel_url")
    tunnel_enabled = conn.get("tunnel_enabled", False)
    if tunnel_url:
        tunnel_display = f"{tunnel_prov} -> {tunnel_url}"
    elif tunnel_enabled:
        tunnel_display = f"{tunnel_prov} ({conn.get('tunnel_status', 'active')})"
    else:
        tunnel_display = f"{tunnel_prov} (disabled)"

    max_tun_len = width - 12
    if len(tunnel_display) > max_tun_len and max_tun_len > 10:
        tunnel_display = tunnel_display[: max_tun_len - 3] + "..."
    print(f"{C.DIM}{C.TREE_CORNER}{C.RESET} Tunnel: {tunnel_display}")


def render_workspace_overview(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
) -> None:
    width = get_terminal_width()
    config = config_manager.load()
    servers = list(config.get("servers", {}))
    from platforms.detector import detect_platform
    from platforms.paths import get_path_service

    platform_desc = detect_platform()
    path_service = get_path_service()

    java_bin = shutil.which("java")
    java_ver = detect_java_version(java_bin) if java_bin else "None"
    php_bin = shutil.which("php")
    php_info = detect_php_runtime(php_bin) if php_bin else {}
    php_ver = php_info.get("version") or "None"

    tag = f"MSM {C.BOX_H}{C.BOX_H} Workspace Overview "
    pad = max(2, width - visible_len(tag))
    print(f"{C.ACCENT}{tag}{C.DIM}{C.BOX_H * pad}{C.RESET}")
    print(
        f"Host: {platform_desc.os_type.value} {platform_desc.architecture.value} "
        f"{C.DIM}{C.BOX_V}{C.RESET} Java: {java_ver} {C.DIM}{C.BOX_V}{C.RESET} PHP: {php_ver}"
    )
    print(f"Configured Servers ({len(servers)}):")
    for idx, s_name in enumerate(servers, start=1):
        s_cfg = config.get("servers", {}).get(s_name, {})
        s_flavor = s_cfg.get("server_flavor") or "N/A"
        s_ver = s_cfg.get("server_version") or "N/A"
        s_port = s_cfg.get("server_settings", {}).get("port", 25565)
        inst = runtime.get_instance(s_name)
        dot = (
            f"{C.OK}{C.DOT_ON}{C.RESET}"
            if inst.is_running()
            else f"{C.ERR}{C.DOT_OFF}{C.RESET}"
        )
        s_dir = path_service.resolve_server_dir(s_name, config)
        print(
            f" [{idx}] {dot} {C.BOLD}{s_name}{C.RESET}  {s_flavor} {s_ver}  :{s_port}"
        )
        print(f"     {C.DIM}Directory: {s_dir}{C.RESET}")


def print_header(current_server: str | None, runtime: RuntimeManager) -> None:
    clear_screen()
    width = get_terminal_width()
    divider = f"{C.DIM}{C.BOX_H * width}{C.RESET}"

    system_info = get_system_info()
    running_servers = runtime.running_servers()
    ram_usage = (
        f"{system_info['available_ram_mb']}MB / {system_info['total_ram_mb']}MB"
        if system_info["total_ram_mb"]
        else "unavailable"
    )
    cpu_usage = system_info["cpu_usage"]
    cpu_info = f"{system_info['cpu_count'] or 'unknown'} cores @ " + (
        f"{cpu_usage:.1f}%" if cpu_usage is not None else "unavailable"
    )

    print(
        f"{C.BOLD}{C.ACCENT}  MSM{C.RESET} "
        f"{C.DIM}· Minecraft Server Manager v{VERSION}{C.RESET}"
    )
    print(divider)
    print(f"  {C.DIM}RAM     :{C.RESET} {ram_usage}")
    print(f"  {C.DIM}CPU     :{C.RESET} {cpu_info}")
    print(f"  {C.DIM}OS      :{C.RESET} {system_info['platform']}")
    print(f"  {C.DIM}Running :{C.RESET} {len(running_servers)} server(s)")
    if current_server:
        print(f"  {C.DIM}Selected:{C.RESET} {C.BOLD}{current_server}{C.RESET}")
    print(divider + "\n")


def print_connection_summary(instance) -> None:
    info = instance.get_connection_info()
    lan_endpoints = info["lan_endpoints"]
    if lan_endpoints:
        lan_display = ", ".join(lan_endpoints[:2])
        if len(lan_endpoints) > 2:
            lan_display += f" (+{len(lan_endpoints) - 2} more)"
    else:
        lan_display = "Not detected"

    tunnel_display = info["tunnel_url"] or info["tunnel_status"]

    print(f"  {C.DIM}Localhost :{C.RESET} {info['loopback_endpoint']}")
    print(f"  {C.DIM}LAN/Wi-Fi :{C.RESET} {lan_display}")
    print(f"  {C.DIM}Tunnel    :{C.RESET} {tunnel_display}")
    if info.get("tunnel_setup_url"):
        print(f"  {C.DIM}Setup URL :{C.RESET} {info['tunnel_setup_url']}")


def resolve_tunnel_binary(binary_path: str) -> str | None:
    resolved = shutil.which(binary_path)
    if resolved:
        return resolved
    file_path = Path(binary_path).expanduser()
    if file_path.exists():
        return str(file_path)
    return None


def save_tunnel_config(
    instance,
    config_manager: ConfigManager,
    current_server: str,
    provider: str,
    binary_path: str,
    enabled: bool,
    logger,
    protocol: str | None = None,
    local_host: str | None = None,
    local_port: int | None = None,
) -> None:
    def updater(saved_config: dict) -> None:
        tunnel = saved_config["servers"][current_server].setdefault("tunnel", {})
        tunnel["provider"] = provider
        tunnel["binary_path"] = binary_path
        tunnel["enabled"] = enabled
        tunnel["autostart"] = enabled
        if protocol is not None:
            tunnel["protocol"] = protocol
        if local_host is not None:
            tunnel["local_host"] = local_host
        if local_port is not None:
            tunnel["local_port"] = local_port

    config_manager.mutate(updater)
    if enabled:
        instance.restart_tunnel()
    else:
        instance.stop_tunnel()
    logger.log(
        "SUCCESS",
        f"Tunnel settings saved for {current_server}.",
        provider=provider,
        enabled=enabled,
    )


def configure_playit_api_tunnel(
    instance, config_manager, current_server, logger
) -> bool:
    config = config_manager.load()
    server_config = config["servers"][current_server]
    tunnel = server_config.get("tunnel", {})
    secret = extract_playit_secret_from_file(instance.playit_secret_file)
    if not secret:
        logger.log("WARNING", "Link a Playit agent before creating its tunnel.")
        return False
    protocol = (
        "udp"
        if server_config.get("server_flavor") == "pocketmine"
        else tunnel.get("protocol", "tcp")
    )
    local_host = tunnel.get("local_host", "127.0.0.1")
    local_port = tunnel.get("local_port") or instance.get_server_port()
    try:
        with PlayitApiClient(agent_secret=secret, timeout=5) as client:
            agent_id = client.agent_rundata().get("agent_id")
            if not agent_id:
                raise PlayitApiError("Playit did not return an agent id.")
            request = dict(
                server_name=current_server,
                agent_id=agent_id,
                flavor=server_config.get("server_flavor"),
                protocol=protocol,
                local_host=local_host,
                local_port=local_port,
                existing_tunnel_id=tunnel.get("playit_tunnel_id"),
            )
            for attempt in range(5):
                try:
                    tunnel_id, endpoint = client.create_or_update_tunnel(**request)
                    break
                except PlayitApiError as exc:
                    if "AgentVersionTooOld" in str(exc):
                        if attempt == 4:
                            raise PlayitApiError(
                                "AgentVersionTooOld after waiting for daemon registration. "
                                "Check that the Playit daemon is connected in diagnostics. "
                                "If connected, update Playit (Termux: pkg update && "
                                "pkg upgrade playit), then retry setup and keep the "
                                "existing agent; re-linking is not required."
                            ) from exc
                        if attempt == 0:
                            logger.log(
                                "INFO", "Waiting for Playit daemon registration..."
                            )
                        time.sleep(2)
                        continue
                    session_key = load_playit_session()
                    if not session_key:
                        raise
                    with PlayitApiClient(session_key=session_key, timeout=5) as account:
                        tunnel_id, endpoint = account.create_or_update_tunnel(**request)
                    break
    except (PlayitApiError, ValueError) as exc:
        logger.log("ERROR", f"Playit mapping could not be configured: {exc}")
        return False

    def updater(saved):
        target = saved["servers"][current_server]["tunnel"]
        target.update(
            provider="playit",
            protocol=protocol,
            local_host=local_host,
            local_port=int(local_port),
            playit_tunnel_id=tunnel_id,
            last_endpoint=endpoint,
        )

    config_manager.mutate(updater)
    if endpoint:
        write_text_file(instance.playit_endpoint_file, endpoint)
    else:
        remove_file(instance.playit_endpoint_file)
    logger.log(
        "SUCCESS", f"Playit mapping saved: {endpoint or 'waiting for allocation'}"
    )
    return True


def tunnel_diagnostics_screen(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    current_server: str,
    logger,
    provider: str | None = None,
) -> None:
    instance = runtime.get_instance(current_server)
    config = config_manager.load()
    server_config = config["servers"][current_server]
    tunnel_config = server_config.get("tunnel", {})
    selected = provider or tunnel_config.get("provider", "playit")
    server_port = int(server_config.get("server_settings", {}).get("port", 25565))
    flavor = server_config.get("server_flavor")

    print_header(current_server, runtime)
    print(f"{C.BOLD}Tunnel Diagnostics ({selected}){C.RESET}")
    print_connection_summary(instance)
    print()

    if selected == "playit":
        checks = diagnose_playit(
            instance.server_dir, tunnel_config, server_port, flavor
        )
    elif selected == "ngrok":
        checks = diagnose_ngrok(
            instance.server_dir, tunnel_config, server_port, flavor, logger
        )
    else:
        logger.log("ERROR", f"Unknown provider: {selected}")
        pause()
        return

    for check in checks:
        symbol = f"{C.OK}[OK]{C.RESET}" if check.ok else f"{C.ERR}[ERR]{C.RESET}"
        print(f"  {symbol} {check.name}: {check.detail}")
    pause()


def ngrok_setup_wizard(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> None:
    instance = runtime.get_instance(current_server)
    config = config_manager.load()
    tunnel = config["servers"][current_server].setdefault("tunnel", {})

    stored_provider = tunnel.get("provider")
    stored_binary = str(tunnel.get("binary_path") or "")
    if (
        stored_provider == "ngrok"
        and stored_binary
        and "playit" not in Path(stored_binary).name.lower()
    ):
        candidate = stored_binary
    else:
        candidate = "ngrok"

    resolved_binary = resolve_ngrok_binary(candidate) or resolve_ngrok_binary("ngrok")

    print_header(current_server, runtime)
    print(f" {C.BOLD}Ngrok Setup Wizard:{C.RESET} {current_server}\n")
    print_connection_summary(instance)
    print()

    binary_path = resolved_binary or "ngrok"
    if not resolved_binary:
        logger.log(
            "WARNING",
            "Ngrok binary was not found on this device.",
        )
        prompt_txt = "Download and install ngrok automatically? (Y/n): "
        if prompt(prompt_txt, default="y").lower() != "n":
            logger.log("INFO", "Downloading ngrok...")
            downloaded_path = download_ngrok_binary(logger=logger)
            if downloaded_path:
                logger.log("SUCCESS", f"Ngrok installed to {downloaded_path}")
                resolved_binary = str(downloaded_path)
                binary_path = str(downloaded_path)
            else:
                logger.log("ERROR", "Auto-installation failed.")
        else:
            custom_input = prompt("Enter custom ngrok binary path: ")
            if custom_input:
                resolved_binary = resolve_ngrok_binary(custom_input)
                binary_path = resolved_binary or custom_input

    from getpass import getpass

    try:
        authtoken = getpass(
            "\nNgrok authtoken (press Enter to keep existing): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        authtoken = ""

    if authtoken and resolved_binary:
        result = run_command(
            [resolved_binary, "config", "add-authtoken", authtoken],
            logger=None,
            check=False,
            capture_output=True,
        )
        if result and result.returncode == 0:
            logger.log("SUCCESS", "Stored ngrok authtoken successfully.")
        else:
            stderr = ""
            if result:
                stderr = (result.stderr or result.stdout or "").strip()
            logger.log("ERROR", f"Failed to store ngrok authtoken. {stderr}".strip())

    save_tunnel_config(
        instance,
        config_manager,
        current_server,
        provider="ngrok",
        binary_path=str(binary_path),
        enabled=True,
        logger=logger,
    )
    logger.log("SUCCESS", f"Ngrok tunnel enabled for {current_server}.")
    pause()


def playit_setup_wizard(runtime, config_manager, current_server, logger) -> None:
    import secrets
    from utils.playit import save_playit_secret

    instance = runtime.get_instance(current_server)
    instance.ensure_server_files()
    config = config_manager.load()
    tunnel = config["servers"][current_server].get("tunnel", {})
    candidate = (
        tunnel.get("binary_path") if tunnel.get("provider") == "playit" else None
    )
    resolved = resolve_playit_binary(candidate)
    if not resolved and running_on_termux():
        if (
            prompt("Install Playit with Termux pkg? (Y/n): ", default="y").lower()
            != "n"
        ):
            for command in (
                ["pkg", "install", "-y", "tur-repo"],
                ["pkg", "install", "-y", "playit"],
            ):
                outcome = run_command(
                    command, logger=logger, check=False, capture_output=True
                )
                if not outcome or outcome.returncode:
                    logger.log("ERROR", "Playit package installation failed.")
                    pause()
                    return
            resolved = resolve_playit_binary()
    if not resolved:
        custom = prompt("Playit daemon executable path: ")
        resolved = resolve_playit_binary(custom) if custom else None
    if not resolved:
        logger.log(
            "ERROR",
            "A Playit daemon is required. Install playitd or a standalone agent.",
        )
        pause()
        return

    secret = extract_playit_secret_from_file(instance.playit_secret_file)
    if secret:
        try:
            with PlayitApiClient(agent_secret=secret, timeout=5) as client:
                client.agent_rundata()
        except PlayitApiError as exc:
            logger.log(
                "WARNING", f"Saved Playit credentials are no longer usable: {exc}"
            )
            secret = None
        else:
            if (
                prompt(
                    "Link a fresh agent instead of existing? (y/N): ", default="n"
                ).lower()
                == "y"
            ):
                secret = None
    if not secret:
        code = secrets.token_hex(5)
        try:
            with PlayitApiClient(timeout=5) as client:
                print(
                    f"Open https://playit.gg/claim/{code} and approve this agent.",
                    flush=True,
                )
                print(
                    "Waiting for browser approval (up to 3 minutes). "
                    "Keep MSM running; Ctrl+C cancels.",
                    flush=True,
                )

                def show_claim_status(status):
                    if status == "WaitingForUser":
                        logger.log(
                            "INFO",
                            "Browser detected; approve the agent on the Playit page.",
                        )
                    elif status == "UserAccepted":
                        logger.log("INFO", "Agent approved; finishing linking.")

                secret = client.wait_for_claim(code, on_status=show_claim_status)
            instance.stop_tunnel()
            save_playit_secret(instance.playit_secret_file, secret)
            remove_file(instance.playit_endpoint_file)

            def reset(saved):
                saved["servers"][current_server]["tunnel"]["playit_tunnel_id"] = None

            config_manager.mutate(reset)
        except KeyboardInterrupt:
            logger.log(
                "INFO", "Playit linking cancelled; existing credentials were kept."
            )
            return
        except PlayitApiError as exc:
            logger.log("ERROR", f"Playit linking failed: {exc}")
            pause()
            return

    save_tunnel_config(
        instance,
        config_manager,
        current_server,
        provider="playit",
        binary_path=resolved,
        enabled=True,
        logger=logger,
        protocol=(
            "udp"
            if config["servers"][current_server].get("server_flavor") == "pocketmine"
            else tunnel.get("protocol", "tcp")
        ),
    )
    if instance.tunnel_process is None or instance.tunnel_process.poll() is not None:
        logger.log(
            "ERROR",
            "Agent linked, but the Playit daemon did not stay running. "
            "Check tunnel diagnostics, then retry setup using the existing agent.",
        )
        pause()
        return
    if not configure_playit_api_tunnel(
        instance, config_manager, current_server, logger
    ):
        logger.log(
            "WARNING",
            "Agent credentials saved; mapping setup is incomplete. "
            "Check diagnostics or retry setup using the existing agent.",
        )
    pause()


def configure_tunnel_advanced(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> None:
    while True:
        config = config_manager.load()
        server_config = config["servers"][current_server]
        tunnel = server_config.setdefault("tunnel", {})

        print_header(current_server, runtime)
        print(f" {C.BOLD}Advanced Tunnel Configuration:{C.RESET} {current_server}\n")
        print(f" [ 1] Provider    : {tunnel.get('provider', 'playit')}")
        print(f" [ 2] Protocol    : {tunnel.get('protocol', 'tcp')}")
        print(f" [ 3] Local Host  : {tunnel.get('local_host', '127.0.0.1')}")
        print(f" [ 4] Local Port  : {tunnel.get('local_port') or 'auto'}")
        print(f" [ 5] Binary Path : {tunnel.get('binary_path', 'default')}")
        print(" [ 0] Back")

        choice = prompt(f"\n{C.BOLD}Choose setting [0-5]: {C.RESET}")
        if choice == "0":
            return
        if choice == "1":
            print(f"\nSupported Providers: {', '.join(SUPPORTED_TUNNEL_PROVIDERS)}")
            val = prompt(
                f"Provider [{tunnel.get('provider', 'playit')}]: ",
                default=tunnel.get("provider", "playit"),
            ).lower()
            if val in SUPPORTED_TUNNEL_PROVIDERS:

                def update_prov(s: dict) -> None:
                    s["servers"][current_server]["tunnel"]["provider"] = val

                config_manager.mutate(update_prov)
                logger.log("SUCCESS", f"Tunnel provider set to {val}.")
            else:
                logger.log("ERROR", "Unsupported provider.")
            pause()
        elif choice == "2":
            val = prompt("Protocol (tcp/udp) [tcp]: ", default="tcp").lower()
            if val in SUPPORTED_TUNNEL_PROTOCOLS:

                def update_prot(s: dict) -> None:
                    s["servers"][current_server]["tunnel"]["protocol"] = val

                config_manager.mutate(update_prot)
                logger.log("SUCCESS", f"Protocol set to {val}.")
            else:
                logger.log("ERROR", "Unsupported protocol.")
            pause()
        elif choice == "3":
            val = prompt("Local host [127.0.0.1]: ", default="127.0.0.1")

            def update_host(s: dict) -> None:
                s["servers"][current_server]["tunnel"]["local_host"] = val

            config_manager.mutate(update_host)
            logger.log("SUCCESS", f"Local host set to {val}.")
            pause()
        elif choice == "4":
            raw = prompt("Local port (number or 'auto') [auto]: ")
            val = int(raw) if raw.isdigit() else None

            def update_port(s: dict) -> None:
                s["servers"][current_server]["tunnel"]["local_port"] = val

            config_manager.mutate(update_port)
            logger.log("SUCCESS", "Local port updated.")
            pause()
        elif choice == "5":
            val = prompt("Binary path (leave empty for default): ")
            if val:

                def update_bin(s: dict) -> None:
                    s["servers"][current_server]["tunnel"]["binary_path"] = val

                config_manager.mutate(update_bin)
                logger.log("SUCCESS", "Binary path updated.")
            pause()


def tunnel_manager_menu(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> None:
    instance = runtime.get_instance(current_server)

    while True:
        clear_screen()
        config = config_manager.load()
        server_config = config["servers"][current_server]
        tunnel = server_config.setdefault("tunnel", {})
        provider = tunnel.get("provider", "playit")
        enabled = tunnel.get("enabled", False)
        status_text = (
            f"{C.OK}{C.DOT_ON} Enabled ({provider}){C.RESET}"
            if enabled
            else f"{C.DIM}{C.DOT_OFF} Disabled{C.RESET}"
        )

        width = get_terminal_width()
        tag = f"MSM {C.BOX_H}{C.BOX_H} Tunnels: {current_server} "
        pad = max(2, width - visible_len(tag))
        print(f"{C.ACCENT}{tag}{C.DIM}{C.BOX_H * pad}{C.RESET}")
        print(f" Status   : {status_text}")
        print_connection_summary(instance)
        print(f"{C.DIM}{C.BOX_H * width}{C.RESET}")

        flash = render_flash()
        if flash:
            print(flash, end="")

        print(" [1] Toggle Tunnel (Start/Stop)")
        print(" [2] Setup Playit.gg (Recommended - Free & Fast)")
        print(" [3] Setup Ngrok")
        print(" [4] Test & Diagnose Tunnel Health")
        print(" [5] Advanced Tunnel Settings")
        print(" [6] Reset / Re-link Agent (Delete Stored Secrets)")
        print(" [0] Back")

        choice = prompt(f"\n{C.ACCENT}{C.POINTER}{C.RESET} ")
        if choice in ("0", "b", "q", "back", "exit"):
            return
        if choice == "1":
            binary_path = tunnel.get(
                "binary_path",
                DEFAULT_TUNNEL_BINARIES.get(provider, "playit"),
            )
            save_tunnel_config(
                instance,
                config_manager,
                current_server,
                provider=provider,
                binary_path=binary_path,
                enabled=not enabled,
                logger=logger,
            )
            action_text = "Enabled" if not enabled else "Disabled"
            set_flash("[OK]", C.OK, f"Tunnel {action_text}.")
            continue
        if choice == "2":
            playit_setup_wizard(runtime, config_manager, current_server, logger)
            continue
        if choice == "3":
            ngrok_setup_wizard(runtime, config_manager, current_server, logger)
            continue
        if choice == "4":
            tunnel_diagnostics_screen(
                runtime, config_manager, current_server, logger, provider=provider
            )
            continue
        if choice == "5":
            configure_tunnel_advanced(runtime, config_manager, current_server, logger)
            continue
        if choice == "6":
            confirm = prompt(
                "\nReset tunnel agent and delete saved credentials? (y/N): ",
                default="n",
            ).lower()
            if confirm == "y":
                instance.stop_tunnel()
                remove_file(instance.playit_secret_file)
                remove_file(instance.playit_endpoint_file)
                remove_file(instance.server_dir / ".msm.ngrok.endpoint")
                remove_file(instance.server_dir / ".msm.playit.log")
                remove_file(instance.server_dir / ".msm.ngrok.log")
                set_flash("[OK]", C.OK, "Tunnel credentials cleared.")
            continue


def tunnel_setup_wizard(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> None:
    tunnel_manager_menu(runtime, config_manager, current_server, logger)


def quick_property_editor(
    instance,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> None:
    """Inline quick property editor for the top 8 server.properties keys."""
    properties_path = instance.server_dir / SERVER_PROPERTIES_FILE
    difficulty_levels = ["peaceful", "easy", "normal", "hard"]
    gamemodes = ["survival", "creative", "adventure", "spectator"]

    while True:
        clear_screen()
        width = get_terminal_width()
        tag = f"MSM {C.BOX_H}{C.BOX_H} Properties: {current_server} "
        pad = max(2, width - visible_len(tag))
        print(f"{C.ACCENT}{tag}{C.DIM}{C.BOX_H * pad}{C.RESET}")

        flash = render_flash()
        if flash:
            print(flash, end="")

        props = load_properties(properties_path)

        online_mode = props.get("online-mode", "true")
        pvp = props.get("pvp", "true")
        difficulty = props.get("difficulty", "normal")
        gamemode = props.get("gamemode", "survival")
        whitelist = props.get("white-list", "false")
        max_players = props.get("max-players", "20")
        view_distance = props.get("view-distance", "10")
        motd = props.get("motd", "A Minecraft Server")

        print(f" [1] online-mode   : {online_mode}")
        print(f" [2] pvp           : {pvp}")
        print(f" [3] difficulty    : {difficulty}")
        print(f" [4] gamemode      : {gamemode}")
        print(f" [5] white-list    : {whitelist}")
        print(f" [6] max-players   : {max_players}")
        print(f" [7] view-distance : {view_distance}")
        print(f" [8] motd          : {motd}")
        print(f"{C.DIM}{C.BOX_H * width}{C.RESET}")
        print(" [1-8] Toggle/Edit   [c] Custom key=val   [0] Back")

        choice = prompt(f"\n{C.ACCENT}{C.POINTER}{C.RESET} ").lower()
        if choice in ("0", "b", "q", "back", "exit"):
            return
        elif choice == "1":
            new_val = "false" if online_mode.lower() == "true" else "true"
            props["online-mode"] = new_val
            instance.save_server_properties(props)
            set_flash("[OK]", C.OK, f"online-mode set to {new_val}")
        elif choice == "2":
            new_val = "false" if pvp.lower() == "true" else "true"
            props["pvp"] = new_val
            instance.save_server_properties(props)
            set_flash("[OK]", C.OK, f"pvp set to {new_val}")
        elif choice == "3":
            diff_cur = difficulty.lower()
            idx = (
                difficulty_levels.index(diff_cur)
                if diff_cur in difficulty_levels
                else 0
            )
            new_val = difficulty_levels[(idx + 1) % len(difficulty_levels)]
            props["difficulty"] = new_val
            instance.save_server_properties(props)
            set_flash("[OK]", C.OK, f"difficulty set to {new_val}")
        elif choice == "4":
            gm_cur = gamemode.lower()
            idx = gamemodes.index(gm_cur) if gm_cur in gamemodes else 0
            new_val = gamemodes[(idx + 1) % len(gamemodes)]
            props["gamemode"] = new_val
            instance.save_server_properties(props)
            set_flash("[OK]", C.OK, f"gamemode set to {new_val}")
        elif choice == "5":
            new_val = "false" if whitelist.lower() == "true" else "true"
            props["white-list"] = new_val
            instance.save_server_properties(props)
            set_flash("[OK]", C.OK, f"white-list set to {new_val}")
        elif choice == "6":
            raw = prompt(f"max-players [{max_players}]: ")
            if raw.isdigit():
                props["max-players"] = raw
                instance.save_server_properties(props)
                set_flash("[OK]", C.OK, f"max-players set to {raw}")
        elif choice == "7":
            raw = prompt(f"view-distance [{view_distance}]: ")
            if raw.isdigit():
                props["view-distance"] = raw
                instance.save_server_properties(props)
                set_flash("[OK]", C.OK, f"view-distance set to {raw}")
        elif choice == "8":
            raw = prompt(f"motd [{motd}]: ")
            if raw:
                props["motd"] = raw
                instance.save_server_properties(props)
                set_flash("[OK]", C.OK, "motd updated")
        elif choice == "c":
            pair = prompt("Enter key=value: ")
            if "=" in pair:
                k, v = pair.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    props[k] = v
                    instance.save_server_properties(props)
                    set_flash("[OK]", C.OK, f"{k} set to {v}")


def server_creation_wizard(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    logger,
) -> str | None:
    """Step-by-step inline server creation wizard with bracketed smart defaults."""
    width = get_terminal_width()
    tag = f"MSM {C.BOX_H}{C.BOX_H} Create New Server "
    pad = max(2, width - visible_len(tag))
    print(f"\n{C.ACCENT}{tag}{C.DIM}{C.BOX_H * pad}{C.RESET}")

    cfg_data = config_manager.load()
    default_name = f"server-{len(cfg_data.get('servers', {})) + 1}"
    raw_name = prompt(f"Server name [{default_name}]: ", default=default_name)
    sanitized = sanitize_input(raw_name) if raw_name else default_name
    if not sanitized:
        logger.log("ERROR", "Invalid server name.")
        pause()
        return None

    if sanitized in cfg_data.get("servers", {}):
        logger.log("ERROR", f"Server '{sanitized}' already exists.")
        pause()
        return None

    flavors = list(SERVER_FLAVORS.keys())
    print("\nSupported server flavors:")
    for idx, flav in enumerate(flavors, start=1):
        print(f" [{idx}] {flav} - {SERVER_FLAVORS[flav]['name']}")
    flav_choice = prompt("Choose flavor [1: paper]: ", default="1")
    f_idx = (
        int(flav_choice) - 1
        if flav_choice.isdigit() and 1 <= int(flav_choice) <= len(flavors)
        else 0
    )
    flavor = flavors[f_idx]

    # Version resolution
    versions_data = get_versions_for_flavor(flavor, logger=logger)
    release_versions = [
        v for v, info in versions_data.items() if not info.get("is_snapshot")
    ]
    default_version = (
        release_versions[0]
        if release_versions
        else (next(iter(versions_data)) if versions_data else "1.21.1")
    )
    version = prompt(f"Version [{default_version}]: ", default=default_version)
    version_info = versions_data.get(version, {})

    # RAM allocation
    raw_ram = prompt("RAM allocation in MB [2048]: ", default="2048")
    ram_mb = int(raw_ram) if raw_ram.isdigit() else 2048

    # Port binding
    default_port = SERVER_FLAVORS[flavor].get("default_port", 25565)
    raw_port = prompt(f"Server port [{default_port}]: ", default=str(default_port))
    port = int(raw_port) if raw_port.isdigit() else default_port

    # Pre-flight runtime check
    if flavor == "pocketmine":
        php_bin = shutil.which("php")
        php_info = detect_php_runtime(php_bin) if php_bin else {}
        if not php_info.get("compatible"):
            print(f"{C.WARN}[WARN] PocketMine requires PHP with ZTS support.{C.RESET}")
    else:
        req_java = get_required_java(version)
        java_path = get_java_path(req_java)
        if not java_path:
            print(
                f"{C.WARN}[WARN] Java {req_java} required for Minecraft {version} "
                f"was not detected.{C.RESET}"
            )

    config_manager.ensure_server(sanitized)
    instance = runtime.get_instance(sanitized)
    instance.server_dir.mkdir(parents=True, exist_ok=True)

    def updater(s_cfg: dict) -> None:
        target = s_cfg["servers"][sanitized]
        target["server_flavor"] = flavor
        target["server_version"] = version
        target["ram_mb"] = ram_mb
        target["server_settings"]["port"] = port

    config_manager.mutate(updater)

    try:
        run_with_spinner(
            f"Installing {flavor} {version}",
            instance.install_binary,
            flavor,
            version,
            version_info,
        )
        instance.apply_server_files()
        set_flash("[OK]", C.OK, f"Server '{sanitized}' created and installed.")
        return sanitized
    except Exception as exc:
        logger.log("ERROR", f"Failed to download server: {exc}")
        pause()
        return sanitized


def create_new_server(config_manager: ConfigManager, logger) -> None:
    raw_name = prompt(f"{C.BOLD}Enter a new server name: {C.RESET}")
    if not raw_name:
        logger.log("ERROR", "Server name cannot be empty.")
        return
    sanitized_name = sanitize_input(raw_name)
    config = config_manager.load()
    if sanitized_name in config.get("servers", {}):
        logger.log("ERROR", f"Server '{sanitized_name}' already exists.")
        return
    config_manager.ensure_server(sanitized_name)
    get_server_dir(sanitized_name).mkdir(parents=True, exist_ok=True)
    logger.log("SUCCESS", f"Created server '{sanitized_name}'.")


def select_current_server(
    config_manager: ConfigManager,
    logger,
    runtime: RuntimeManager | None = None,
) -> str | None:
    from platforms.paths import get_path_service

    path_service = get_path_service()
    config = config_manager.load()
    servers = list(config.get("servers", {}))
    if not servers:
        logger.log("ERROR", "No servers are configured.")
        return None

    print(f"\n {C.BOLD}Configured Servers ({len(servers)}):{C.RESET}\n")
    for index, server_name in enumerate(servers, start=1):
        s_cfg = config.get("servers", {}).get(server_name, {})
        flavor = s_cfg.get("server_flavor")
        flavor_name = SERVER_FLAVORS.get(flavor, {}).get("name", "Not installed")
        version = s_cfg.get("server_version") or "N/A"
        server_dir = path_service.resolve_server_dir(server_name, config)

        status_text = ""
        if runtime:
            inst = runtime.get_instance(server_name)
            status_text = (
                f"  [{C.OK}{C.DOT_ON} ONLINE{C.RESET}]"
                if inst.is_running()
                else f"  [{C.ERR}{C.DOT_OFF} OFFLINE{C.RESET}]"
            )

        print(f" [{index:2d}] {C.BOLD}{server_name}{C.RESET}{status_text}")
        print(f"      {C.DIM}Type     :{C.RESET} {flavor_name} {version}")
        print(f"      {C.DIM}Directory:{C.RESET} {server_dir}")
        print()

    choice = prompt(
        f"{C.BOLD}Choose server [1-{len(servers)}] (or Enter to cancel): {C.RESET}"
    )
    if not choice:
        return None
    if not choice.isdigit():
        logger.log("ERROR", "Selection must be a number.")
        return None
    selection = int(choice) - 1
    if selection < 0 or selection >= len(servers):
        logger.log("ERROR", "Invalid server selection.")
        return None

    chosen = servers[selection]
    config["current_server"] = chosen
    config_manager.save(config)
    logger.log("SUCCESS", f"Switched to server '{chosen}'.")
    return chosen


def select_server_flavor() -> str | None:
    flavors = list(SERVER_FLAVORS)
    print(f"{C.BOLD}Server flavors:{C.RESET}")
    for index, flavor in enumerate(flavors, start=1):
        details = SERVER_FLAVORS[flavor]
        print(f" {index}. {details['name']} - {details['description']}")
    choice = prompt(f"\n{C.BOLD}Choose flavor: {C.RESET}")
    if not choice.isdigit():
        return None
    selection = int(choice) - 1
    if selection < 0 or selection >= len(flavors):
        return None
    return flavors[selection]


def select_server_version(flavor: str, logger) -> tuple[str | None, dict | None]:
    include_snapshots = False
    page = 0
    while True:
        versions_data = get_versions_for_flavor(
            flavor,
            include_snapshots=include_snapshots,
            logger=logger,
        )
        versions = list(versions_data.keys())
        if not versions:
            logger.log("ERROR", f"No versions were returned for {flavor}.")
            return None, None
        total_pages = max(1, ((len(versions) - 1) // VERSIONS_PER_PAGE) + 1)
        page = min(page, total_pages - 1)
        start = page * VERSIONS_PER_PAGE
        end = start + VERSIONS_PER_PAGE
        page_versions = versions[start:end]
        print(f"\n{C.BOLD}{SERVER_FLAVORS[flavor]['name']} versions{C.RESET}")
        print(
            f"{C.DIM}Snapshots: {'on' if include_snapshots else 'off'} "
            f"| Page {page + 1}/{total_pages}{C.RESET}"
        )
        for index, version in enumerate(page_versions, start=1):
            marker = " [snapshot]" if versions_data[version].get("is_snapshot") else ""
            print(f" {index}. {version}{marker}")
        print(
            "\n n = next page | p = previous page | s = toggle snapshots | 0 = cancel"
        )
        choice = prompt(f"{C.BOLD}Choose version: {C.RESET}").lower()
        if choice == "0":
            return None, None
        if choice == "n":
            if page < total_pages - 1:
                page += 1
            continue
        if choice == "p":
            if page > 0:
                page -= 1
            continue
        if choice == "s":
            include_snapshots = not include_snapshots
            page = 0
            continue
        if not choice.isdigit():
            logger.log("ERROR", "Invalid version selection.")
            continue
        selection = int(choice) - 1
        if selection < 0 or selection >= len(page_versions):
            logger.log("ERROR", "Invalid version selection.")
            continue
        version = page_versions[selection]
        return version, versions_data[version]


def install_server(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        logger.log("ERROR", "No server is selected.")
        return
    flavor = select_server_flavor()
    if not flavor:
        logger.log("ERROR", "Invalid flavor selection.")
        return
    version, version_info = select_server_version(flavor, logger)
    if not version or not version_info:
        return
    instance = runtime.get_instance(current_server)
    artifact = run_with_spinner(
        f"Downloading {SERVER_FLAVORS[flavor]['name']} {version}",
        instance.install_binary,
        flavor,
        version,
        version_info,
    )

    def updater(saved_config: dict) -> None:
        server_config = saved_config["servers"][current_server]
        server_config["server_flavor"] = flavor
        server_config["server_version"] = version
        server_config["server_settings"]["port"] = SERVER_FLAVORS[flavor][
            "default_port"
        ]

    config_manager.mutate(updater)
    instance.apply_server_files()
    logger.log("SUCCESS", f"Installed {artifact.name} for '{current_server}'.")


def configure_server(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        logger.log("ERROR", "No server is selected.")
        return
    instance = runtime.get_instance(current_server)

    while True:
        updater = None
        config = config_manager.load()
        server_config = config["servers"][current_server]
        settings = server_config.get("server_settings", {})
        rcon = server_config.get("rcon", {})

        eula_path = instance.server_dir / EULA_FILE
        eula_val = load_properties(eula_path).get("eula", "false").lower() == "true"
        eula_display = (
            f"{C.OK}Accepted{C.RESET}" if eula_val else f"{C.ERR}Not Accepted{C.RESET}"
        )
        online_val = str(settings.get("online-mode", "true")).lower() == "true"
        online_display = (
            f"{C.OK}True{C.RESET} (Online)"
            if online_val
            else f"{C.WARN}False{C.RESET} (Offline/Cracked)"
        )
        rcon_val = rcon.get("enabled", False)
        rcon_display = (
            f"{C.OK}Enabled{C.RESET}" if rcon_val else f"{C.DIM}Disabled{C.RESET}"
        )

        print_header(current_server, runtime)
        print(f" {C.BOLD}Server Settings & Properties:{C.RESET} {current_server}\n")
        print(f" [ 1] RAM Allocation      : {server_config.get('ram_mb', 2048)} MB")
        print(f" [ 2] Server Port         : {settings.get('port', 25565)}")
        print(f" [ 3] Server MOTD         : {settings.get('motd', 'Minecraft Server')}")
        print(f" [ 4] Max Players         : {settings.get('max-players', 20)}")
        print(f" [ 5] Online Mode         : {online_display}")
        print(f" [ 6] EULA Agreement      : {eula_display}")
        print(f" [ 7] Auto-Restart        : {server_config.get('auto_restart', False)}")
        print(f" [ 8] RCON Remote CLI     : {rcon_display}")
        print(
            f" [ 9] RCON Password       : {'********' if rcon.get('password') else '(Not Set)'}"
        )
        print(" [10] Edit Raw Config Files (server.properties / eula.txt)")
        print(" [ 0] Back")

        choice = prompt(f"\n{C.BOLD}Choose setting [0-10]: {C.RESET}")
        if choice == "0":
            return
        try:
            if choice == "1":
                value = int(prompt("RAM in MB: "))

                def update_ram(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["ram_mb"] = value

                updater = update_ram
            elif choice == "2":
                value = int(prompt("Server port: "))
                if not 1 <= value <= 65535:
                    logger.log("ERROR", "Port must be between 1 and 65535.")
                    pause()
                    continue

                def update_port(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["server_settings"][
                        "port"
                    ] = value

                updater = update_port
            elif choice == "3":
                value = prompt("MOTD: ")

                def update_motd(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["server_settings"][
                        "motd"
                    ] = value

                updater = update_motd
            elif choice == "4":
                value = int(prompt("Max players: "))

                def update_max_players(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["server_settings"][
                        "max-players"
                    ] = value

                updater = update_max_players
            elif choice == "5":

                def update_online_mode(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    cur = str(
                        server["server_settings"].get("online-mode", "true")
                    ).lower()
                    server["server_settings"]["online-mode"] = (
                        "false" if cur == "true" else "true"
                    )

                updater = update_online_mode
            elif choice == "6":
                new_eula = "false" if eula_val else "true"
                write_text_file(eula_path, f"eula={new_eula}\n")
                logger.log("SUCCESS", f"EULA set to {new_eula}.")
                pause()
                continue
            elif choice == "7":

                def update_auto_restart(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    server["auto_restart"] = not server.get("auto_restart", False)

                updater = update_auto_restart
            elif choice == "8":

                def update_rcon_toggle(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    rcon_cfg = server.setdefault("rcon", {})
                    s_cfg = server.setdefault("server_settings", {})
                    new_state = not rcon_cfg.get("enabled", False)
                    rcon_cfg["enabled"] = new_state
                    s_cfg["enable-rcon"] = str(new_state).lower()

                updater = update_rcon_toggle
            elif choice == "9":
                value = prompt("RCON password: ")

                def update_rcon_password(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    server.setdefault("rcon", {})["password"] = value
                    server.setdefault("server_settings", {})["rcon.password"] = value

                updater = update_rcon_password
            elif choice == "10":
                edit_server_files(runtime, config_manager, logger)
                continue
            else:
                logger.log("ERROR", "Invalid configuration selection.")
                pause()
                continue

            if updater is not None:
                config_manager.mutate(updater)
            instance.apply_server_files()
        except ValueError:
            logger.log("ERROR", "A numeric value was required.")
            pause()


def edit_server_files(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        logger.log("ERROR", "No server is selected.")
        return
    instance = runtime.get_instance(current_server)
    instance.ensure_server_files()

    while True:
        properties_path = instance.server_dir / SERVER_PROPERTIES_FILE
        eula_path = instance.server_dir / EULA_FILE
        properties = load_properties(properties_path)
        eula_status = load_properties(eula_path).get("eula", "false")

        print_header(current_server, runtime)
        print(f"{C.BOLD}Server file editor{C.RESET}")
        print(f" server.properties: {properties_path}")
        print(f" eula.txt: {eula_path}")
        print(f" EULA accepted: {eula_status}")
        print("\n 1. Show current properties")
        print(" 2. Set or update a property")
        print(" 3. Delete a property")
        print(" 4. Toggle EULA")
        print(" 0. Back")

        choice = prompt(f"\n{C.BOLD}Choose action: {C.RESET}")
        if choice == "0":
            return
        if choice == "1":
            if not properties:
                print("No server.properties entries found.")
            else:
                for key, value in properties.items():
                    print(f" {key}={value}")
            pause()
            continue
        if choice == "2":
            key = prompt("Property key: ")
            if not key:
                logger.log("ERROR", "Property key cannot be empty.")
                pause()
                continue
            value = prompt("Property value: ")
            properties[key] = value
            instance.save_server_properties(properties)
            logger.log("SUCCESS", f"Updated {key} in {SERVER_PROPERTIES_FILE}.")
            pause()
            continue
        if choice == "3":
            key = prompt("Property key to delete: ")
            if key not in properties:
                logger.log("ERROR", f"Property '{key}' does not exist.")
                pause()
                continue
            properties.pop(key, None)
            instance.save_server_properties(properties)
            logger.log("SUCCESS", f"Deleted {key} from {SERVER_PROPERTIES_FILE}.")
            pause()
            continue
        if choice == "4":
            instance.set_eula(eula_status.lower() != "true")
            logger.log("SUCCESS", "Updated EULA flag.")
            pause()
            continue
        logger.log("ERROR", "Invalid file editor selection.")
        pause()


def choose_backup(instance, logger) -> Path | None:
    backups = instance.list_backups()
    if not backups:
        logger.log("INFO", "No backups are available.")
        return None
    for index, backup in enumerate(backups, start=1):
        print(f" {index}. {backup.name} ({format_bytes(backup.stat().st_size)})")
    choice = prompt(f"\n{C.BOLD}Choose backup: {C.RESET}")
    if not choice.isdigit():
        logger.log("ERROR", "Backup selection must be numeric.")
        return None
    selection = int(choice) - 1
    if selection < 0 or selection >= len(backups):
        logger.log("ERROR", "Invalid backup selection.")
        return None
    return backups[selection]


def world_manager(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        logger.log("ERROR", "No server is selected.")
        return
    instance = runtime.get_instance(current_server)

    while True:
        clear_screen()
        config = config_manager.load()
        server_config = config["servers"][current_server]
        backup_cfg = server_config.get("backup_settings", {})
        backup_enabled = backup_cfg.get("enabled", False)
        backup_interval = backup_cfg.get("interval_hours", 6)
        sched_text = (
            f"Every {backup_interval}h ({C.OK}Active{C.RESET})"
            if backup_enabled
            else f"{C.DIM}Disabled{C.RESET}"
        )

        backups = instance.list_backups()
        width = get_terminal_width()
        tag = f"MSM {C.BOX_H}{C.BOX_H} Backups: {current_server} "
        pad = max(2, width - visible_len(tag))
        print(f"{C.ACCENT}{tag}{C.DIM}{C.BOX_H * pad}{C.RESET}")
        print(f"  {C.DIM}Saved Backups :{C.RESET} {len(backups)}")
        print(f"  {C.DIM}Auto-Backup   :{C.RESET} {sched_text}")
        print(f"{C.DIM}{C.BOX_H * width}{C.RESET}")

        flash = render_flash()
        if flash:
            print(flash, end="")

        print(" [1] Create World Backup Now")
        print(" [2] List All Backups")
        print(" [3] Restore World from Backup")
        print(" [4] Delete a Backup")
        print(" [5] Configure Auto-Backup Schedule")
        print(" [0] Back")

        choice = prompt(f"\n{C.ACCENT}{C.POINTER}{C.RESET} ")
        if choice in ("0", "b", "q", "back", "exit"):
            return
        if choice == "1":
            try:
                backup_path = run_with_spinner(
                    "Creating backup", instance.create_backup
                )
                set_flash("[OK]", C.OK, f"Backup saved: {backup_path.name}")
            except Exception as exc:
                set_flash("[ERR]", C.ERR, f"Backup failed: {exc}")
            continue
        if choice == "2":
            if not backups:
                print("\nNo backups are available.")
            else:
                print(f"\n{C.BOLD}Available Backups:{C.RESET}")
                for backup in backups:
                    size_str = format_bytes(backup.stat().st_size)
                    print(f"  {C.ACCENT}{C.BULLET}{C.RESET} {backup.name} ({size_str})")
            pause()
            continue
        if choice == "3":
            backup_path = choose_backup(instance, logger)
            if not backup_path:
                pause()
                continue
            confirmation = prompt(
                "This will overwrite world data. Type RESTORE to continue: "
            )
            if confirmation != "RESTORE":
                set_flash("[INFO]", C.CYAN, "Restore cancelled.")
                continue
            try:
                run_with_spinner(
                    "Restoring backup", instance.restore_backup, backup_path.name
                )
                set_flash("[OK]", C.OK, f"Restored backup: {backup_path.name}")
            except Exception as exc:
                set_flash("[ERR]", C.ERR, f"Restore failed: {exc}")
            continue
        if choice == "4":
            backup_path = choose_backup(instance, logger)
            if not backup_path:
                pause()
                continue
            confirmation = prompt(f"Type DELETE to remove {backup_path.name}: ")
            if confirmation != "DELETE":
                set_flash("[INFO]", C.CYAN, "Deletion cancelled.")
                continue
            try:
                instance.delete_backup(backup_path.name)
                set_flash("[OK]", C.OK, f"Deleted backup: {backup_path.name}")
            except Exception as exc:
                set_flash("[ERR]", C.ERR, f"Deletion failed: {exc}")
            continue
        if choice == "5":
            prompt_str = (
                f"Enable scheduled auto-backups? "
                f"({'y/N' if backup_enabled else 'Y/n'}): "
            )
            toggle = prompt(prompt_str).lower()
            new_enabled = (toggle != "n") if not backup_enabled else (toggle == "y")
            interval_raw = prompt(f"Backup interval in hours [{backup_interval}]: ")
            try:
                new_interval = float(interval_raw) if interval_raw else backup_interval
            except ValueError:
                new_interval = backup_interval

            def update_sched(s: dict) -> None:
                b = s["servers"][current_server].setdefault("backup_settings", {})
                b["enabled"] = new_enabled
                b["interval_hours"] = new_interval

            config_manager.mutate(update_sched)
            set_flash("[OK]", C.OK, "Auto-backup schedule updated.")
            continue


def show_statistics(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    db_manager: DatabaseManager,
) -> None:
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        return
    stats = db_manager.get_server_statistics(current_server)
    print_header(current_server, runtime)
    print(f" {C.BOLD}Statistics for {current_server}:{C.RESET}\n")
    print(f"  Total sessions     : {stats['total_sessions']}")
    print(f"  Total uptime       : {format_duration(stats['total_uptime'])}")
    print(f"  Average session    : {format_duration(stats['avg_duration'])}")
    print(f"  Total crashes      : {stats['total_crashes']}")
    print(f"  Total restarts     : {stats['total_restarts']}")
    print(f"  Avg RAM usage (24h): {stats['avg_ram_usage_24h'] or 0:.2f}%")
    print(f"  Avg CPU usage (24h): {stats['avg_cpu_usage_24h'] or 0:.2f}%")
    print(f"  Peak players (24h) : {stats['peak_players_24h'] or 0}")
    pause()


def show_console(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        return
    instance = runtime.get_instance(current_server)
    if not instance.is_running():
        logger.log("ERROR", f"{current_server} is not running.")
        pause()
        return

    backend = instance.get_backend()
    can_attach, attach_cmd_or_reason = backend.attach()
    if not can_attach:
        logger.log("WARNING", attach_cmd_or_reason)
        view_live_console(runtime, config_manager, logger)
        return

    print(
        f"\n{C.CYAN}Attaching to {current_server} (screen: {instance.screen_name})...{C.RESET}"
    )
    print(
        f"{C.WARN}TIP: To detach without stopping the server, press Ctrl+A then D.{C.RESET}"
    )
    time.sleep(1.5)
    env = os.environ.copy()
    if running_on_termux():
        termux_tmp = os.environ.get("TMPDIR") or "/data/data/com.termux/files/usr/tmp"
        Path(termux_tmp).mkdir(parents=True, exist_ok=True)
        env.setdefault("TMPDIR", termux_tmp)
        env.setdefault("SCREENDIR", termux_tmp)
    try:
        res = subprocess.run(
            ["screen", "-x", instance.screen_name],
            env=env,
            check=False,
        )
        if res.returncode != 0:
            subprocess.run(
                ["screen", "-r", "-d", instance.screen_name],
                env=env,
                check=False,
            )
    except FileNotFoundError:
        logger.log("ERROR", "screen is not installed.")
    print(f"\n{C.DIM}Detached from console.{C.RESET}")
    pause()


def show_platform_diagnostics(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    from platforms.detector import detect_platform
    from platforms.paths import get_path_service

    platform_desc = detect_platform()
    path_service = get_path_service()
    caps = platform_desc.capabilities

    print_header(None, runtime)
    print(f" {C.BOLD}Platform & Capability Diagnostics{C.RESET}\n")

    print(f"  {C.BOLD}Host System:{C.RESET}")
    print(f"    OS Type         : {platform_desc.os_type.value}")
    print(f"    Variant         : {platform_desc.variant.value}")
    print(
        f"    Architecture    : {platform_desc.architecture.value} ({platform_desc.raw_arch})"
    )
    print(f"    OS Release      : {platform_desc.release}")
    print(f"    Python Version  : {platform_desc.python_version}")
    print(f"    Package Manager : {platform_desc.package_manager or 'None detected'}")
    wsl_str = f"WSL{platform_desc.wsl_version}" if platform_desc.is_wsl else "No"
    print(f"    WSL Environment : {wsl_str}")
    print(f"    Termux Android  : {'Yes' if platform_desc.is_termux else 'No'}")

    pmmp_str = "Supported" if caps.supports_pocketmine_binary else "Manual PHP Required"
    java_prov_str = (
        "Supported" if caps.supports_java_provisioning else "System packages only"
    )
    attach_str = "Supported" if caps.supports_console_attachment else "Logs tail only"

    print(f"\n  {C.BOLD}Runtime Capabilities:{C.RESET}")
    print(f"    Supported Backends: {', '.join(caps.supported_backends)}")
    print(f"    Default Backend   : {caps.default_backend}")
    print(f"    Screen Available  : {'Yes' if caps.supports_screen else 'No'}")
    print(f"    POSIX Signals     : {'Yes' if caps.supports_posix_signals else 'No'}")
    print(
        f"    Win Process Groups: {'Yes' if caps.supports_windows_process_groups else 'No'}"
    )
    print(f"    PocketMine PMMP   : {pmmp_str}")
    print(f"    Java Provisioning : {java_prov_str}")
    print(f"    Console Attachment: {attach_str}")

    print(f"\n  {C.BOLD}Storage & Paths:{C.RESET}")
    print(f"    Config Dir : {path_service.config_dir}")
    dbg_status = (
        f"{C.OK}ENABLED (verbose){C.RESET}"
        if logger.is_debug_enabled()
        else f"{C.DIM}DISABLED (silent){C.RESET}"
    )
    print(f"\n  {C.BOLD}Logging & Verbosity:{C.RESET}")
    print(f"    Terminal DEBUG Logs: {dbg_status}")
    print(f"    Full Log File      : {path_service.log_file}")

    if caps.notes:
        print(f"\n  {C.BOLD}Diagnostic Notes:{C.RESET}")
        for note in caps.notes:
            print(f"    {C.BULLET} {note}")

    print("\n  [ t] Toggle Terminal DEBUG logs")
    print("  [Enter] Continue")
    act = prompt(f"\n{C.BOLD}Action: {C.RESET}").lower()
    if act in ("t", "toggle"):
        new_state = logger.toggle_debug()
        new_text = "ENABLED" if new_state else "DISABLED (silent)"
        logger.log("SUCCESS", f"Terminal DEBUG output is now {new_text}.")
        pause()


def view_live_console(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        return
    instance = runtime.get_instance(current_server)
    log_file = instance.server_dir / "logs" / "latest.log"

    while True:
        clear_screen()
        width = get_terminal_width()
        is_running = instance.is_running()
        status_text = (
            f"{C.OK}{C.DOT_ON} ONLINE{C.RESET}"
            if is_running
            else f"{C.ERR}{C.DOT_OFF} OFFLINE{C.RESET}"
        )
        tag = f"MSM {C.BOX_H}{C.BOX_H} Console: {current_server} [{status_text}] "
        pad = max(2, width - visible_len(tag))
        print(f"{C.ACCENT}{tag}{C.DIM}{C.BOX_H * pad}{C.RESET}")
        print(f"{C.DIM}Log path: {log_file}{C.RESET}\n")

        lines: list[str] = []
        if log_file.exists():
            try:
                raw_lines = log_file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                lines = raw_lines[-15:]
            except Exception as e:
                lines = [f"[Error reading log: {e}]"]
        else:
            lines = ["[No log output generated yet]"]

        print(f"{C.DIM}{'─' * width}{C.RESET}")
        for line in lines:
            if "ERROR" in line or "Exception" in line:
                print(f"{C.ERR}{line}{C.RESET}")
            elif "WARN" in line:
                print(f"{C.WARN}{line}{C.RESET}")
            elif "INFO" in line:
                print(f"{C.CYAN}{line[:11]}{C.RESET}{line[11:]}")
            else:
                print(f"{C.DIM}{line}{C.RESET}")
        print(f"{C.DIM}{'─' * width}{C.RESET}\n")

        flash = render_flash()
        if flash:
            print(flash, end="")

        print(" [Enter] Refresh   [s] Send cmd   [a] Attach screen   [0] Back")
        action = prompt(f"\n{C.ACCENT}{C.POINTER}{C.RESET} ").lower()
        if action in ("0", "b", "q", "back"):
            return
        elif action == "s":
            cmd = prompt("Command: ")
            if cmd:
                if not instance.is_running():
                    set_flash("[ERR]", C.ERR, "Server is not running.")
                else:
                    success = instance.send_command(cmd)
                    if success:
                        set_flash("[OK]", C.OK, f"Sent: {cmd}")
                    else:
                        set_flash("[ERR]", C.ERR, "Delivery failed.")
        elif action == "a":
            show_console(runtime, config_manager, logger)


def send_command_menu(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        return
    instance = runtime.get_instance(current_server)
    if not instance.is_running():
        logger.log("ERROR", f"{current_server} is not running.")
        pause()
        return

    print_header(current_server, runtime)
    print(f" {C.BOLD}Send command to {current_server}:{C.RESET}")
    print(" (e.g. 'help', 'list', 'op <player>', 'time set day')")
    command = prompt("\n> ")
    if not command:
        return
    success = instance.send_command(command)
    if success:
        logger.log("SUCCESS", f"Sent command '{command}' to {current_server}.")
        time.sleep(0.5)
        log_file = instance.server_dir / "logs" / "latest.log"
        if log_file.exists():
            try:
                recent = log_file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()[-5:]
                if recent:
                    print(f"\n{C.DIM}Recent console output:{C.RESET}")
                    for ln in recent:
                        print(f"  {ln}")
            except Exception:
                pass
    else:
        logger.log("ERROR", "Command delivery failed.")
    pause()


def server_console_menu(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    view_live_console(runtime, config_manager, logger)


def server_management_menu(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> None:
    while True:
        config = ensure_current_server(config_manager)
        current_server = config.get("current_server")
        print_header(current_server, runtime)
        print(f" {C.BOLD}Server Instance Manager{C.RESET}\n")
        print(" [ 1] Switch Active Server")
        print(" [ 2] Create New Server")
        print(" [ 3] Platform & Capability Diagnostics")
        print(" [ 0] Back")

        choice = prompt(f"\n{C.BOLD}Choose action [0-3]: {C.RESET}")
        if choice == "0":
            return
        if choice == "1":
            select_current_server(config_manager, logger, runtime=runtime)
            pause()
        elif choice == "2":
            server_creation_wizard(runtime, config_manager, logger)
        elif choice == "3":
            show_platform_diagnostics(runtime, config_manager, logger)
        else:
            logger.log("ERROR", "Invalid choice.")
            pause()


def startup_server_picker(
    runtime: RuntimeManager, config_manager: ConfigManager, logger
) -> str | None:
    """Prompt the user on startup to select a server, displaying paths and live status."""
    from platforms.paths import get_path_service

    path_service = get_path_service()

    while True:
        config = config_manager.load()
        servers = list(config.get("servers", {}))
        if not servers:
            print_header(None, runtime)
            logger.log("INFO", "No servers found. Create one to begin.")
            server_creation_wizard(runtime, config_manager, logger)
            continue

        print_header(None, runtime)
        print(
            f" {C.BOLD}Select Server to Manage ({len(servers)} available):{C.RESET}\n"
        )

        for index, server_name in enumerate(servers, start=1):
            s_cfg = config.get("servers", {}).get(server_name, {})
            flavor = s_cfg.get("server_flavor")
            flavor_name = SERVER_FLAVORS.get(flavor, {}).get("name", "Not installed")
            version = s_cfg.get("server_version") or "N/A"
            server_dir = path_service.resolve_server_dir(server_name, config)
            inst = runtime.get_instance(server_name)
            status = (
                f"{C.OK}{C.DOT_ON} ONLINE{C.RESET}"
                if inst.is_running()
                else f"{C.ERR}{C.DOT_OFF} OFFLINE{C.RESET}"
            )

            print(f" [{index:2d}] {C.BOLD}{server_name}{C.RESET}  [{status}]")
            print(f"      {C.DIM}Type     :{C.RESET} {flavor_name} {version}")
            print(f"      {C.DIM}Directory:{C.RESET} {server_dir}")
            print()

        print(f" {C.DIM}── Actions ───────────────────────────────────{C.RESET}")
        print(" [ +] Create New Server")
        print(" [ d] Platform & System Diagnostics")
        print(" [ 0] Exit")

        choice = prompt(
            f"\n{C.BOLD}Choose server [1-{len(servers)}] or action: {C.RESET}"
        )
        if choice in ("0", "q", "exit"):
            return None
        elif choice in ("+", "c", "new", "n"):
            server_creation_wizard(runtime, config_manager, logger)
            continue
        elif choice.lower() == "d":
            show_platform_diagnostics(runtime, config_manager, logger)
            continue
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(servers):
                selected = servers[idx]
                config["current_server"] = selected
                config_manager.save(config)
                return selected
            else:
                logger.log("ERROR", "Invalid selection number.")
                pause()
        else:
            logger.log("ERROR", "Invalid selection.")
            pause()


def main() -> None:
    if "--diagnostics" in sys.argv or "-d" in sys.argv:
        import json
        from platforms.detector import detect_platform

        desc = detect_platform()
        print(json.dumps(desc.to_dict(), indent=2))
        return

    logger, config_manager, db_manager, runtime = create_services()
    if "--debug" in sys.argv or "-v" in sys.argv:
        logger.set_debug(True)
    if not check_base_dependencies(logger):
        raise SystemExit(1)

    selected = startup_server_picker(runtime, config_manager, logger)
    if not selected:
        if runtime.running_servers():
            leave = prompt(
                "Leave running servers active in background? (Y/n): ", default="y"
            ).lower()
            if leave == "n":
                for s_name in runtime.running_servers():
                    runtime.get_instance(s_name).stop()
        raise SystemExit(0)

    while True:
        config = ensure_current_server(config_manager)
        current_server = config.get("current_server")
        if not current_server or current_server not in config.get("servers", {}):
            selected = startup_server_picker(runtime, config_manager, logger)
            if not selected:
                break
            continue

        instance = runtime.get_instance(current_server)
        is_running = instance.is_running()
        width = get_terminal_width()

        clear_screen()
        render_server_hud(current_server, runtime, config_manager)

        flash = render_flash()
        if flash:
            print(flash, end="")

        divider = f"{C.DIM}{C.BOX_H * width}{C.RESET}"
        lbl_1 = "[1] Stop  " if is_running else "[1] Start "
        print(f"\n{C.DIM}── Controls ──────────────────────────────────{C.RESET}")
        print(f" {C.ACCENT}{lbl_1}{C.RESET} [2] Console   [3] Restart   [4] Kill")
        print(f"{C.DIM}── Config & Access ───────────────────────────{C.RESET}")
        print(" [5] Tunnels   [6] Properties   [7] Backups")
        print(f"{C.DIM}── Workspace ─────────────────────────────────{C.RESET}")
        print(" [s] Switch    [n] New       [d] Delete    [q] Quit")
        print(divider)

        raw_cmd = prompt(f"{C.ACCENT}{C.POINTER}{C.RESET} ").strip()
        if not raw_cmd:
            continue

        parts = raw_cmd.split(maxsplit=1)
        action_name = parts[0].lower()
        arg_val = parts[1].strip() if len(parts) > 1 else ""

        try:
            if action_name in ("1", "start", "stop"):
                if is_running:
                    instance.stop()
                    set_flash("[OK]", C.OK, f"Stopped {current_server}.")
                else:
                    started = instance.start()
                    if started:
                        set_flash(
                            "[OK]",
                            C.OK,
                            f"Started {current_server} (PID {instance.current_pid()}).",
                        )
                    else:
                        set_flash("[ERR]", C.ERR, "Server start failed.")
            elif action_name in ("2", "console", "log", "logs"):
                view_live_console(runtime, config_manager, logger)
            elif action_name in ("3", "restart"):
                instance.stop()
                started = instance.start()
                if started:
                    set_flash("[OK]", C.OK, f"Restarted {current_server}.")
                else:
                    set_flash("[ERR]", C.ERR, "Restart failed.")
            elif action_name in ("4", "kill"):
                instance.stop(force=True)
                set_flash("[WARN]", C.WARN, f"Force-killed {current_server}.")
            elif action_name in ("5", "tunnel", "tunnels"):
                tunnel_manager_menu(runtime, config_manager, current_server, logger)
            elif action_name in ("6", "prop", "props", "properties"):
                quick_property_editor(instance, config_manager, current_server, logger)
            elif action_name in ("7", "backup", "backups"):
                world_manager(runtime, config_manager, logger)
            elif action_name in ("s", "switch"):
                select_current_server(config_manager, logger, runtime=runtime)
            elif action_name in ("n", "new"):
                created = server_creation_wizard(runtime, config_manager, logger)
                if created:
                    cfg = config_manager.load()
                    cfg["current_server"] = created
                    config_manager.save(cfg)
            elif action_name in ("d", "delete"):
                confirmation = prompt(
                    f"Type DELETE to permanently remove '{current_server}': "
                )
                if confirmation == "DELETE":
                    if instance.is_running():
                        instance.stop(force=True)
                    shutil.rmtree(instance.server_dir, ignore_errors=True)

                    def remove_srv(s: dict) -> None:
                        s.get("servers", {}).pop(current_server, None)
                        if s.get("current_server") == current_server:
                            s["current_server"] = None

                    config_manager.mutate(remove_srv)
                    set_flash("[OK]", C.OK, f"Deleted server '{current_server}'.")
            elif action_name in ("q", "0", "quit", "exit"):
                if runtime.running_servers():
                    leave = prompt(
                        "Leave running servers active in background? (Y/n): ",
                        default="y",
                    ).lower()
                    if leave == "n":
                        for s_name in runtime.running_servers():
                            runtime.get_instance(s_name).stop()
                raise SystemExit(0)
            elif action_name in ("cmd", "rcon"):
                if not arg_val:
                    set_flash("[WARN]", C.WARN, "Usage: cmd <command>")
                elif not instance.is_running():
                    set_flash("[ERR]", C.ERR, "Server is not running.")
                else:
                    success = instance.send_command(arg_val)
                    if success:
                        set_flash("[OK]", C.OK, f"Sent: {arg_val}")
                    else:
                        set_flash("[ERR]", C.ERR, "Command delivery failed.")
            elif action_name == "set":
                if "=" in arg_val:
                    k, v = arg_val.split("=", 1)
                    k, v = k.strip(), v.strip()
                    props_path = instance.server_dir / SERVER_PROPERTIES_FILE
                    props = load_properties(props_path)
                    props[k] = v
                    instance.save_server_properties(props)
                    set_flash("[OK]", C.OK, f"Set {k}={v}")
                else:
                    set_flash("[WARN]", C.WARN, "Usage: set key=val")
            elif action_name == "use":
                cfg = config_manager.load()
                all_servers = list(cfg.get("servers", {}))
                target = arg_val
                if target.isdigit():
                    idx = int(target) - 1
                    if 0 <= idx < len(all_servers):
                        target = all_servers[idx]
                if target in all_servers:
                    cfg["current_server"] = target
                    config_manager.save(cfg)
                    set_flash("[OK]", C.OK, f"Active server set to '{target}'.")
                else:
                    set_flash("[ERR]", C.ERR, f"Server '{target}' not found.")
            elif action_name == "help":
                clear_screen()
                print(f"{C.BOLD}Available Commands & Shortcuts:{C.RESET}\n")
                print("  start / stop   : Toggle active server state")
                print("  restart / kill : Restart or force-terminate server")
                print("  cmd <command>  : Send command via console or RCON")
                print("  set <key>=<val>: Update property in server.properties")
                print("  use <name>     : Switch active server context")
                print("  5 (tunnels)    : Manage Playit / Ngrok remote tunnels")
                print("  6 (properties) : Quick property editor")
                print("  7 (backups)    : World backup and restore manager")
                print("  s (switch)     : Interactive server selector")
                print("  n (new)        : Server creation wizard")
                print("  d (delete)     : Delete active server")
                pause()
            else:
                set_flash("[ERR]", C.ERR, f"Unknown command: '{raw_cmd}'. Type 'help'.")
        except KeyboardInterrupt as exc:
            raise SystemExit(0) from exc
        except Exception as exc:
            logger.log("CRITICAL", f"Unexpected error: {exc}")
            pause()


if __name__ == "__main__":
    main()
