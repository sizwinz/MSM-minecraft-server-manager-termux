from __future__ import annotations

from pathlib import Path

import pytest
from utils.playit_api import (
    PLAYIT_THIRD_PARTY_AUTH_URL,
    PlayitApiClient,
    PlayitApiError,
    build_tunnel_create_request,
    extract_tunnel_endpoint,
    load_playit_session,
    save_playit_session,
)


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected HTTP {self.status_code}")


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None, dict[str, str]]] = []
        self.headers: dict[str, str] = {}

    def post(self, url: str, json: dict | None = None, timeout: int | None = None):
        self.calls.append(("POST", url, json, dict(self.headers)))
        return self.responses.pop(0)


def test_login_apply_uses_third_party_code_and_returns_session_key() -> None:
    session = FakeSession(
        FakeResponse(
            {"status": "success", "data": {"session_key": "session-123", "auth": {}}}
        )
    )
    client = PlayitApiClient(session=session)

    session_key = client.login_apply("one-time-code")

    assert session_key == "session-123"
    assert PLAYIT_THIRD_PARTY_AUTH_URL.endswith("partner=other")
    assert session.calls[0][1] == "https://api.playit.gg/login/apply"
    assert session.calls[0][2] == {"token": "one-time-code"}


def test_playit_session_save_and_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("utils.playit_api.CONFIG_DIR", tmp_path)

    save_playit_session("session-abc")

    assert load_playit_session() == "session-abc"
    assert (tmp_path / "playit_session.json").exists()


def test_agent_rundata_uses_agent_secret_auth() -> None:
    session = FakeSession(
        FakeResponse(
            {"status": "success", "data": {"agent_id": "agent-id", "tunnels": []}}
        )
    )
    client = PlayitApiClient(agent_secret="agent-secret", session=session)

    data = client.agent_rundata()

    assert data["agent_id"] == "agent-id"
    assert session.calls[0][3]["Authorization"] == "Agent-Key agent-secret"


def test_claim_keeps_polling_setup_until_browser_approval(monkeypatch):
    monkeypatch.setattr("utils.playit_api.time.sleep", lambda _: None)
    session = FakeSession(
        *[
            FakeResponse({"status": "success", "data": state})
            for state in ("WaitingForUserVisit", "WaitingForUser", "UserAccepted")
        ],
        FakeResponse({"status": "success", "data": {"secret_key": "new-secret"}}),
    )
    statuses = []
    client = PlayitApiClient(session=session)
    assert client.wait_for_claim("code", on_status=statuses.append) == "new-secret"
    assert statuses == ["WaitingForUserVisit", "WaitingForUser", "UserAccepted"]
    assert [call[1].rsplit("/", 1)[1] for call in session.calls] == [
        "setup",
        "setup",
        "setup",
        "exchange",
    ]
    assert session.calls[0][2]["agent_type"] == "self-managed"


def test_claim_retries_exchange_after_approval_propagation_delay(monkeypatch):
    monkeypatch.setattr("utils.playit_api.time.sleep", lambda _: None)
    session = FakeSession(
        FakeResponse({"status": "success", "data": "UserAccepted"}),
        FakeResponse({"status": "fail", "data": "NotAccepted"}),
        FakeResponse({"status": "success", "data": "UserAccepted"}),
        FakeResponse({"status": "success", "data": {"secret_key": "new-secret"}}),
    )
    assert PlayitApiClient(session=session).wait_for_claim("code") == "new-secret"
    assert len(session.calls) == 4


def test_rejected_claim_does_not_exchange_credentials():
    session = FakeSession(FakeResponse({"status": "success", "data": "UserRejected"}))
    with pytest.raises(PlayitApiError, match="rejected"):
        PlayitApiClient(session=session).wait_for_claim("code")
    assert len(session.calls) == 1


