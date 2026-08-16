# PowerShell Installer for MSM (Minecraft Server Manager)
# Usage: powershell -ExecutionPolicy Bypass -File install.ps1
#        irm https://raw.githubusercontent.com/sizwinz/MSM-minecraft-server-manager-termux/main/install.ps1 | iex

$ErrorActionPreference = "Stop"

try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

# ASCII-safe Unicode glyph definitions
$G_CHECK  = [char]0x2714 # checkmark
$G_CROSS  = [char]0x2716 # cross
$G_BULLET = [char]0x2022 # bullet
$G_H      = [char]0x2500 # horizontal line
$G_V      = [char]0x2502 # vertical line
$G_TL     = [char]0x256D # top-left rounded corner
$G_TR     = [char]0x256E # top-right rounded corner
$G_BL     = [char]0x2570 # bottom-left rounded corner
$G_BR     = [char]0x256F # bottom-right rounded corner
$G_L      = [char]0x251C # left tee
$G_R      = [char]0x2524 # right tee

function Write-Color([string]$text, [string]$color = "White", [switch]$NoNewline) {
    if ($NoNewline) {
        Write-Host $text -ForegroundColor $color -NoNewline
    } else {
        Write-Host $text -ForegroundColor $color
    }
}

function Show-Banner {
    $div = [string]$G_H * 60
    Write-Host ""
    Write-Color "  __  __ ____  __  __ " "Cyan"
    Write-Color " |  \/  / ___||  \/  |  " "Cyan" -NoNewline
    Write-Color "Minecraft Server Manager" "White"
    Write-Color " | |\/| \___ \| |\/| |  " "Cyan" -NoNewline
    Write-Color ("Windows Edition {0} v6.0" -f $G_BULLET) "DarkGray"
    Write-Color " |_|  |_|____/|_|  |_| " "Cyan"
    Write-Color $div "Cyan"
    Write-Host ""
}

function Write-StepHeader([int]$current, [int]$total, [string]$title) {
    Write-Color (" -> [{0}/{1}] {2}..." -f $current, $total, $title) "Cyan"
}

function Write-StepSuccess([int]$current, [int]$total, [string]$msg) {
    Write-Color ("  {0} [{1}/{2}] {3}" -f $G_CHECK, $current, $total, $msg) "Green"
}

function Write-SubInfo([string]$msg) {
    Write-Color ("    {0} {1}" -f $G_BULLET, $msg) "DarkGray"
}

$RepoUrl = "https://github.com/sizwinz/MSM-minecraft-server-manager-termux.git"
$RepoDirName = "MSM-minecraft-server-manager-termux"

function Check-Python {
    Write-StepHeader 1 5 "Checking Python environment"
    $py = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $py) {
        $py = Get-Command py.exe -ErrorAction SilentlyContinue
    }

    if (-not $py) {
        Write-Color "  [!] Python 3.10+ was not found on your system." "Yellow"
        Write-Color "      You can install it automatically using Windows Package Manager:" "Gray"
        Write-Color "        winget install Python.Python.3.12" "Green"
        Write-Color "      Or download from: https://www.python.org/downloads/" "Gray"
        throw "Python not installed."
    }

    $verOutput = (& $py.Source --version 2>&1).ToString().Trim()
    Write-StepSuccess 1 5 "$verOutput ($($py.Source))"
    return $py.Source
}

function Check-Git {
    Write-StepHeader 2 5 "Checking Git"
    $git = Get-Command git.exe -ErrorAction SilentlyContinue
    if ($git) {
        $gitVer = (& $git.Source --version 2>&1).ToString().Trim()
        Write-StepSuccess 2 5 "$gitVer ($($git.Source))"
        return $git.Source
    } else {
        Write-Color "  [!] Git was not found on your system." "Yellow"
        Write-Color "      You can install it automatically using Windows Package Manager:" "Gray"
        Write-Color "        winget install Git.Git" "Green"
        Write-Color "      Or download from: https://git-scm.com/" "Gray"
        throw "Git not installed."
    }
}

