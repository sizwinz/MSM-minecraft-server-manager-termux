"""Interactive CLI for Minecraft Server Manager."""

from __future__ import annotations

import shutil
import subprocess
import threading
import time
from pathlib import Path

from core.config import ConfigManager
from core.constants import (
    CONFIG_FILE,
    DATABASE_FILE,
    DEFAULT_TUNNEL_BINARIES,
    EULA_FILE,
    LOG_FILE,
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
from ui.colors import C
from utils.logging_utils import EnhancedLogger
from utils.network import download_ngrok_binary, get_versions_for_flavor
from utils.ngrok import diagnose_ngrok
from utils.playit import diagnose_playit
from utils.playit_api import (
    PLAYIT_THIRD_PARTY_AUTH_URL,
    PlayitApiClient,
    PlayitApiError,
    load_playit_session,
    save_playit_session,
)
from utils.properties import load_properties
from utils.system import (
    check_base_dependencies,
    format_bytes,
    get_server_dir,
    get_system_info,
    read_text_file,
    run_command,
    running_on_termux,
    sanitize_input,
    write_text_file,
)
from utils.tunnels import (
    build_playit_claim_exchange_command,
    build_playit_claim_generate_command,
    build_playit_claim_url_command,
    extract_last_non_empty_line,
    extract_playit_claim_url,
)


def pause() -> None:
    input("\nPress Enter to continue...")


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
        except Exception as exc:  # pragma: no cover - presentation glue
            state["error"] = exc
        finally:
            state["done"] = True

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    index = 0
    while not state["done"]:
        print(
            f"\r {C.PRIMARY}{spinner[index % len(spinner)]}{C.RESET} {message}",
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
    default: int = 60, min_width: int = 36, max_width: int = 72
) -> int:
    cols = shutil.get_terminal_size((default, 24)).columns
    return max(min_width, min(cols, max_width))


def create_services():
    logger = EnhancedLogger(LOG_FILE, MAX_LOG_SIZE, LOG_RETENTION_DAYS)
    config_manager = ConfigManager(CONFIG_FILE, logger)
    db_manager = DatabaseManager(DATABASE_FILE)
    runtime = RuntimeManager(config_manager, db_manager, logger)
    return logger, config_manager, db_manager, runtime


def ensure_current_server(config_manager: ConfigManager) -> dict:
    config = config_manager.load()
    if not config.get("current_server") and config.get("servers"):
        config["current_server"] = next(iter(config["servers"]))
        config = config_manager.save(config)
    return config


def print_header(current_server: str | None, runtime: RuntimeManager) -> None:
    clear_screen()
    width = get_terminal_width()
    divider = f"{C.PRIMARY}{C.BOX_H * width}{C.RESET}"

    system_info = get_system_info()
    running_servers = runtime.running_servers()
    ram_usage = f"{system_info['available_ram_mb']}MB/{system_info['total_ram_mb']}MB"
    cpu_info = f"{system_info['cpu_count']}c @ {system_info['cpu_usage']:.1f}%"

    print(
        f"{C.BOLD}{C.PRIMARY}  MSM{C.RESET} "
        f"{C.DIM}• Minecraft Server Manager v{VERSION}{C.RESET}"
    )
    print(divider)

    sys_full = f"RAM: {ram_usage}  CPU: {cpu_info}  OS: {system_info['platform']}"
    if len(sys_full) + 16 <= width:
        print(f" {C.DIM}{sys_full} • Active: {len(running_servers)}{C.RESET}")
    else:
        print(f" {C.DIM}RAM: {ram_usage} • CPU: {cpu_info}{C.RESET}")
        print(
            f" {C.DIM}OS: {system_info['platform']} "
            f"• Active: {len(running_servers)}{C.RESET}"
        )

    if current_server:
        print(f" {C.DIM}Active Server:{C.RESET} {C.BOLD}{current_server}{C.RESET}")
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
    if instance.is_running():
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
    instance,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> bool:
    config = config_manager.load()
    server_config = config["servers"][current_server]
    tunnel = server_config.setdefault("tunnel", {})
    secret = read_text_file(instance.playit_secret_file)
    if not secret:
        logger.log(
            "WARNING",
            "Playit agent is not linked yet; complete the claim flow before creating a tunnel.",
        )
        return False

    session_key = load_playit_session()
    if not session_key:
        print()
        print("Open this Playit authorization page:")
        print(PLAYIT_THIRD_PARTY_AUTH_URL)
        auth_code = input("Paste the one-time Playit auth code: ").strip()
        if not auth_code:
            logger.log(
                "WARNING", "Playit API tunnel setup skipped; no auth code was entered."
            )
            return False
        try:
            session_key = PlayitApiClient().login_apply(auth_code)
        except PlayitApiError as exc:
            logger.log("ERROR", f"Playit authentication failed: {exc}")
            return False
        save_choice = (
            input(
                "Save the Playit login secret locally for future tunnel updates? (Y/n): "
            )
            .strip()
            .lower()
        )
        if save_choice != "n":
            path = save_playit_session(session_key)
            logger.log("SUCCESS", f"Saved Playit login secret at {path}")

    protocol = tunnel.get("protocol", "tcp")
    local_host = tunnel.get("local_host", "127.0.0.1")
    local_port = tunnel.get("local_port") or instance.get_server_port()
    flavor = server_config.get("server_flavor")
    if flavor == "pocketmine":
        protocol = "udp"

    try:
        agent_data = PlayitApiClient(agent_secret=secret).agent_rundata()
        agent_id = agent_data.get("agent_id")
        if not agent_id:
            raise PlayitApiError("Playit did not return an agent id.")
        tunnel_id, endpoint = PlayitApiClient(
            session_key=session_key
        ).create_or_update_tunnel(
            server_name=current_server,
            agent_id=agent_id,
            flavor=flavor,
            protocol=protocol,
            local_host=local_host,
            local_port=local_port,
            existing_tunnel_id=tunnel.get("playit_tunnel_id"),
        )
    except PlayitApiError as exc:
        logger.log("ERROR", f"Playit tunnel automation failed: {exc}")
        return False

    def updater(saved_config: dict) -> None:
        saved_tunnel = saved_config["servers"][current_server].setdefault("tunnel", {})
        saved_tunnel["provider"] = "playit"
        saved_tunnel["protocol"] = protocol
        saved_tunnel["local_host"] = local_host
        saved_tunnel["local_port"] = int(local_port)
        saved_tunnel["playit_tunnel_id"] = tunnel_id
        if endpoint:
            saved_tunnel["last_endpoint"] = endpoint

    config_manager.mutate(updater)
    if endpoint:
        write_text_file(instance.playit_endpoint_file, endpoint)
        logger.log("SUCCESS", f"Playit tunnel is configured: {endpoint}")
    else:
        logger.log(
            "SUCCESS", "Playit tunnel is configured; endpoint is pending allocation."
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
        symbol = f"{C.GREEN}✓{C.RESET}" if check.ok else f"{C.RED}✗{C.RESET}"
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
    current_binary = tunnel.get("binary_path") or DEFAULT_TUNNEL_BINARIES["ngrok"]
    default_binary = resolve_tunnel_binary(current_binary) or current_binary

    print_header(current_server, runtime)
    print(f"{C.BOLD}Ngrok Setup Wizard{C.RESET}")
    print_connection_summary(instance)
    print()
    print("Ngrok requirements:")
    print(" - The ngrok agent must be installed and reachable by MSM.")
    print(" - Your ngrok account must be configured with an authtoken.")
    print(" - TCP endpoints may require billing details on ngrok.")
    if running_on_termux():
        print(
            " - If you installed ngrok through a wrapper, set the binary path to that wrapper."
        )

    binary_path = (
        input(f"\nNgrok binary path [{default_binary}]: ").strip() or default_binary
    )
    resolved_binary = resolve_tunnel_binary(binary_path)
    if resolved_binary:
        logger.log("INFO", f"Using ngrok binary at {resolved_binary}")
    else:
        logger.log(
            "WARNING", f"Ngrok binary '{binary_path}' was not found on this device."
        )
        prompt = "Would you like to automatically download and install ngrok? (Y/n): "
        if input(prompt).strip().lower() != "n":
            logger.log("INFO", "Downloading ngrok...")
            downloaded_path = download_ngrok_binary(logger=logger)
            if downloaded_path:
                logger.log("SUCCESS", f"Ngrok installed to {downloaded_path}")
                resolved_binary = str(downloaded_path)
            else:
                logger.log("ERROR", "Auto-installation failed.")

    authtoken = input(
        "Ngrok authtoken (leave blank to keep the existing config): "
    ).strip()
    if authtoken:
        if not resolved_binary:
            logger.log(
                "ERROR",
                "Cannot configure ngrok authtoken because the binary was not found.",
            )
        else:
            result = run_command(
                [resolved_binary, "config", "add-authtoken", authtoken],
                logger=logger,
                check=False,
                capture_output=True,
            )
            if result and result.returncode == 0:
                logger.log("SUCCESS", "Stored ngrok authtoken successfully.")
            else:
                stderr = ""
                if result:
                    stderr = (result.stderr or result.stdout or "").strip()
                logger.log(
                    "ERROR", f"Failed to store ngrok authtoken. {stderr}".strip()
                )

    enable_tunnel = (
        input("Enable ngrok for this server? (Y/n): ").strip().lower() != "n"
    )
    save_tunnel_config(
        instance,
        config_manager,
        current_server,
        provider="ngrok",
        binary_path=binary_path,
        enabled=enable_tunnel,
        logger=logger,
    )
    pause()


def playit_setup_wizard(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> None:
    instance = runtime.get_instance(current_server)
    config = config_manager.load()
    tunnel = config["servers"][current_server].setdefault("tunnel", {})
    current_binary = tunnel.get("binary_path")
    if not current_binary or current_binary == DEFAULT_TUNNEL_BINARIES["ngrok"]:
        current_binary = (
            resolve_tunnel_binary("playit")
            or resolve_tunnel_binary(DEFAULT_TUNNEL_BINARIES["playit"])
            or DEFAULT_TUNNEL_BINARIES["playit"]
        )

    print_header(current_server, runtime)
    print(f"{C.BOLD}Playit Setup Wizard{C.RESET}")
    print_connection_summary(instance)
    print()
    print("Playit notes:")
    print(" - MSM uses `claim generate`, `claim url`, and `claim exchange` for setup.")
    print(" - MSM uses the playit agent for the managed background session.")
    print(" - MSM can create or update the Playit tunnel after the agent is linked.")
    print(" - Playit account approval happens in your browser with a one-time code.")
    print(f" - Local tunnel target defaults to 127.0.0.1:{instance.get_server_port()}.")
    if running_on_termux():
        print("\nInstall playit on Termux:")
        print(" pkg update && pkg upgrade")
        print(" pkg install tur-repo playit")
        print(" MSM does not need tmux when it manages playit for this server.")

    binary_path = input(f"\nPlayit binary path [{current_binary}]: ").strip() or str(
        current_binary
    )
    from utils.playit import resolve_playit_binary

    resolved_binary = resolve_playit_binary(binary_path)
    if resolved_binary:
        logger.log("INFO", f"Using playit binary at {resolved_binary}")
    else:
        logger.log(
            "WARNING", f"Playit binary '{binary_path}' was not found on this device."
        )
        if running_on_termux():
            prompt = "Would you like to automatically install playit via termux packages? (Y/n): "
            if input(prompt).strip().lower() != "n":
                logger.log("INFO", "Installing playit...")
                repo_result = run_command(
                    ["pkg", "install", "-y", "tur-repo"],
                    logger=logger,
                    check=False,
                    capture_output=True,
                )
                playit_result = run_command(
                    ["pkg", "install", "-y", "playit"],
                    logger=logger,
                    check=False,
                    capture_output=True,
                )
                if (
                    repo_result
                    and repo_result.returncode == 0
                    and playit_result
                    and playit_result.returncode == 0
                ):
                    resolved_binary = resolve_playit_binary("playit")
                    if resolved_binary:
                        logger.log(
                            "SUCCESS",
                            f"Playit installed successfully at {resolved_binary}",
                        )
                    else:
                        logger.log(
                            "ERROR",
                            "Installation succeeded but binary is still missing.",
                        )
                else:
                    logger.log("ERROR", "Playit auto-installation failed.")

    if resolved_binary:
        run_guided_claim = (
            input("Run the guided playit claim flow now? (Y/n): ").strip().lower()
            != "n"
        )
        if run_guided_claim:
            is_daemon = Path(resolved_binary).name == "playitd"
            daemon = None
            cli_binary = resolved_binary
            socket_file = None
            if is_daemon:
                socket_file = instance.server_dir / ".msm.playit.sock"
                secret_file = instance.playit_secret_file
                # Try to find the CLI companion for the daemon
                parent = Path(resolved_binary).parent
                cli_binary = str(parent / "playit-cli")
                if not Path(cli_binary).exists():
                    cli_binary = str(parent / "playit")

                if not Path(cli_binary).exists():
                    cli_binary = (
                        shutil.which("playit-cli")
                        or shutil.which("playit")
                        or resolved_binary
                    )

                if socket_file.exists():
                    socket_file.unlink()

                logger.log(
                    "INFO",
                    f"Starting temporary {Path(resolved_binary).name} for setup...",
                )
                daemon = subprocess.Popen(
                    [
                        resolved_binary,
                        "--secret-path",
                        str(secret_file),
                        "--socket-path",
                        str(socket_file),
                    ],
                    cwd=instance.server_dir,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(1)  # Allow daemon to initialize socket

            try:
                claim_generate = run_command(
                    build_playit_claim_generate_command(
                        cli_binary, socket_path=socket_file
                    ),
                    logger=logger,
                    check=False,
                    capture_output=True,
                    cwd=instance.server_dir,
                )
                generate_output = ""
                if claim_generate:
                    gen_stdout = claim_generate.stdout or ""
                    gen_stderr = claim_generate.stderr or ""
                    generate_output = f"{gen_stdout}\n{gen_stderr}".strip()
                raw_claim_line = extract_last_non_empty_line(generate_output)
                claim_code = raw_claim_line.split()[-1] if raw_claim_line else None

                if (
                    not claim_generate
                    or claim_generate.returncode != 0
                    or not claim_code
                ):
                    logger.log(
                        "ERROR",
                        "Failed to generate a playit claim code.",
                    )
                    if generate_output:
                        logger.log("INFO", generate_output)
                else:
                    logger.log("INFO", f"Playit claim code: {claim_code}")
                    claim_url_result = run_command(
                        build_playit_claim_url_command(
                            cli_binary,
                            claim_code,
                            socket_path=socket_file,
                        ),
                        logger=logger,
                        check=False,
                        capture_output=True,
                        cwd=instance.server_dir,
                    )
                    url_output = ""
                    if claim_url_result:
                        url_output = (
                            f"{claim_url_result.stdout or ''}\n"
                            f"{claim_url_result.stderr or ''}"
                        ).strip()
                    claim_url = extract_playit_claim_url(url_output)
                    claim_url = claim_url or extract_last_non_empty_line(url_output)
                    if claim_url:
                        logger.log("INFO", f"Open this playit claim URL: {claim_url}")
                        print()
                        print(f"Claim URL: {claim_url}")
                    else:
                        logger.log(
                            "WARNING",
                            "MSM could not derive a playit claim URL automatically.",
                        )
                    step = input(
                        "Press Enter after linking the device in your browser, or type 'skip': "
                    ).strip()
                    if step.lower() != "skip":
                        exchange_result = run_command(
                            build_playit_claim_exchange_command(
                                cli_binary,
                                claim_code,
                                secret_path=instance.playit_secret_file,
                                socket_path=socket_file,
                            ),
                            logger=logger,
                            check=False,
                            capture_output=True,
                            cwd=instance.server_dir,
                        )
                        exchange_output = ""
                        if exchange_result:
                            exchange_output = f"{exchange_result.stdout or ''}\n"
                            exchange_output += exchange_result.stderr or ""
                            exchange_output = exchange_output.strip()
                        stored_secret = read_text_file(instance.playit_secret_file)
                        if not stored_secret:
                            raw_secret_line = extract_last_non_empty_line(
                                exchange_output
                            )
                            fallback_secret = (
                                raw_secret_line.split()[-1] if raw_secret_line else None
                            )
                            if fallback_secret:
                                write_text_file(
                                    instance.playit_secret_file, fallback_secret
                                )
                                stored_secret = fallback_secret
                        if (
                            exchange_result
                            and exchange_result.returncode == 0
                            and stored_secret
                        ):
                            logger.log(
                                "SUCCESS",
                                (
                                    f"Stored playit secret for {current_server} "
                                    f"at {instance.playit_secret_file}"
                                ),
                            )
                        else:
                            logger.log(
                                "ERROR",
                                "Playit claim exchange did not produce a usable secret.",
                            )
                            if exchange_output:
                                logger.log("INFO", exchange_output)
            finally:
                if daemon:
                    daemon.terminate()

    if read_text_file(instance.playit_secret_file):
        auto_map = (
            input("Create or update the Playit tunnel for this server now? (Y/n): ")
            .strip()
            .lower()
            != "n"
        )
        if auto_map:
            configure_playit_api_tunnel(
                instance,
                config_manager,
                current_server,
                logger,
            )

    enable_tunnel = (
        input("Enable playit for this server? (Y/n): ").strip().lower() != "n"
    )
    save_tunnel_config(
        instance,
        config_manager,
        current_server,
        provider="playit",
        binary_path=binary_path,
        enabled=enable_tunnel,
        logger=logger,
    )
    pause()


def tunnel_setup_wizard(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> None:
    instance = runtime.get_instance(current_server)

    while True:
        config = config_manager.load()
        tunnel = config["servers"][current_server].setdefault("tunnel", {})
        print_header(current_server, runtime)
        print(f"{C.BOLD}Tunnel Setup Wizard{C.RESET}")
        print(f"Current provider: {tunnel.get('provider', 'playit')}")
        print(f"Enabled: {tunnel.get('enabled', False)}")
        print(f"Binary: {tunnel.get('binary_path', DEFAULT_TUNNEL_BINARIES['playit'])}")
        print(f"Protocol: {tunnel.get('protocol', 'tcp')}")
        print_connection_summary(instance)
        print()
        print(" 1. Setup ngrok")
        print(" 2. Setup playit")
        print(" 3. Diagnostics (current provider)")
        print(" 4. Playit diagnostics")
        print(" 5. Ngrok diagnostics")
        print(" 6. Disable tunnel for this server")
        print(" 0. Back")

        choice = input(f"\n{C.BOLD}Choose action: {C.RESET}").strip()
        if choice == "0":
            return
        if choice == "1":
            ngrok_setup_wizard(runtime, config_manager, current_server, logger)
            continue
        if choice == "2":
            playit_setup_wizard(runtime, config_manager, current_server, logger)
            continue
        if choice == "3":
            tunnel_diagnostics_screen(runtime, config_manager, current_server, logger)
            continue
        if choice == "4":
            tunnel_diagnostics_screen(
                runtime, config_manager, current_server, logger, provider="playit"
            )
            continue
        if choice == "5":
            tunnel_diagnostics_screen(
                runtime, config_manager, current_server, logger, provider="ngrok"
            )
            continue
        if choice == "6":
            binary_path = tunnel.get(
                "binary_path",
                DEFAULT_TUNNEL_BINARIES.get(
                    tunnel.get("provider", "playit"), "playit-cli"
                ),
            )
            save_tunnel_config(
                instance,
                config_manager,
                current_server,
                provider=tunnel.get("provider", "playit"),
                binary_path=binary_path,
                enabled=False,
                logger=logger,
            )
            pause()
            continue
        logger.log("ERROR", "Invalid tunnel wizard selection.")
        pause()


def create_new_server(config_manager: ConfigManager, logger) -> None:
    name = input(f"{C.BOLD}Enter a new server name: {C.RESET}").strip()
    if not name:
        logger.log("ERROR", "Server name cannot be empty.")
        return
    sanitized_name = sanitize_input(name)
    config = config_manager.load()
    if sanitized_name in config.get("servers", {}):
        logger.log("ERROR", f"Server '{sanitized_name}' already exists.")
        return
    config_manager.ensure_server(sanitized_name)
    get_server_dir(sanitized_name).mkdir(parents=True, exist_ok=True)
    logger.log("SUCCESS", f"Created server '{sanitized_name}'.")


def select_current_server(config_manager: ConfigManager, logger) -> None:
    config = config_manager.load()
    servers = list(config.get("servers", {}))
    if not servers:
        logger.log("ERROR", "No servers are configured.")
        return
    print(f"{C.BOLD}Configured servers:{C.RESET}")
    for index, server_name in enumerate(servers, start=1):
        print(f" {index}. {server_name}")
    choice = input(f"\n{C.BOLD}Choose server: {C.RESET}").strip()
    if not choice.isdigit():
        logger.log("ERROR", "Selection must be a number.")
        return
    selection = int(choice) - 1
    if selection < 0 or selection >= len(servers):
        logger.log("ERROR", "Invalid server selection.")
        return
    config["current_server"] = servers[selection]
    config_manager.save(config)
    logger.log("SUCCESS", f"Switched to server '{servers[selection]}'.")


def select_server_flavor() -> str | None:
    flavors = list(SERVER_FLAVORS)
    print(f"{C.BOLD}Server flavors:{C.RESET}")
    for index, flavor in enumerate(flavors, start=1):
        details = SERVER_FLAVORS[flavor]
        print(f" {index}. {details['name']} - {details['description']}")
    choice = input(f"\n{C.BOLD}Choose flavor: {C.RESET}").strip()
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
        choice = input(f"{C.BOLD}Choose version: {C.RESET}").strip().lower()
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
        print_header(current_server, runtime)
        print(f"{C.BOLD}Configure {current_server}{C.RESET}")
        print(f" 1. RAM MB: {server_config['ram_mb']}")
        print(f" 2. Port: {server_config['server_settings']['port']}")
        print(f" 3. Auto restart: {server_config['auto_restart']}")
        print(f" 4. MOTD: {server_config['server_settings']['motd']}")
        print(f" 5. Max players: {server_config['server_settings']['max-players']}")
        print(f" 6. Online mode: {server_config['server_settings']['online-mode']}")
        print(f" 7. Scheduled backups: {server_config['backup_settings']['enabled']}")
        print(
            f" 8. Backup interval hours: {server_config['backup_settings']['interval_hours']}"
        )
        print(f" 9. Tunnel enabled: {server_config['tunnel']['enabled']}")
        print(f"10. Tunnel provider: {server_config['tunnel']['provider']}")
        print(f"11. Tunnel binary: {server_config['tunnel']['binary_path']}")
        print(f"12. Tunnel protocol: {server_config['tunnel'].get('protocol', 'tcp')}")
        print(
            f"13. Tunnel local host: {server_config['tunnel'].get('local_host', '127.0.0.1')}"
        )
        print(
            f"14. Tunnel local port: {server_config['tunnel'].get('local_port') or 'auto'}"
        )
        print("15. Tunnel setup wizard")
        print(f"16. RCON enabled: {server_config['rcon']['enabled']}")
        print(f"17. RCON password set: {bool(server_config['rcon']['password'])}")
        print(" 0. Back")

        choice = input(f"\n{C.BOLD}Choose setting: {C.RESET}").strip()
        if choice == "0":
            return
        try:
            if choice == "1":
                value = int(input("RAM in MB: ").strip())

                def update_ram(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["ram_mb"] = value

                updater = update_ram

            elif choice == "2":
                value = int(input("Server port: ").strip())
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

                def update_auto_restart(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    server["auto_restart"] = not server["auto_restart"]

                updater = update_auto_restart

            elif choice == "4":
                value = input("MOTD: ").strip()

                def update_motd(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["server_settings"][
                        "motd"
                    ] = value

                updater = update_motd

            elif choice == "5":
                value = int(input("Max players: ").strip())

                def update_max_players(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["server_settings"][
                        "max-players"
                    ] = value

                updater = update_max_players

            elif choice == "6":

                def update_online_mode(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    current_value = str(
                        server["server_settings"].get("online-mode", "true")
                    ).lower()
                    server["server_settings"]["online-mode"] = (
                        "false" if current_value == "true" else "true"
                    )

                updater = update_online_mode

            elif choice == "7":

                def update_backup_toggle(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    backup_settings = server.setdefault("backup_settings", {})
                    backup_settings["enabled"] = not backup_settings.get(
                        "enabled", False
                    )

                updater = update_backup_toggle

            elif choice == "8":
                value = float(input("Backup interval in hours: ").strip())

                def update_backup_interval(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["backup_settings"][
                        "interval_hours"
                    ] = value

                updater = update_backup_interval

            elif choice == "9":

                def update_tunnel_toggle(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    tunnel = server.setdefault("tunnel", {})
                    tunnel["enabled"] = not tunnel.get("enabled", False)

                updater = update_tunnel_toggle

            elif choice == "10":
                provider_options = ", ".join(SUPPORTED_TUNNEL_PROVIDERS)
                current_provider = server_config["tunnel"].get("provider", "playit")
                value = (
                    input(
                        f"Tunnel provider ({provider_options}) [{current_provider}]: "
                    )
                    .strip()
                    .lower()
                    or current_provider
                )
                if value not in SUPPORTED_TUNNEL_PROVIDERS:
                    logger.log(
                        "ERROR", f"Tunnel provider must be one of: {provider_options}"
                    )
                    pause()
                    continue

                def update_tunnel_provider(saved_config: dict) -> None:
                    tunnel = saved_config["servers"][current_server].setdefault(
                        "tunnel", {}
                    )
                    previous_provider = tunnel.get("provider", "playit")
                    previous_binary = tunnel.get(
                        "binary_path", DEFAULT_TUNNEL_BINARIES["playit"]
                    )
                    tunnel["provider"] = value
                    if previous_binary == DEFAULT_TUNNEL_BINARIES.get(
                        previous_provider,
                        previous_binary,
                    ):
                        tunnel["binary_path"] = DEFAULT_TUNNEL_BINARIES.get(
                            value, value
                        )

                updater = update_tunnel_provider

            elif choice == "11":
                current_provider = server_config["tunnel"].get("provider", "playit")
                default_binary = server_config["tunnel"].get(
                    "binary_path",
                    DEFAULT_TUNNEL_BINARIES.get(current_provider, current_provider),
                )
                value = (
                    input(f"Tunnel binary path [{default_binary}]: ").strip()
                    or default_binary
                )

                def update_tunnel_binary(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["tunnel"][
                        "binary_path"
                    ] = value

                updater = update_tunnel_binary

            elif choice == "12":
                protocol_options = ", ".join(SUPPORTED_TUNNEL_PROTOCOLS)
                current_proto = server_config["tunnel"].get("protocol", "tcp")
                value = (
                    input(f"Protocol ({protocol_options}) [{current_proto}]: ")
                    .strip()
                    .lower()
                    or current_proto
                )
                if value not in SUPPORTED_TUNNEL_PROTOCOLS:
                    logger.log(
                        "ERROR",
                        f"Protocol must be one of: {protocol_options}",
                    )
                    pause()
                    continue

                def update_tunnel_protocol(saved_config: dict) -> None:
                    saved_config["servers"][current_server].setdefault("tunnel", {})[
                        "protocol"
                    ] = value

                updater = update_tunnel_protocol

            elif choice == "13":
                current_host = server_config["tunnel"].get("local_host", "127.0.0.1")
                value = input(f"Local host [{current_host}]: ").strip() or current_host

                def update_tunnel_host(saved_config: dict) -> None:
                    saved_config["servers"][current_server].setdefault("tunnel", {})[
                        "local_host"
                    ] = value

                updater = update_tunnel_host

            elif choice == "14":
                current_lp = server_config["tunnel"].get("local_port")
                prompt_default = str(current_lp) if current_lp else "auto"
                raw = input(f"Local port [{prompt_default}]: ").strip()
                if raw.lower() in ("", "auto", "none"):
                    lp_value = None
                else:
                    lp_value = int(raw)

                def update_tunnel_port(saved_config: dict) -> None:
                    saved_config["servers"][current_server].setdefault("tunnel", {})[
                        "local_port"
                    ] = lp_value

                updater = update_tunnel_port

            elif choice == "15":
                tunnel_setup_wizard(runtime, config_manager, current_server, logger)
                continue

            elif choice == "16":

                def update_rcon_toggle(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    rcon = server.setdefault("rcon", {})
                    settings = server.setdefault("server_settings", {})
                    enabled = not rcon.get("enabled", False)
                    rcon["enabled"] = enabled
                    settings["enable-rcon"] = str(enabled).lower()

                updater = update_rcon_toggle

            elif choice == "17":
                value = input("RCON password: ").strip()

                def update_rcon_password(saved_config: dict) -> None:
                    server = saved_config["servers"][current_server]
                    server["rcon"]["password"] = value
                    server["server_settings"]["rcon.password"] = value

                updater = update_rcon_password

            else:
                logger.log("ERROR", "Invalid configuration selection.")
                pause()
                continue

            if updater is not None:
                saved_config = config_manager.mutate(updater)
            else:
                saved_config = config
            instance.apply_server_files()
            if choice in {"9", "10", "11", "12", "13", "14"} and instance.is_running():
                tunnel_settings = saved_config["servers"][current_server]["tunnel"]
                if tunnel_settings.get("enabled"):
                    instance.restart_tunnel()
                else:
                    instance.stop_tunnel()
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

        choice = input(f"\n{C.BOLD}Choose action: {C.RESET}").strip()
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
            key = input("Property key: ").strip()
            if not key:
                logger.log("ERROR", "Property key cannot be empty.")
                pause()
                continue
            value = input("Property value: ").strip()
            properties[key] = value
            instance.save_server_properties(properties)
            logger.log("SUCCESS", f"Updated {key} in {SERVER_PROPERTIES_FILE}.")
            pause()
            continue
        if choice == "3":
            key = input("Property key to delete: ").strip()
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
    choice = input(f"\n{C.BOLD}Choose backup: {C.RESET}").strip()
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
        print_header(current_server, runtime)
        print(f"{C.BOLD}World manager{C.RESET}")
        print(" 1. Create backup")
        print(" 2. List backups")
        print(" 3. Restore backup")
        print(" 4. Delete backup")
        print(" 0. Back")

        choice = input(f"\n{C.BOLD}Choose action: {C.RESET}").strip()
        if choice == "0":
            return
        if choice == "1":
            try:
                backup_path = run_with_spinner(
                    "Creating backup", instance.create_backup
                )
                logger.log("SUCCESS", f"Backup saved to {backup_path}")
            except Exception as exc:
                logger.log("ERROR", f"Backup failed: {exc}")
            pause()
            continue
        if choice == "2":
            backups = instance.list_backups()
            if not backups:
                logger.log("INFO", "No backups are available.")
            else:
                for backup in backups:
                    print(f" {backup.name} ({format_bytes(backup.stat().st_size)})")
            pause()
            continue
        if choice == "3":
            backup_path = choose_backup(instance, logger)
            if not backup_path:
                pause()
                continue
            confirmation = input(
                "This will overwrite world data. Type RESTORE to continue: "
            ).strip()
            if confirmation != "RESTORE":
                logger.log("INFO", "Restore cancelled.")
                pause()
                continue
            try:
                run_with_spinner(
                    "Restoring backup", instance.restore_backup, backup_path.name
                )
            except Exception as exc:
                logger.log("ERROR", f"Restore failed: {exc}")
            pause()
            continue
        if choice == "4":
            backup_path = choose_backup(instance, logger)
            if not backup_path:
                pause()
                continue
            confirmation = input(f"Type DELETE to remove {backup_path.name}: ").strip()
            if confirmation != "DELETE":
                logger.log("INFO", "Deletion cancelled.")
                pause()
                continue
            try:
                instance.delete_backup(backup_path.name)
            except Exception as exc:
                logger.log("ERROR", f"Deletion failed: {exc}")
            pause()
            continue
        logger.log("ERROR", "Invalid world manager selection.")
        pause()


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
    print(f"{C.BOLD}Statistics for {current_server}{C.RESET}")
    print(f" Total sessions: {stats['total_sessions']}")
    print(f" Total uptime: {format_duration(stats['total_uptime'])}")
    print(f" Average session: {format_duration(stats['avg_duration'])}")
    print(f" Total crashes: {stats['total_crashes']}")
    print(f" Total restarts: {stats['total_restarts']}")
    print(f" Avg RAM usage (24h): {stats['avg_ram_usage_24h'] or 0:.2f}%")
    print(f" Avg CPU usage (24h): {stats['avg_cpu_usage_24h'] or 0:.2f}%")
    print(f" Peak players (24h): {stats['peak_players_24h'] or 0}")
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
    print(f"{C.CYAN}Attaching to {current_server}. Detach with Ctrl+A then D.{C.RESET}")
    time.sleep(1)
    try:
        subprocess.run(["screen", "-r", instance.screen_name], check=False)
    except FileNotFoundError:
        logger.log("ERROR", "screen is not installed.")
        pause()


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
    while True:
        print_header(current_server, runtime)
        print(f"{C.BOLD}Send command to {current_server}{C.RESET}")
        print(" Leave blank to return.")
        command = input("> ").strip()
        if not command:
            return
        success = instance.send_command(command)
        if not success:
            logger.log("ERROR", "Command delivery failed.")
        time.sleep(1)


def main() -> None:
    logger, config_manager, db_manager, runtime = create_services()
    if not check_base_dependencies(logger):
        raise SystemExit(1)

    while True:
        config = ensure_current_server(config_manager)
        if not config.get("servers"):
            print_header(None, runtime)
            logger.log("INFO", "No servers found. Create one to begin.")
            create_new_server(config_manager, logger)
            pause()
            continue

        current_server = config.get("current_server")
        if not current_server:
            logger.log("ERROR", "No current server is selected.")
            pause()
            continue

        instance = runtime.get_instance(current_server)
        server_config = config["servers"][current_server]
        flavor = server_config.get("server_flavor")
        flavor_name = SERVER_FLAVORS.get(flavor, {}).get("name", "Not installed")
        version = server_config.get("server_version") or "N/A"
        status = (
            f"{C.GREEN}{C.DOT_ON} ONLINE{C.RESET}"
            if instance.is_running()
            else f"{C.RED}{C.DOT_OFF} OFFLINE{C.RESET}"
        )

        print_header(current_server, runtime)
        print(f" {C.BOLD}{current_server}{C.RESET}  [{status}]")
        print(f" {C.DIM}{flavor_name} {version}{C.RESET}")
        print()
        print_connection_summary(instance)
        print()

        width = get_terminal_width()
        if width >= 65:
            items = [
                ("1", "Start server"),
                ("7", "World manager"),
                ("2", "Stop server"),
                ("8", "Send command"),
                ("3", "Install / update"),
                ("9", "Statistics"),
                ("4", "Configure server"),
                ("10", "Create new server"),
                ("5", "Edit properties & EULA"),
                ("11", "Switch server"),
                ("6", "Attach to console"),
                ("0", "Exit"),
            ]
            for i in range(0, len(items), 2):
                k1, t1 = items[i]
                k2, t2 = items[i + 1]
                left = f" [{k1:>2}] {t1}"
                right = f" [{k2:>2}] {t2}"
                print(f"{left:<30}{right}")
        else:
            print(" [ 1] Start server")
            print(" [ 2] Stop server")
            print(" [ 3] Install or update server")
            print(" [ 4] Configure server")
            print(" [ 5] Edit server.properties and eula.txt")
            print(" [ 6] Attach to console")
            print(" [ 7] World manager")
            print(" [ 8] Send command")
            print(" [ 9] Statistics")
            print(" [10] Create new server")
            print(" [11] Switch server")
            print(" [ 0] Exit")

        choice = input(f"\n{C.BOLD}Choose action [0-11]: {C.RESET}").strip()
        try:
            if choice == "1":
                started = instance.start()
                if started:
                    instance.print_connection_details()
                pause()
            elif choice == "2":
                force = input("Force stop? (y/N): ").strip().lower() == "y"
                instance.stop(force=force)
                pause()
            elif choice == "3":
                install_server(runtime, config_manager, logger)
                pause()
            elif choice == "4":
                configure_server(runtime, config_manager, logger)
            elif choice == "5":
                edit_server_files(runtime, config_manager, logger)
            elif choice == "6":
                show_console(runtime, config_manager, logger)
            elif choice == "7":
                world_manager(runtime, config_manager, logger)
            elif choice == "8":
                send_command_menu(runtime, config_manager, logger)
            elif choice == "9":
                show_statistics(runtime, config_manager, db_manager)
            elif choice == "10":
                create_new_server(config_manager, logger)
                pause()
            elif choice == "11":
                select_current_server(config_manager, logger)
                pause()
            elif choice == "0":
                if runtime.running_servers():
                    leave_running = (
                        input(
                            "Leave running servers active in screen after exit? (Y/n): "
                        )
                        .strip()
                        .lower()
                    )
                    if leave_running == "n":
                        for server_name in runtime.running_servers():
                            runtime.get_instance(server_name).stop()
                raise SystemExit(0)
            else:
                logger.log("ERROR", "Invalid menu selection.")
                pause()
        except KeyboardInterrupt as exc:
            raise SystemExit(0) from exc
        except Exception as exc:
            logger.log("CRITICAL", f"Unexpected error: {exc}")
            pause()


if __name__ == "__main__":
    main()
