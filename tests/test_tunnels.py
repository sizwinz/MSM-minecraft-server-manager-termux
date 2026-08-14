from __future__ import annotations

from utils.tunnels import (
    build_playit_claim_exchange_command,
    build_playit_claim_generate_command,
    build_playit_claim_url_command,
    build_playit_start_command,
    extract_last_non_empty_line,
    extract_playit_claim_url,
    extract_playit_public_endpoint,
)


def test_extract_last_non_empty_line_returns_last_non_blank_line():
    assert extract_last_non_empty_line("\n alpha \n\n beta \n") == "beta"


def test_build_playit_claim_generate_command_uses_generate_subcommand():
    assert build_playit_claim_generate_command("playit-cli") == [
        "playit-cli",
        "claim",
        "generate",
    ]


def test_build_playit_claim_url_command_uses_url_subcommand():
    assert build_playit_claim_url_command("playit-cli", "claim-code") == [
        "playit-cli",
        "claim",
        "url",
        "claim-code",
    ]


def test_build_playit_claim_exchange_command_uses_secret_path_and_exchange_subcommand():
    assert build_playit_claim_exchange_command(
        "playit-cli",
        "claim-code",
        secret_path=".msm.playit.secret",
    ) == [
        "playit-cli",
        "--secret_path",
        ".msm.playit.secret",
        "claim",
        "exchange",
        "claim-code",
    ]


def test_build_playit_start_command_uses_secret_path_and_start_subcommand():
    assert build_playit_start_command(
        "playit-cli", secret_path=".msm.playit.secret"
    ) == [
        "playit-cli",
        "--secret-path",
        ".msm.playit.secret",
    ]


def test_extract_playit_public_endpoint_from_tunnel_address_log():
    log_text = """
    INFO connected
    INFO tunnel_address=147.185.221.24:25565 local_address=127.0.0.1:25565
    """

    assert extract_playit_public_endpoint(log_text) == "147.185.221.24:25565"


def test_extract_playit_public_endpoint_from_hostname_log():
    log_text = """
    INFO tunnel ready
    INFO public endpoint is fancy-world.ply.gg:30123
    """

    assert extract_playit_public_endpoint(log_text) == "fancy-world.ply.gg:30123"


def test_extract_playit_public_endpoint_from_joinmc_log():
    log_text = """
    INFO tunnel active
    INFO custom domain diamond-craft.gl.joinmc.link
    """

    assert extract_playit_public_endpoint(log_text) == "diamond-craft.gl.joinmc.link"


def test_extract_playit_claim_url_from_log():
    log_text = """
    INFO visit https://playit.gg/claim/some-agent-token to link this device
    """

    assert (
        extract_playit_claim_url(log_text) == "https://playit.gg/claim/some-agent-token"
    )


def test_extract_playit_claim_url_rejects_non_claim_urls():
    log_text = """
    INFO unrelated https://playit.gg/docs/claiming
    INFO unrelated https://example.com/claim/some-agent-token
    """

    assert extract_playit_claim_url(log_text) is None
