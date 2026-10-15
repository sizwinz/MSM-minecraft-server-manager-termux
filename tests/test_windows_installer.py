"""Exercise installer error paths without installing software or touching accounts."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell installer checks")
@pytest.mark.parametrize(
    "failure", ["pip_upgrade", "requirements", "old_python", "old_venv"]
)
def test_installer_rejects_failed_dependencies_and_old_python(tmp_path, failure):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell unavailable")
    installer = Path(__file__).resolve().parents[1] / "install.ps1"
    (tmp_path / "requirements.txt").touch()
    (tmp_path / ".venv").mkdir()
    fake_python = tmp_path / "fake-python.ps1"
    fake_python.write_text(
        "if ($args -contains '--version') { 'Python 3.9.0'; $global:LASTEXITCODE=0 }\n"
        "elseif ($args -contains '-c') { $global:LASTEXITCODE=1 }\n"
        "elseif (($env:MSM_TEST_FAILURE -eq 'pip_upgrade' -and $args -contains '--upgrade') "
        "-or ($env:MSM_TEST_FAILURE -eq 'requirements' -and $args -contains '-r')) "
        "{ $global:LASTEXITCODE=1 } else { $global:LASTEXITCODE=0 }\n",
        encoding="utf-8",
    )
    harness = tmp_path / "check.ps1"
    harness.write_text(
        r"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:MSM_TEST_INSTALLER, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw "Installer parsing failed: $parseErrors" }
# Define functions only; never execute the installer's top-level installation.
foreach ($statement in $ast.EndBlock.Statements) {
    if ($statement -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
        Invoke-Expression $statement.Extent.Text
    }
}
function Join-Path($Path, $ChildPath) {
    if ($ChildPath -in @('.venv\Scripts\python.exe', 'Scripts\python.exe')) {
        return $env:MSM_TEST_PYTHON
    }
    Microsoft.PowerShell.Management\Join-Path $Path $ChildPath
}
function Get-Command { [pscustomobject]@{Source=$env:MSM_TEST_PYTHON} }
try {
    if ($env:MSM_TEST_FAILURE -eq 'old_python') { Check-Python }
    elseif ($env:MSM_TEST_FAILURE -eq 'old_venv') {
        Setup-Venv $env:MSM_TEST_PYTHON $env:MSM_TEST_DIR
    }
    else { Install-Dependencies $env:MSM_TEST_DIR }
    throw 'Expected installer to fail'
} catch {
    $expected = switch ($env:MSM_TEST_FAILURE) {
        'pip_upgrade' { 'Failed to upgrade pip' }
        'requirements' { 'Failed to install Python dependencies' }
        'old_python' { 'requires Python 3.10' }
        'old_venv' { 'Existing .venv requires Python 3.10' }
    }
    if ($_.ToString() -notlike "*$expected*") { throw }
    Write-Output 'EXPECTED_FAILURE_HANDLED'
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        env={
            **os.environ,
            "MSM_TEST_INSTALLER": str(installer),
            "MSM_TEST_PYTHON": str(fake_python),
            "MSM_TEST_DIR": str(tmp_path),
            "MSM_TEST_FAILURE": failure,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EXPECTED_FAILURE_HANDLED" in result.stdout
    assert not (tmp_path / "msm.cmd").exists()
