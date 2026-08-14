from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "install.sh"


def _working_bash() -> str:
    candidates = [
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            result = subprocess.run(
                [candidate, "-c", "exit 0"],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return candidate
        except Exception:
            continue
    pytest.skip("a working bash executable is required for installer dry-run tests")


def _write_stub(bin_dir: Path, name: str, body: str = "") -> None:
    path = bin_dir / name
    script = "#!/bin/sh\n" + (body or "exit 0\n")
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)


def _run_installer(
    tmp_path: Path,
    fake_bin: Path,
    extra_env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    bash = _working_bash()
    install_dir = tmp_path / "MSM-minecraft-server-manager-termux"
    install_dir.mkdir(exist_ok=True)
    (install_dir / "msm.py").write_text("", encoding="utf-8")
    (install_dir / "requirements.txt").write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "MSM_INSTALL_DRY_RUN": "1",
        "MSM_INSTALL_DIR": str(install_dir),
        **extra_env,
    }
    return subprocess.run(
        [bash, str(INSTALLER)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_termux_install_does_not_require_sudo_or_use_apt_get(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("pkg", "git", "python", "chmod"):
        _write_stub(fake_bin, command)
    _write_stub(fake_bin, "id", "printf '10070\\n'\n")

    result = _run_installer(
        tmp_path, fake_bin, {"PREFIX": "/data/data/com.termux/files/usr"}
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "pkg update -y" in result.stdout
    assert "pkg upgrade -y" in result.stdout
    assert (
        "pkg install -y python git screen php python-psutil tur-repo playit"
    ) in result.stdout
    assert "sudo" not in result.stdout
    assert "apt-get" not in result.stdout


def test_debian_install_uses_sudo_only_for_system_packages(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in (
        "apt-get",
        "apt-cache",
        "curl",
        "gpg",
        "tee",
        "git",
        "python3",
        "chmod",
    ):
        _write_stub(fake_bin, command)
    _write_stub(fake_bin, "id", "printf '1000\\n'\n")
    _write_stub(fake_bin, "sudo", 'exec "$@"\n')

    result = _run_installer(tmp_path, fake_bin, {"PREFIX": ""})

    assert result.returncode == 0, result.stderr + result.stdout
    assert "sudo apt-get update -y" in result.stdout
    assert (
        "sudo apt-get install -y git screen python3 python3-pip "
        "python3-venv curl gnupg ca-certificates"
    ) in result.stdout
    assert "git clone" not in result.stdout
    assert "sudo git" not in result.stdout
    assert "sudo python" not in result.stdout


def test_installer_reuses_current_checkout(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("pkg", "git", "python", "chmod"):
        _write_stub(fake_bin, command)
    _write_stub(fake_bin, "id", "printf '10070\\n'\n")
    (tmp_path / "msm.py").write_text("", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("", encoding="utf-8")

    result = _run_installer(
        tmp_path, fake_bin, {"PREFIX": "/data/data/com.termux/files/usr"}
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Preparing MSM codebase" in result.stdout
    assert "git clone" not in result.stdout


def test_unsupported_platform_fails_with_clear_message(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for command in ("id", "sh", "seq", "uname"):
        _write_stub(fake_bin, command)
    _write_stub(fake_bin, "id", "printf '1000\\n'\n")

    result = _run_installer(
        tmp_path,
        fake_bin,
        {"PREFIX": "", "PATH": str(fake_bin)},
    )

    assert result.returncode != 0
    assert "Only Termux and Debian/Ubuntu/WSL are supported" in result.stdout
