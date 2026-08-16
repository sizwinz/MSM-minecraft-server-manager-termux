# PowerShell Installer for MSM (Minecraft Server Manager)
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

function Write-Color([string]$text, [string]$color = "White") {
    Write-Host $text -ForegroundColor $color
}

function Show-Banner {
    Write-Host ""
    Write-Color "  __  __ ____  __  __ " "Cyan"
    Write-Color " |  \/  / ___||  \/  |  " "Cyan" -NoNewline
    Write-Color "Minecraft Server Manager" "White"
    Write-Color " | |\/| \___ \| |\/| |  " "Cyan" -NoNewline
    Write-Color "Windows Edition v6.0" "DarkGray"
    Write-Color " |_|  |_|____/|_|  |_| " "Cyan"
    Write-Color "────────────────────────────────────────────────────────" "Cyan"
    Write-Host ""
}

function Check-Python {
    Write-Host " [1/4] Checking Python environment..." -ForegroundColor Cyan
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $py) {
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
    }

    if (-not $py) {
        Write-Color " [!] Python 3.10+ was not found on your system." "Yellow"
        Write-Color "     You can install it automatically using Windows Package Manager:" "Gray"
        Write-Color "       winget install Python.Python.3.12" "Green"
        Write-Color "     Or download from: https://www.python.org/downloads/" "Gray"
        throw "Python not installed."
    }

    $verOutput = & $py.Source --version 2>&1
    Write-Color "   Found: $verOutput ($($py.Source))" "Green"
    return $py.Source
}

function Check-Git {
    Write-Host " [2/4] Checking Git..." -ForegroundColor Cyan
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        Write-Color "   Found: Git ($($git.Source))" "Green"
    } else {
        Write-Color "   Git was not found. Download it from https://git-scm.com/ or run:" "Yellow"
        Write-Color "     winget install Git.Git" "Green"
    }
}

function Setup-Venv([string]$pythonPath) {
    Write-Host " [3/4] Creating virtual environment (.venv)..." -ForegroundColor Cyan
    if (Test-Path ".venv") {
        Write-Color "   Existing .venv found." "Gray"
    } else {
        & $pythonPath -m venv .venv
        Write-Color "   Created .venv" "Green"
    }
}

function Install-Dependencies {
    Write-Host " [4/4] Installing Python dependencies..." -ForegroundColor Cyan
    $venvPython = Join-Path (Get-Location) ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Virtual environment python executable not found at $venvPython"
    }

    & $venvPython -m pip install --upgrade pip --quiet
    if (Test-Path "requirements.txt") {
        & $venvPython -m pip install -r requirements.txt --quiet
    }
    Write-Color "   Dependencies installed successfully." "Green"
}

function Show-Success {
    Write-Host ""
    Write-Color "╭────────────────────────────────────────────────────────╮" "Green"
    Write-Color "│  ✨ MSM installed successfully on Windows!             │" "Green"
    Write-Color "│                                                        │" "Green"
    Write-Color "│  To launch MSM:                                        │" "Green"
    Write-Color "│    .\.venv\Scripts\Activate.ps1                        │" "Cyan"
    Write-Color "│    python msm.py                                       │" "Cyan"
    Write-Color "│                                                        │" "Green"
    Write-Color "│  Or run directly:                                      │" "Green"
    Write-Color "│    .\.venv\Scripts\python.exe msm.py                   │" "Cyan"
    Write-Color "╰────────────────────────────────────────────────────────╯" "Green"
    Write-Host ""
}

try {
    Show-Banner
    $py = Check-Python
    Check-Git
    Setup-Venv $py
    Install-Dependencies
    Show-Success
} catch {
    Write-Color "`n[ERROR] Installation failed: $_" "Red"
    exit 1
}
