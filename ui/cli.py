"""Interactive CLI for Minecraft Server Manager."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

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
from ui.colors import C
from utils.logging_utils import EnhancedLogger
from utils.network import download_ngrok_binary, get_versions_for_flavor
from utils.ngrok import diagnose_ngrok, resolve_ngrok_binary
from utils.playit import (
    diagnose_playit,
    extract_playit_secret_from_file,
    read_playit_log_tail,
    resolve_playit_binary,
)
from utils.playit_api import (
    PLAYIT_THIRD_PARTY_AUTH_URL,
    PlayitApiClient,
    PlayitApiError,
    auto_provision_playit_tunnel,
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
    remove_file,
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


def print_header(current_server: str | None, runtime: RuntimeManager) -> None:
    clear_screen()
    width = get_terminal_width()
    divider = f"{C.PRIMARY}{C.BOX_H * width}{C.RESET}"

    system_info = get_system_info()
    running_servers = runtime.running_servers()
    ram_usage = f"{system_info['available_ram_mb']}MB / {system_info['total_ram_mb']}MB"
    cpu_info = f"{system_info['cpu_count']} cores @ {system_info['cpu_usage']:.1f}%"

    print(
        f"{C.BOLD}{C.PRIMARY}  MSM{C.RESET} "
        f"{C.DIM}• Minecraft Server Manager v{VERSION}{C.RESET}"
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
        prompt = "Download and install ngrok automatically? (Y/n): "
        if input(prompt).strip().lower() != "n":
            logger.log("INFO", "Downloading ngrok...")
            downloaded_path = download_ngrok_binary(logger=logger)
            if downloaded_path:
                logger.log("SUCCESS", f"Ngrok installed to {downloaded_path}")
                resolved_binary = str(downloaded_path)
                binary_path = str(downloaded_path)
            else:
                logger.log("ERROR", "Auto-installation failed.")
        else:
            custom_input = input("Enter custom ngrok binary path: ").strip()
            if custom_input:
                resolved_binary = resolve_ngrok_binary(custom_input)
                binary_path = resolved_binary or custom_input

    authtoken = input("\nNgrok authtoken (press Enter to keep existing): ").strip()
    if authtoken and resolved_binary:
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


def playit_setup_wizard(
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
        stored_provider == "playit"
        and stored_binary
        and "ngrok" not in Path(stored_binary).name.lower()
    ):
        candidate = stored_binary
    else:
        candidate = "playit"

    resolved_binary = resolve_playit_binary(candidate) or resolve_playit_binary(
        "playit"
    )
    binary_path = resolved_binary or "playit-cli"

    print_header(current_server, runtime)
    print(f" {C.BOLD}Playit.gg Fast Setup:{C.RESET} {current_server}\n")
    print_connection_summary(instance)
    print()

    if not resolved_binary:
        logger.log(
            "WARNING",
            "Playit binary was not found on this device.",
        )
        if running_on_termux():
            prompt = "Install playit automatically via Termux package manager? (Y/n): "
            if input(prompt).strip().lower() != "n":
                logger.log("INFO", "Installing playit...")
                run_command(
                    ["pkg", "install", "-y", "tur-repo"],
                    logger=logger,
                    check=False,
                    capture_output=True,
                )
                run_command(
                    ["pkg", "install", "-y", "playit"],
                    logger=logger,
                    check=False,
                    capture_output=True,
                )
                resolved_binary = resolve_playit_binary("playit")
                if resolved_binary:
                    logger.log("SUCCESS", f"Playit installed at {resolved_binary}")
                    binary_path = resolved_binary
        else:
            custom_input = input("Enter custom playit binary path: ").strip()
            if custom_input:
                resolved_binary = resolve_playit_binary(custom_input)
                binary_path = resolved_binary or custom_input

    if resolved_binary:
        if instance.playit_secret_file.exists():
            print(
                f" {C.GREEN}✓{C.RESET} Existing Playit credentials found on this server."
            )
            relink = (
                input(
                    f" {C.BOLD}Do you want to re-link a fresh Playit agent? (y/N): {C.RESET}"
                )
                .strip()
                .lower()
            )
            if relink == "y":
                instance.stop_tunnel()
                remove_file(instance.playit_secret_file)
                remove_file(instance.playit_endpoint_file)
                remove_file(instance.server_dir / ".msm.playit.log")
                logger.log(
                    "INFO", "Cleared previous agent secret. Starting fresh linking..."
                )

    if resolved_binary and Path(resolved_binary).name == "playit-cli":
        parent = Path(resolved_binary).parent
        if (parent / "playit").exists():
            resolved_binary = str(parent / "playit")
        elif (parent / "playitd").exists():
            resolved_binary = str(parent / "playitd")
        else:
            resolved_binary = (
                shutil.which("playit") or shutil.which("playitd") or resolved_binary
            )
    binary_path = resolved_binary or "playit"

    if resolved_binary and not instance.playit_secret_file.exists():
        secret_file = instance.playit_secret_file
        socket_file = instance.server_dir / ".msm.playit.sock"
        if socket_file.exists():
            socket_file.unlink(missing_ok=True)

        log_path = instance.server_dir / ".msm.playit.log"
        log_handle = log_path.open("w", encoding="utf-8")

        daemon_cmd = [
            resolved_binary,
            "--secret-path",
            str(secret_file),
            "--socket-path",
            str(socket_file),
        ]
        daemon = subprocess.Popen(
            daemon_cmd,
            cwd=instance.server_dir,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )

        try:
            time.sleep(1)
            if daemon.poll() is not None:
                log_handle.flush()
                tail = read_playit_log_tail(instance.server_dir)
                logger.log("ERROR", f"Playit daemon exited: {tail}")

            claim_url = None
            for _ in range(6):
                time.sleep(0.5)
                tail = read_playit_log_tail(instance.server_dir)
                claim_url = extract_playit_claim_url(tail)
                if claim_url:
                    break

            cli_binary = resolved_binary
            claim_code = None
            if not claim_url:
                parent = Path(resolved_binary).parent
                possible_cli = parent / "playit-cli"
                if possible_cli.exists():
                    cli_binary = str(possible_cli)
                else:
                    cli_binary = shutil.which("playit-cli") or resolved_binary

                for _ in range(10):
                    claim_gen = run_command(
                        build_playit_claim_generate_command(
                            cli_binary, socket_path=socket_file
                        ),
                        logger=logger,
                        check=False,
                        capture_output=True,
                        cwd=instance.server_dir,
                    )
                    if claim_gen and claim_gen.stdout:
                        raw = extract_last_non_empty_line(claim_gen.stdout)
                        if (
                            raw
                            and not raw.startswith("Error")
                            and not raw.startswith("error")
                        ):
                            claim_code = raw.split()[-1]
                            break
                    time.sleep(0.5)

                if claim_code:
                    url_res = run_command(
                        build_playit_claim_url_command(
                            cli_binary, claim_code, socket_path=socket_file
                        ),
                        logger=logger,
                        check=False,
                        capture_output=True,
                        cwd=instance.server_dir,
                    )
                    if url_res and url_res.stdout:
                        claim_url = extract_playit_claim_url(
                            url_res.stdout
                        ) or extract_last_non_empty_line(url_res.stdout)

            if claim_url:
                width = min(get_terminal_width(), 70)
                print(f" {C.CYAN}┌{'─' * (width - 4)}┐{C.RESET}")
                print(f" {C.CYAN}│{C.RESET} {C.BOLD}Playit Claim URL:{C.RESET}")
                print(f" {C.CYAN}│{C.RESET} {C.UNDERLINE}{claim_url}{C.RESET}")
                print(f" {C.CYAN}└{'─' * (width - 4)}┘{C.RESET}\n")
                print(
                    f" {C.DIM}Open the URL in your browser and click 'Add Agent'.{C.RESET}"
                )
            elif claim_code:
                logger.log("INFO", f"Claim code: {claim_code}")
            else:
                tail = read_playit_log_tail(instance.server_dir, line_count=5)
                logger.log(
                    "WARNING",
                    f"Waiting for Playit agent to produce claim link... {tail}",
                )

            step = input(
                "\nOpen the URL, press Enter and accept the agent in the website (or 's' to skip): "
            ).strip()
            if step.lower() != "s":
                if claim_code:
                    exchange_res = run_command(
                        build_playit_claim_exchange_command(
                            cli_binary,
                            claim_code,
                            secret_path=secret_file,
                            socket_path=socket_file,
                        ),
                        logger=logger,
                        check=False,
                        capture_output=True,
                        cwd=instance.server_dir,
                    )
                    if exchange_res:
                        out = (
                            f"{exchange_res.stdout or ''}\n"
                            f"{exchange_res.stderr or ''}"
                        ).strip()
                        for line in out.splitlines():
                            line_str = line.strip()
                            if "secret" in line_str.lower() and ":" in line_str:
                                sec_val = line_str.split(":", 1)[1].strip()
                                if sec_val and not sec_val.startswith("{"):
                                    write_text_file(secret_file, sec_val)
                                    break
                            elif (
                                len(line_str) in (32, 64)
                                and not line_str.startswith("Error")
                                and not line_str.startswith("error")
                            ):
                                write_text_file(secret_file, line_str)
                                break

                for _ in range(10):
                    if secret_file.exists() and secret_file.stat().st_size > 0:
                        break
                    global_toml = Path.home() / ".config" / "playit_gg" / "playit.toml"
                    if global_toml.exists():
                        toml_sec = extract_playit_secret_from_file(global_toml)
                        if toml_sec:
                            write_text_file(secret_file, toml_sec)
                            break
                    time.sleep(0.5)

                if secret_file.exists() and secret_file.stat().st_size > 0:
                    logger.log(
                        "SUCCESS",
                        f"Stored playit secret for {current_server}.",
                    )
                    sec_token = read_text_file(secret_file)
                    if sec_token:
                        cfg = config_manager.load()
                        srv_cfg = cfg.get("servers", {}).get(current_server, {})
                        srv_flavor = srv_cfg.get("server_flavor")
                        srv_port = int(
                            srv_cfg.get("server_settings", {}).get("port", 25565)
                        )
                        _tid, ep = auto_provision_playit_tunnel(
                            current_server,
                            sec_token,
                            flavor=srv_flavor,
                            local_port=srv_port,
                        )
                        if ep:
                            write_text_file(instance.playit_endpoint_file, ep)
                            logger.log(
                                "SUCCESS",
                                f"Created Playit tunnel for {current_server}: {ep}",
                            )
                else:
                    logger.log(
                        "ERROR",
                        "Playit account linking not completed yet.",
                    )
        finally:
            daemon.terminate()
            try:
                daemon.wait(timeout=2)
            except Exception:
                daemon.kill()
            log_handle.close()

    save_tunnel_config(
        instance,
        config_manager,
        current_server,
        provider="playit",
        binary_path=str(binary_path),
        enabled=True,
        logger=logger,
    )
    logger.log("SUCCESS", f"Playit tunnel enabled for {current_server}.")
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

        choice = input(f"\n{C.BOLD}Choose setting [0-5]: {C.RESET}").strip()
        if choice == "0":
            return
        if choice == "1":
            print(f"\nSupported Providers: {', '.join(SUPPORTED_TUNNEL_PROVIDERS)}")
            val = (
                input(f"Provider [{tunnel.get('provider', 'playit')}]: ")
                .strip()
                .lower()
            )
            if val in SUPPORTED_TUNNEL_PROVIDERS:

                def update_prov(s: dict) -> None:
                    s["servers"][current_server]["tunnel"]["provider"] = val

                config_manager.mutate(update_prov)
                logger.log("SUCCESS", f"Tunnel provider set to {val}.")
            else:
                logger.log("ERROR", "Unsupported provider.")
            pause()
        elif choice == "2":
            val = input("Protocol (tcp/udp) [tcp]: ").strip().lower() or "tcp"
            if val in SUPPORTED_TUNNEL_PROTOCOLS:

                def update_prot(s: dict) -> None:
                    s["servers"][current_server]["tunnel"]["protocol"] = val

                config_manager.mutate(update_prot)
                logger.log("SUCCESS", f"Protocol set to {val}.")
            else:
                logger.log("ERROR", "Unsupported protocol.")
            pause()
        elif choice == "3":
            val = input("Local host [127.0.0.1]: ").strip() or "127.0.0.1"

            def update_host(s: dict) -> None:
                s["servers"][current_server]["tunnel"]["local_host"] = val

            config_manager.mutate(update_host)
            logger.log("SUCCESS", f"Local host set to {val}.")
            pause()
        elif choice == "4":
            raw = input("Local port (number or 'auto') [auto]: ").strip()
            val = int(raw) if raw.isdigit() else None

            def update_port(s: dict) -> None:
                s["servers"][current_server]["tunnel"]["local_port"] = val

            config_manager.mutate(update_port)
            logger.log("SUCCESS", "Local port updated.")
            pause()
        elif choice == "5":
            val = input("Binary path (leave empty for default): ").strip()
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
        config = config_manager.load()
        server_config = config["servers"][current_server]
        tunnel = server_config.setdefault("tunnel", {})
        provider = tunnel.get("provider", "playit")
        enabled = tunnel.get("enabled", False)
        status_text = (
            f"{C.GREEN}{C.DOT_ON} Enabled ({provider}){C.RESET}"
            if enabled
            else f"{C.DIM}{C.DOT_OFF} Disabled{C.RESET}"
        )

        print_header(current_server, runtime)
        print(f" {C.BOLD}Public Tunnel & Remote Access:{C.RESET} {current_server}\n")
        print(f"  {C.DIM}Tunnel Status:{C.RESET} {status_text}")
        print_connection_summary(instance)
        print()

        print(" [ 1] Start / Restart Tunnel")
        print(" [ 2] Stop Tunnel")
        print(" [ 3] Setup Playit.gg (Recommended - Free & Fast)")
        print(" [ 4] Setup Ngrok")
        print(" [ 5] Test & Diagnose Tunnel Health")
        print(" [ 6] Advanced Tunnel Settings (Host, Port, Protocol, Binary)")
        print(" [ 7] Reset / Re-link Agent (Delete Stored Secrets)")
        if enabled:
            print(" [ 8] Disable Tunnel")
        else:
            print(" [ 8] Enable Tunnel")
        print(" [ 0] Back")

        choice = input(f"\n{C.BOLD}Choose action [0-8]: {C.RESET}").strip()
        if choice == "0":
            return
        if choice == "1":
            instance.restart_tunnel()
            pause()
            continue
        if choice == "2":
            instance.stop_tunnel()
            logger.log("SUCCESS", f"Stopped tunnel for {current_server}.")
            pause()
            continue
        if choice == "3":
            playit_setup_wizard(runtime, config_manager, current_server, logger)
            continue
        if choice == "4":
            ngrok_setup_wizard(runtime, config_manager, current_server, logger)
            continue
        if choice == "5":
            tunnel_diagnostics_screen(
                runtime, config_manager, current_server, logger, provider=provider
            )
            continue
        if choice == "6":
            configure_tunnel_advanced(runtime, config_manager, current_server, logger)
            continue
        if choice == "7":
            confirm = (
                input(
                    f"\n{C.YELLOW}Reset tunnel agent and delete saved "
                    f"credentials for {current_server}? (y/N): {C.RESET}"
                )
                .strip()
                .lower()
            )
            if confirm == "y":
                instance.stop_tunnel()
                remove_file(instance.playit_secret_file)
                remove_file(instance.playit_endpoint_file)
                remove_file(instance.server_dir / ".msm.ngrok.endpoint")
                remove_file(instance.server_dir / ".msm.playit.log")
                remove_file(instance.server_dir / ".msm.ngrok.log")
                logger.log(
                    "SUCCESS",
                    f"Reset tunnel credentials and state for {current_server}.",
                )
            pause()
            continue
        if choice == "8":
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
            pause()
            continue
        logger.log("ERROR", "Invalid tunnel manager selection.")
        pause()


def tunnel_setup_wizard(
    runtime: RuntimeManager,
    config_manager: ConfigManager,
    current_server: str,
    logger,
) -> None:
    tunnel_manager_menu(runtime, config_manager, current_server, logger)


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
        settings = server_config.get("server_settings", {})
        rcon = server_config.get("rcon", {})

        eula_path = instance.server_dir / EULA_FILE
        eula_val = load_properties(eula_path).get("eula", "false").lower() == "true"
        eula_display = (
            f"{C.GREEN}Accepted{C.RESET}"
            if eula_val
            else f"{C.RED}Not Accepted{C.RESET}"
        )
        online_val = str(settings.get("online-mode", "true")).lower() == "true"
        online_display = (
            f"{C.GREEN}True{C.RESET} (Online)"
            if online_val
            else f"{C.YELLOW}False{C.RESET} (Offline/Cracked)"
        )
        rcon_val = rcon.get("enabled", False)
        rcon_display = (
            f"{C.GREEN}Enabled{C.RESET}" if rcon_val else f"{C.DIM}Disabled{C.RESET}"
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

        choice = input(f"\n{C.BOLD}Choose setting [0-10]: {C.RESET}").strip()
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
                value = input("MOTD: ").strip()

                def update_motd(saved_config: dict) -> None:
                    saved_config["servers"][current_server]["server_settings"][
                        "motd"
                    ] = value

                updater = update_motd
            elif choice == "4":
                value = int(input("Max players: ").strip())

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
                value = input("RCON password: ").strip()

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
        config = config_manager.load()
        server_config = config["servers"][current_server]
        backup_cfg = server_config.get("backup_settings", {})
        backup_enabled = backup_cfg.get("enabled", False)
        backup_interval = backup_cfg.get("interval_hours", 6)
        sched_text = (
            f"Every {backup_interval}h ({C.GREEN}Active{C.RESET})"
            if backup_enabled
            else f"{C.DIM}Disabled{C.RESET}"
        )

        backups = instance.list_backups()

        print_header(current_server, runtime)
        print(f" {C.BOLD}World & Backup Manager:{C.RESET} {current_server}\n")
        print(f"  {C.DIM}Saved Backups :{C.RESET} {len(backups)}")
        print(f"  {C.DIM}Auto-Backup   :{C.RESET} {sched_text}")
        print()
        print(" [ 1] Create World Backup Now")
        print(" [ 2] List All Backups")
        print(" [ 3] Restore World from Backup")
        print(" [ 4] Delete a Backup")
        print(" [ 5] Configure Auto-Backup Schedule")
        print(" [ 0] Back")

        choice = input(f"\n{C.BOLD}Choose action [0-5]: {C.RESET}").strip()
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
            if not backups:
                logger.log("INFO", "No backups are available.")
            else:
                print(f"\n{C.BOLD}Available Backups:{C.RESET}")
                for backup in backups:
                    size_str = format_bytes(backup.stat().st_size)
                    print(f"  {C.CYAN}•{C.RESET} {backup.name} ({size_str})")
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
        if choice == "5":
            prompt = (
                f"Enable scheduled auto-backups? "
                f"({'y/N' if backup_enabled else 'Y/n'}): "
            )
            toggle = input(prompt).strip().lower()
            new_enabled = (toggle != "n") if not backup_enabled else (toggle == "y")
            interval_raw = input(
                f"Backup interval in hours [{backup_interval}]: "
            ).strip()
            try:
                new_interval = float(interval_raw) if interval_raw else backup_interval
            except ValueError:
                new_interval = backup_interval

            def update_sched(s: dict) -> None:
                b = s["servers"][current_server].setdefault("backup_settings", {})
                b["enabled"] = new_enabled
                b["interval_hours"] = new_interval

            config_manager.mutate(update_sched)
            logger.log("SUCCESS", "Auto-backup schedule updated.")
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
        f"{C.YELLOW}TIP: To detach without stopping the server, press Ctrl+A then D.{C.RESET}"
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
    print(f"    Data Dir   : {path_service.data_dir}")
    print(f"    Servers Dir: {path_service.servers_dir}")
    print(f"    Logs Dir   : {path_service.logs_dir}")

    if caps.notes:
        print(f"\n  {C.BOLD}Diagnostic Notes:{C.RESET}")
        for note in caps.notes:
            print(f"    • {note}")

    print()
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
        print_header(current_server, runtime)
        is_running = instance.is_running()
        status_text = (
            f"{C.GREEN}{C.DOT_ON} ONLINE{C.RESET}"
            if is_running
            else f"{C.RED}{C.DOT_OFF} OFFLINE{C.RESET}"
        )
        print(
            f" {C.BOLD}Live Console Viewer:{C.RESET} {current_server} [{status_text}]"
        )
        print(f" {C.DIM}Log path: {log_file}{C.RESET}\n")

        lines: list[str] = []
        if log_file.exists():
            try:
                raw_lines = log_file.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                lines = raw_lines[-25:]
            except Exception as e:
                lines = [f"[Error reading log: {e}]"]
        else:
            lines = ["[No log output generated yet]"]

        width = min(get_terminal_width(), 100)
        print(f"{C.PRIMARY}{'─' * width}{C.RESET}")
        for line in lines:
            if "ERROR" in line or "Exception" in line:
                print(f"{C.RED}{line}{C.RESET}")
            elif "WARN" in line:
                print(f"{C.YELLOW}{line}{C.RESET}")
            elif "INFO" in line:
                print(f"{C.CYAN}{line[:11]}{C.RESET}{line[11:]}")
            else:
                print(f"{C.DIM}{line}{C.RESET}")
        print(f"{C.PRIMARY}{'─' * width}{C.RESET}\n")

        print(" [Enter] Refresh logs   [s] Send command   [a] Attach screen   [0] Back")
        action = input(f"\n{C.BOLD}Action: {C.RESET}").strip().lower()
        if action in ("0", "b", "q"):
            return
        elif action == "s":
            send_command_menu(runtime, config_manager, logger)
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
    print(" (Leave blank and press Enter to return)")
    command = input("\n> ").strip()
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
    config = ensure_current_server(config_manager)
    current_server = config.get("current_server")
    if not current_server:
        return
    instance = runtime.get_instance(current_server)

    while True:
        print_header(current_server, runtime)
        is_running = instance.is_running()
        status_text = (
            f"{C.GREEN}{C.DOT_ON} ONLINE{C.RESET}"
            if is_running
            else f"{C.RED}{C.DOT_OFF} OFFLINE{C.RESET}"
        )
        print(
            f" {C.BOLD}Server Console & Logs:{C.RESET} {current_server} [{status_text}]\n"
        )
        print(" [ 1] View Live Console Logs (Mobile-friendly tail)")
        print(" [ 2] Send Single Command to Server")
        print(" [ 3] Attach to Interactive Screen Terminal")
        print(" [ 0] Back")

        choice = input(f"\n{C.BOLD}Choose action [0-3]: {C.RESET}").strip()
        if choice == "0":
            return
        if choice == "1":
            view_live_console(runtime, config_manager, logger)
        elif choice == "2":
            send_command_menu(runtime, config_manager, logger)
        elif choice == "3":
            show_console(runtime, config_manager, logger)
        else:
            logger.log("ERROR", "Invalid choice.")
            pause()


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

        choice = input(f"\n{C.BOLD}Choose action [0-3]: {C.RESET}").strip()
        if choice == "0":
            return
        if choice == "1":
            select_current_server(config_manager, logger)
            pause()
        elif choice == "2":
            create_new_server(config_manager, logger)
            pause()
        elif choice == "3":
            show_platform_diagnostics(runtime, config_manager, logger)
        else:
            logger.log("ERROR", "Invalid choice.")
            pause()


def main() -> None:
    if "--diagnostics" in sys.argv or "-d" in sys.argv:
        import json
        from platforms.detector import detect_platform

        desc = detect_platform()
        print(json.dumps(desc.to_dict(), indent=2))
        return

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

        print(f" {C.PRIMARY}── Controls ──────────────────────────────────{C.RESET}")
        print(" [ 1] Start server")
        print(" [ 2] Stop server")
        print(" [ 3] Server console (Live console / Send command)")
        print()
        print(f" {C.PRIMARY}── Configuration & Access ────────────────────{C.RESET}")
        print(" [ 4] Server settings (RAM, Port, MOTD, EULA, Properties)")
        print(" [ 5] Public tunnel (Playit.gg & Ngrok manager)")
        print(" [ 6] World & backups (Backup, Restore, Schedule)")
        print()
        print(f" {C.PRIMARY}── Management ────────────────────────────────{C.RESET}")
        print(" [ 7] Install / update server flavor & version")
        print(" [ 8] Switch / create server")
        print(" [ 9] Statistics & Performance")
        print(" [10] Platform & System Diagnostics")
        print(" [ 0] Exit")

        choice = input(f"\n{C.BOLD}Choose action [0-10]: {C.RESET}").strip()
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
                server_console_menu(runtime, config_manager, logger)
            elif choice == "4":
                configure_server(runtime, config_manager, logger)
            elif choice == "5":
                tunnel_manager_menu(runtime, config_manager, current_server, logger)
            elif choice == "6":
                world_manager(runtime, config_manager, logger)
            elif choice == "7":
                install_server(runtime, config_manager, logger)
                pause()
            elif choice == "8":
                server_management_menu(runtime, config_manager, logger)
            elif choice == "9":
                show_statistics(runtime, config_manager, db_manager)
            elif choice == "10" or choice.lower() == "d":
                show_platform_diagnostics(runtime, config_manager, logger)
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