def test_unapproved_claim_expires_without_real_waits(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("utils.playit_api.time.monotonic", lambda: clock[0])
    monkeypatch.setattr(
        "utils.playit_api.time.sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    session = FakeSession(
        *[
            FakeResponse({"status": "success", "data": "WaitingForUserVisit"})
            for _ in range(3)
        ]
    )
    with pytest.raises(PlayitApiError, match="timed out"):
        PlayitApiClient(session=session).wait_for_claim("code", timeout=3)
    assert len(session.calls) == 3
    assert all(call[1].endswith("/claim/setup") for call in session.calls)


@pytest.mark.parametrize("state", [None, {}, "unexpected"])
def test_claim_rejects_invalid_setup_response(state):
    session = FakeSession(FakeResponse({"status": "success", "data": state}))
    with pytest.raises(PlayitApiError, match="invalid claim status"):
        PlayitApiClient(session=session).wait_for_claim("code")


def test_build_tunnel_create_request_for_java_tcp() -> None:
    request = build_tunnel_create_request(
        server_name="survival",
        agent_id="agent-id",
        flavor="paper",
        protocol="tcp",
        local_host="127.0.0.1",
        local_port=25565,
    )

    assert request["name"] == "MSM survival"
    assert request["tunnel_type"] == "minecraft-java"
    assert request["port_type"] == "tcp"
    assert request["origin"]["type"] == "agent"
    assert request["origin"]["data"] == {
        "agent_id": "agent-id",
        "local_ip": "127.0.0.1",
        "local_port": 25565,
    }
    assert request["alloc"] == {"type": "region", "details": {"region": "global"}}


def test_build_tunnel_create_request_for_pocketmine_udp() -> None:
    request = build_tunnel_create_request(
        server_name="bedrock",
        agent_id="agent-id",
        flavor="pocketmine",
        protocol="tcp",
        local_host="127.0.0.1",
        local_port=19132,
    )

    assert request["tunnel_type"] == "minecraft-bedrock"
    assert request["port_type"] == "udp"


def test_create_or_update_tunnel_updates_existing_tunnel() -> None:
    session = FakeSession(
        FakeResponse(
            {
                "status": "success",
                "data": {
                    "tunnels": [
                        {
                            "id": "existing-id",
                            "name": "MSM survival",
                            "origin": {
                                "type": "agent",
                                "data": {"agent_id": "agent-id"},
                            },
                            "alloc": {
                                "status": "allocated",
                                "data": {
                                    "assigned_domain": "old.ply.gg",
                                    "port_start": 1234,
                                },
                            },
                        }
                    ]
                },
            }
        ),
        FakeResponse({"status": "success", "data": None}),
    )
    client = PlayitApiClient(session_key="session-key", session=session)

    tunnel_id, endpoint = client.create_or_update_tunnel(
        server_name="survival",
        agent_id="agent-id",
        flavor="paper",
        protocol="tcp",
        local_host="127.0.0.1",
        local_port=25565,
        existing_tunnel_id="existing-id",
    )

    assert tunnel_id == "existing-id"
    assert endpoint == "old.ply.gg:1234"
    assert session.calls[1][1] == "https://api.playit.gg/tunnels/update"
    assert session.calls[1][2]["tunnel_id"] == "existing-id"
    assert session.calls[1][2]["local_port"] == 25565


def test_create_or_update_tunnel_creates_when_missing() -> None:
    session = FakeSession(
        FakeResponse({"status": "success", "data": {"tunnels": []}}),
        FakeResponse({"status": "success", "data": {"id": "new-id"}}),
        FakeResponse(
            {
                "status": "success",
                "data": {
                    "tunnels": [
                        {
                            "id": "new-id",
                            "alloc": {
                                "status": "allocated",
                                "data": {
                                    "assigned_domain": "new.ply.gg",
                                    "port_start": 25565,
                                },
                            },
                        }
                    ]
                },
            }
        ),
    )
    client = PlayitApiClient(session_key="session-key", session=session)

    tunnel_id, endpoint = client.create_or_update_tunnel(
        server_name="survival",
        agent_id="agent-id",
        flavor="paper",
        protocol="tcp",
        local_host="127.0.0.1",
        local_port=25565,
    )

    assert tunnel_id == "new-id"
    assert endpoint == "new.ply.gg:25565"
    assert session.calls[1][1] == "https://api.playit.gg/tunnels/create"


def test_api_fail_status_raises_clear_error() -> None:
    session = FakeSession(
        FakeResponse({"status": "fail", "data": "RequiresVerifiedAccount"})
    )
    client = PlayitApiClient(session_key="session-key", session=session)

    with pytest.raises(PlayitApiError, match="RequiresVerifiedAccount"):
        client.tunnels_list()


def test_extract_tunnel_endpoint_prefers_domain_and_port() -> None:
    tunnel = {
        "alloc": {
            "status": "allocated",
            "data": {"assigned_domain": "mc.ply.gg", "port_start": 30123},
        }
    }

    assert extract_tunnel_endpoint(tunnel) == "mc.ply.gg:30123"
