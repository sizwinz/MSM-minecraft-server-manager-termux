"""Playit account API helpers for automated tunnel creation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests

from core.constants import CONFIG_DIR

PLAYIT_API_BASE_URL = "https://api.playit.gg"
PLAYIT_THIRD_PARTY_AUTH_URL = (
    "https://playit.gg/account/setup/wizard/new-account/third-party/"
    "third-party-select?partner=other"
)
PLAYIT_SESSION_FILE_NAME = "playit_session.json"
DEFAULT_PLAYIT_REGION = "global"


class PlayitApiError(RuntimeError):
    """Raised when the Playit account API returns a fail or error response."""


def _session_file() -> Path:
    return CONFIG_DIR / PLAYIT_SESSION_FILE_NAME


def save_playit_session(session_key: str) -> Path:
    path = _session_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session_key": session_key}, indent=2), encoding="utf-8"
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def load_playit_session() -> str | None:
    path = _session_file()
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    session_key = payload.get("session_key")
    return session_key if isinstance(session_key, str) and session_key else None


def _friendly_error(data: Any) -> str:
    if isinstance(data, dict):
        return (
            data.get("message") or data.get("error") or json.dumps(data, sort_keys=True)
        )
    return str(data)


def _successful_data(payload: dict[str, Any]) -> Any:
    status = payload.get("status")
    if status == "success":
        return payload.get("data")
    raise PlayitApiError(_friendly_error(payload.get("data", payload)))


def _account_headers(session_key: str | None) -> dict[str, str]:
    return {"Authorization": session_key} if session_key else {}


def _agent_headers(agent_secret: str | None) -> dict[str, str]:
    if not agent_secret:
        return {}
    token = agent_secret.strip()
    if not token.startswith("Agent-Secret ") and not token.startswith("Session "):
        token = f"Agent-Secret {token}"
    return {"Authorization": token}


def auto_provision_playit_tunnel(
    server_name: str,
    secret_key: str,
    flavor: str | None = None,
    protocol: str | None = "tcp",
    local_host: str = "127.0.0.1",
    local_port: int | str = 25565,
) -> tuple[str | None, str | None]:
    """Create or fetch tunnel for agent using agent_secret key."""
    try:
        client = PlayitApiClient(agent_secret=secret_key)
        rundata = client.agent_rundata()
        agent_id = (
            rundata.get("agent_id")
            or rundata.get("id")
            or (rundata.get("agent") or {}).get("id")
        )
        if not agent_id:
            return None, None
        return client.create_or_update_tunnel(
            server_name=server_name,
            agent_id=str(agent_id),
            flavor=flavor,
            protocol=protocol,
            local_host=local_host,
            local_port=local_port,
        )
    except Exception:
        return None, None


def _coerce_port(value: int | str | None) -> int:
    if value is None:
        raise ValueError("local_port is required")
    return int(value)


def _playit_tunnel_type(flavor: str | None) -> str:
    return "minecraft-bedrock" if flavor == "pocketmine" else "minecraft-java"


def _playit_port_type(flavor: str | None, protocol: str | None) -> str:
    if flavor == "pocketmine":
        return "udp"
    return "tcp" if protocol not in {"udp", "both"} else protocol


def build_tunnel_create_request(
    *,
    server_name: str,
    agent_id: str,
    flavor: str | None,
    protocol: str | None,
    local_host: str,
    local_port: int | str,
    region: str = DEFAULT_PLAYIT_REGION,
) -> dict[str, Any]:
    port_type = _playit_port_type(flavor, protocol)
    return {
        "name": f"MSM {server_name}",
        "tunnel_type": _playit_tunnel_type(flavor),
        "tunnel_description": f"Managed by MSM for {server_name}",
        "port_type": port_type,
        "port_count": 1,
        "origin": {
            "type": "agent",
            "data": {
                "agent_id": agent_id,
                "local_ip": local_host,
                "local_port": _coerce_port(local_port),
            },
        },
        "enabled": True,
        "alloc": {"type": "region", "details": {"region": region}},
        "firewall_id": None,
        "proxy_protocol": None,
    }


def build_tunnel_update_request(
    *,
    tunnel_id: str,
    agent_id: str,
    local_host: str,
    local_port: int | str,
) -> dict[str, Any]:
    return {
        "tunnel_id": tunnel_id,
        "local_ip": local_host,
        "local_port": _coerce_port(local_port),
        "agent_id": agent_id,
        "enabled": True,
    }


def extract_tunnel_endpoint(tunnel: dict[str, Any]) -> str | None:
    allocation = tunnel.get("alloc")
    if isinstance(allocation, dict) and allocation.get("status") == "allocated":
        data = allocation.get("data") or {}
        domain = data.get("assigned_domain") or data.get("ip_hostname")
        port = data.get("port_start")
        if domain and port:
            return f"{domain}:{port}"
        if domain:
            return str(domain)

    for address in tunnel.get("connect_addresses") or []:
        value = address.get("value") if isinstance(address, dict) else None
        if isinstance(value, dict):
            endpoint = value.get("address")
            default_port = value.get("default_port")
            if endpoint and default_port and ":" not in str(endpoint):
                return f"{endpoint}:{default_port}"
            if endpoint:
                return str(endpoint)
        elif isinstance(value, str):
            return value
    return None


def _matches_tunnel(
    tunnel: dict[str, Any],
    *,
    tunnel_id: str | None,
    name: str,
    agent_id: str,
) -> bool:
    if tunnel_id and tunnel.get("id") == tunnel_id:
        return True
    origin = tunnel.get("origin") or {}
    origin_data = origin.get("data") if isinstance(origin, dict) else {}
    return (
        tunnel.get("name") == name
        and isinstance(origin_data, dict)
        and origin_data.get("agent_id") == agent_id
    )


class PlayitApiClient:
    """Small wrapper around the unstable Playit account API."""

    def __init__(
        self,
        *,
        session_key: str | None = None,
        agent_secret: str | None = None,
        session: Any | None = None,
        base_url: str = PLAYIT_API_BASE_URL,
        timeout: int = 20,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        headers = _account_headers(session_key) or _agent_headers(agent_secret)
        self.session.headers.update(headers)

    def _call(self, path: str, request: dict[str, Any] | None = None) -> Any:
        try:
            response = self.session.post(
                f"{self.base_url}{path}",
                json=request or {},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            raise PlayitApiError(str(exc)) from exc
        except ValueError as exc:
            raise PlayitApiError("Playit returned invalid JSON.") from exc
        return _successful_data(payload)

    def login_apply(self, token: str) -> str:
        data = self._call("/login/apply", {"token": token.strip()})
        session_key = data.get("session_key") if isinstance(data, dict) else None
        if not session_key:
            raise PlayitApiError("Playit did not return a session key.")
        return session_key

    def agent_rundata(self) -> dict[str, Any]:
        data = self._call("/agents/rundata", {})
        if not isinstance(data, dict):
            raise PlayitApiError("Playit returned invalid agent run data.")
        return data

    def tunnels_list(self, agent_id: str | None = None) -> list[dict[str, Any]]:
        data = self._call("/tunnels/list", {"tunnel_id": None, "agent_id": agent_id})
        tunnels = data.get("tunnels") if isinstance(data, dict) else None
        return tunnels if isinstance(tunnels, list) else []

    def create_or_update_tunnel(
        self,
        *,
        server_name: str,
        agent_id: str,
        flavor: str | None,
        protocol: str | None,
        local_host: str,
        local_port: int | str,
        existing_tunnel_id: str | None = None,
        region: str = DEFAULT_PLAYIT_REGION,
    ) -> tuple[str, str | None]:
        name = f"MSM {server_name}"
        tunnels = self.tunnels_list(agent_id=agent_id)
        existing = next(
            (
                tunnel
                for tunnel in tunnels
                if _matches_tunnel(
                    tunnel,
                    tunnel_id=existing_tunnel_id,
                    name=name,
                    agent_id=agent_id,
                )
            ),
            None,
        )

        if existing:
            tunnel_id = str(existing["id"])
            self._call(
                "/tunnels/update",
                build_tunnel_update_request(
                    tunnel_id=tunnel_id,
                    agent_id=agent_id,
                    local_host=local_host,
                    local_port=local_port,
                ),
            )
            return tunnel_id, extract_tunnel_endpoint(existing)

        request = build_tunnel_create_request(
            server_name=server_name,
            agent_id=agent_id,
            flavor=flavor,
            protocol=protocol,
            local_host=local_host,
            local_port=local_port,
            region=region,
        )
        created = self._call("/tunnels/create", request)
        tunnel_id = created.get("id") if isinstance(created, dict) else None
        if not tunnel_id:
            raise PlayitApiError("Playit did not return a tunnel id.")

        refreshed = self.tunnels_list(agent_id=agent_id)
        created_tunnel = next(
            (tunnel for tunnel in refreshed if tunnel.get("id") == tunnel_id),
            None,
        )
        endpoint = extract_tunnel_endpoint(created_tunnel) if created_tunnel else None
        return str(tunnel_id), endpoint
