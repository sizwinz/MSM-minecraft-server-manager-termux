# PowerShell Installer for MSM (Minecraft Server Manager)
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1

$ErrorActionPreference = "Stop"

function Write-Color([string]$text, [string]$color = "White", [switch]$NoNewline) {
    if ($NoNewline) {
        Write-Host $text -ForegroundColor $color -NoNewline
    } else {
        Write-Host $text -ForegroundColor $color
    }
}

function Show-Banner {
    Write-Host ""
    Write-Color "  __  __ ____  __  __ " "Cyan"
    Write-Color " |  \/  / ___||  \/  |  " "Cyan" -NoNewline
    Write-Color "Minecraft Server Manager" "White"
    Write-Color " | |\/| \___ \| |\/| |  " "Cyan" -NoNewline
    Write-Color "Windows Edition v6.0" "DarkGray"
    Write-Color " |_|  |_|____/|_|  |_| " "Cyan"
    Write-Color "--------------------------------------------------------" "Cyan"
    Write-Host ""
}

$RepoUrl = "https://github.com/sizwinz/MSM-minecraft-server-manager-termux.git"
$RepoDirName = "MSM-minecraft-server-manager-termux"

function Check-Python {
    Write-Host " [1/5] Checking Python environment..." -ForegroundColor Cyan
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
    Write-Host " [2/5] Checking Git..." -ForegroundColor Cyan
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        Write-Color "   Found: Git ($($git.Source))" "Green"
        return $git.Source
    } else {
        Write-Color " [!] Git was not found on your system." "Yellow"
        Write-Color "     You can install it automatically using Windows Package Manager:" "Gray"
        Write-Color "       winget install Git.Git" "Green"
        Write-Color "     Or download from: https://git-scm.com/" "Gray"
        throw "Git not installed."
    }
}

function Prepare-Checkout {
    Write-Host " [3/5] Preparing MSM codebase..." -ForegroundColor Cyan
    $currentMsm = Join-Path (Get-Location) "msm.py"
    $currentReq = Join-Path (Get-Location) "requirements.txt"
    if ((Test-Path $currentMsm) -and (Test-Path $currentReq)) {
        $targetDir = (Get-Location).Path
        Write-Color "   Using current directory: $targetDir" "Green"
        return $targetDir
    }

    $targetDir = Join-Path $env:USERPROFILE $RepoDirName
    if (-not ((Test-Path (Join-Path $targetDir "msm.py")) -and (Test-Path (Join-Path $targetDir "requirements.txt")))) {
        Write-Color "   Cloning MSM repository to: $targetDir" "Cyan"
        & git clone $RepoUrl $targetDir
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to clone repository from $RepoUrl"
        }
    } else {
        Write-Color "   Found existing installation at: $targetDir" "Green"
    }
    return $targetDir
}

function Setup-Venv([string]$pythonPath, [string]$installDir) {
    Write-Host " [4/5] Creating virtual environment (.venv)..." -ForegroundColor Cyan
    $venvDir = Join-Path $installDir ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if ((Test-Path $venvDir) -and (Test-Path $venvPython)) {
        Write-Color "   Existing .venv found at $venvDir" "Gray"
    } else {
        if (Test-Path $venvDir) {
            Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
        }
        & $pythonPath -m venv $venvDir
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create virtual environment"
        }
        Write-Color "   Created .venv at $venvDir" "Green"
    }
}

function Get-VenvPython([string]$installDir) {
    $candidates = @(
        (Join-Path $installDir ".venv\Scripts\python.exe"),
        (Join-Path $installDir ".venv\bin\python.exe"),
        (Join-Path $installDir ".venv\bin\python")
    )
    foreach ($cand in $candidates) {
        if (Test-Path $cand) {
            return $cand
        }
    }
    return $null
}

function Install-Dependencies([string]$installDir) {
    Write-Host " [5/5] Installing Python dependencies..." -ForegroundColor Cyan
    $venvPython = Get-VenvPython $installDir
    if (-not $venvPython) {
        throw "Virtual environment python executable not found in .venv\Scripts or .venv\bin"
    }

    & $venvPython -m pip install --upgrade pip --quiet
    $reqFile = Join-Path $installDir "requirements.txt"
    if (Test-Path $reqFile) {
        & $venvPython -m pip install -r $reqFile --quiet
    }
    Write-Color "   Dependencies installed successfully." "Green"
}

function Show-Success([string]$installDir) {
    $venvPython = Get-VenvPython $installDir
    Write-Host ""
    Write-Color "+--------------------------------------------------------+" "Green"
    Write-Color "|  MSM installed successfully on Windows!                |" "Green"
    Write-Color "|                                                        |" "Green"
    Write-Color "|  To launch MSM:                                        |" "Green"
    Write-Color "     cd '$installDir'" "Cyan"
    if (Test-Path (Join-Path $installDir ".venv\Scripts\Activate.ps1")) {
        Write-Color "     .\.venv\Scripts\Activate.ps1" "Cyan"
        Write-Color "     python msm.py" "Cyan"
    } else {
        Write-Color "     & '$venvPython' msm.py" "Cyan"
    }
    Write-Color "|                                                        |" "Green"
    Write-Color "|  Or run directly:                                      |" "Green"
    Write-Color "     & '$venvPython' '$installDir\msm.py'" "Cyan"
    Write-Color "+--------------------------------------------------------+" "Green"
    Write-Host ""
}

try {
    Show-Banner
    $py = Check-Python
    Check-Git | Out-Null
    $installDir = Prepare-Checkout
    Setup-Venv $py $installDir
    Install-Dependencies $installDir
    Show-Success $installDir
} catch {
    Write-Color "`n[ERROR] Installation failed: $_" "Red"
    exit 1
}