function Prepare-Checkout {
    Write-StepHeader 3 5 "Preparing MSM codebase"
    $currentMsm = Join-Path (Get-Location) "msm.py"
    $currentReq = Join-Path (Get-Location) "requirements.txt"
    if ((Test-Path $currentMsm) -and (Test-Path $currentReq)) {
        $targetDir = (Get-Location).Path
        Write-StepSuccess 3 5 "Using current directory: $targetDir"
        return $targetDir
    }

    $targetDir = Join-Path $env:USERPROFILE $RepoDirName
    if (-not ((Test-Path (Join-Path $targetDir "msm.py")) -and (Test-Path (Join-Path $targetDir "requirements.txt")))) {
        Write-SubInfo "Cloning repository from GitHub..."
        & git clone --quiet $RepoUrl $targetDir
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to clone repository from $RepoUrl"
        }
        Write-StepSuccess 3 5 "Cloned to $targetDir"
    } else {
        Write-StepSuccess 3 5 "Using existing installation at $targetDir"
    }
    return $targetDir
}

function Setup-Venv([string]$pythonPath, [string]$installDir) {
    Write-StepHeader 4 5 "Configuring virtual environment (.venv)"
    $venvDir = Join-Path $installDir ".venv"
    $venvPython = Join-Path $venvDir "Scripts\python.exe"
    if ((Test-Path $venvDir) -and (Test-Path $venvPython)) {
        Write-StepSuccess 4 5 "Virtual environment verified"
    } else {
        if (Test-Path $venvDir) {
            Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
        }
        Write-SubInfo "Initializing new virtualenv..."
        & $pythonPath -m venv $venvDir
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create virtual environment"
        }
        Write-StepSuccess 4 5 "Virtual environment created"
    }
}

function Install-Dependencies([string]$installDir) {
    Write-StepHeader 5 5 "Installing Python dependencies"
    $venvPython = Join-Path $installDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        throw "Virtual environment python executable not found at $venvPython"
    }

    Write-SubInfo "Upgrading pip and installing requirements.txt..."
    & $venvPython -m pip install --upgrade pip --quiet 2>$null
    $reqFile = Join-Path $installDir "requirements.txt"
    if (Test-Path $reqFile) {
        & $venvPython -m pip install -r $reqFile --quiet 2>$null
    }

    # Generate convenient launcher batch script
    $cmdLauncher = Join-Path $installDir "msm.cmd"
    $cmdContent = "@echo off`r`ncall `"%~dp0.venv\Scripts\activate.bat`"`r`npython `"%~dp0msm.py`" %*"
    [System.IO.File]::WriteAllText($cmdLauncher, $cmdContent, [System.Text.Encoding]::ASCII)

    Write-StepSuccess 5 5 "Dependencies installed and launcher configured"
}

function Show-SuccessCard([string]$installDir) {
    $boxWidth = [Math]::Max(66, $installDir.Length + 16)
    $line = [string]$G_H * ($boxWidth - 2)
    $topBorder = [string]$G_TL + $line + [string]$G_TR
    $midBorder = [string]$G_L + $line + [string]$G_R
    $botBorder = [string]$G_BL + $line + [string]$G_BR

    function Format-CardLine([string]$content) {
        $cleanContent = $content
        if ($cleanContent.Length -gt ($boxWidth - 4)) {
            $cleanContent = $cleanContent.Substring(0, $boxWidth - 7) + "..."
        }
        return [string]$G_V + " " + $cleanContent.PadRight($boxWidth - 4) + " " + [string]$G_V
    }

    Write-Host ""
    Write-Color $topBorder "Green"
    Write-Color (Format-CardLine "MSM installed successfully on Windows!") "Green"
    Write-Color $midBorder "Green"
    Write-Color (Format-CardLine "") "Green"
    Write-Color (Format-CardLine "To launch MSM:") "Green"
    Write-Color (Format-CardLine ("  1. cd `"{0}`"" -f $installDir)) "Cyan"
    Write-Color (Format-CardLine "  2. .\msm.cmd") "Cyan"
    Write-Color (Format-CardLine "") "Green"
    Write-Color (Format-CardLine "Or start directly via virtualenv:") "Gray"
    Write-Color (Format-CardLine "  .\.venv\Scripts\Activate.ps1 ; python msm.py") "DarkGray"
    Write-Color (Format-CardLine "") "Green"
    Write-Color $botBorder "Green"
    Write-Host ""
}

try {
    Show-Banner
    $py = Check-Python
    Check-Git | Out-Null
    $installDir = Prepare-Checkout
    Setup-Venv $py $installDir
    Install-Dependencies $installDir
    Show-SuccessCard $installDir
} catch {
    Write-Color "`n[ERROR] Installation failed: $_" "Red"
    exit 1
}
