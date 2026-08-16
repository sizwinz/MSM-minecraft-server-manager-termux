<div align="center">

# MSM - Minecraft Server Manager

**High-performance, production-grade cross-platform Minecraft server manager.**  
*Multi-platform runtime • Process backend abstraction • Upstream binary lifecycle • Dual tunnel bridging • Telemetry*

<br/>

[![Version](https://img.shields.io/badge/version-6.0-22c55e?style=flat-square)](core/constants.py)
[![Python](https://img.shields.io/badge/python-3.10+-3b82f6?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/platform-Linux_%7C_Termux_%7C_Windows_%7C_macOS_%7C_WSL_%7C_FreeBSD-f97316?style=flat-square)](https://github.com/sizwinz/MSM-minecraft-server-manager-termux)
[![License](https://img.shields.io/badge/license-MIT-64748b?style=flat-square)](LICENSE)
[![Code Style](https://img.shields.io/badge/code%20style-black-000000?style=flat-square)](https://github.com/psf/black)
[![Linter](https://img.shields.io/badge/linter-flake8-4b5563?style=flat-square)](https://flake8.pycqa.org)

<br/>

<img src="assets/msm.png" alt="MSM Interactive Terminal Interface" width="450" />

<br/>
<br/>

[Platform Support](#platform-support-matrix) •
[Architecture](#architecture) •
[Supported Flavors](#supported-server-flavors) •
[Installation](#installation) •
[CLI Reference](#cli-reference) •
[Java & PHP Matrix](#runtime-compatibility-matrices) •
[Configuration](#configuration-reference) •
[Development & Testing](#development)

</div>

---

## Overview

MSM is a lightweight, zero-GUI management suite designed to deploy, operate, and supervise Minecraft server instances across diverse operating systems: from Android mobile devices running Termux to Linux distributions, Windows 10/11, macOS (Intel & Apple Silicon), WSL1/WSL2, and FreeBSD.

It orchestrates server binaries directly from upstream vendor APIs, abstracts process lifecycles across GNU `screen`, headless POSIX process groups, and Windows process trees, captures system telemetry into an atomic SQLite database, manages ZIP world backups with Zip-Slip attack mitigation, and provisions public tunnel endpoints via `playit` and `ngrok`.

---

## Platform Support Matrix

| Platform | Environments / Architectures | Process Backend | Console Mode | Java Provisioning | PocketMine (PMMP) |
|---|---|---|---|---|---|
| **Linux** | Debian, Ubuntu, Arch, Fedora, Alpine, openSUSE, Void (`x86_64`, `aarch64`, `armv7`) | `screen` or `native_posix` | Full Screen Attach / Live Log Tail | Adoptium JRE (8, 17, 21, 25) | Native CLI / PMMP PHP |
| **Android (Termux)** | Termux on Android 7+ (`aarch64`, `armv7`, `x86_64`) | `screen` | Full Screen Attach / Live Log Tail | Termux `pkg` (`tur-repo`) | Termux ZTS PHP |
| **Windows** | Windows 10 & 11 (`x86_64`, `arm64`) | `windows` | Live Log Tail & Command Dispatch | Adoptium JRE / Winget / PATH | Custom PMMP PHP Binaries |
| **macOS** | macOS 12+ (Intel `x86_64` & Apple Silicon `arm64`) | `screen` or `native_posix` | Full Screen Attach / Live Log Tail | Adoptium JRE / Homebrew | Custom PMMP PHP Binaries |
| **WSL (WSL1 / WSL2)** | Ubuntu, Debian, Arch on Windows Subsystem for Linux | `screen` or `native_posix` | Full Screen Attach / Live Log Tail | Adoptium JRE / System pkgs | System PHP |
| **FreeBSD** | FreeBSD 13+ (`amd64`, `aarch64`) | `native_posix` or `screen` | Screen Attach (if installed) / Log Tail | FreeBSD `pkg` (openjdk17/21) | FreeBSD `pkg` PHP |

---

## Architecture

```
                                +-----------------------------------+
                                |            msm.py CLI             |
                                |     (Interactive TUI Menus)       |
                                +-----------------+-----------------+
                                                  |
                                      +-----------v-----------+
                                      |    RuntimeManager     |
                                      +-----------+-----------+
                                                  |
                        +-------------------------+-------------------------+
                        |                                                   |
            +-----------v-----------+                           +-----------v-----------+
            | ServerInstance ("sv1")|                           | ServerInstance ("sv2")|
            +-----------+-----------+                           +-----------+-----------+
                        |                                                   |
      +-----------------+-----------------+               +-----------------+-----------------+
      |                 |                 |               |                 |                 |
+-----v-----+     +-----v-----+     +-----v-----+   +-----v-----+     +-----v-----+     +-----v-----+
|  Backend  |     |  Monitor  |     |  Tunnel   |   |  Backend  |     |  Monitor  |     |  Tunnel   |
| (Screen / |     |  Thread   |     |  Process  |   | (Screen / |     |  Thread   |     |  Process  |
|  POSIX /  |     |  (60s)    |     |(playit/   |   |  POSIX /  |     |  (60s)    |     |(playit/   |
|  Windows) |     +-----+-----+     | ngrok)    |   |  Windows) |     +-----+-----+     | ngrok)    |
| .msm.pid  |           |           +-----------+   | .msm.pid  |           |           +-----------+
+-----------+           |                           +-----------+           |
                        |                                                   |
                        +-------------------------+-------------------------+
                                                  |
                                      +-----------v-----------+
                                      |    DatabaseManager    |
                                      |  (SQLite in WAL Mode) |
                                      +-----------+-----------+
                                                  |
                                 +----------------v----------------+
                                 |         PathService             |
                                 |  - config.json (Atomic swap)    |
                                 |  - msm.db      (Metrics/Logs)   |
                                 |  - msm.log     (Rotating log)   |
                                 +---------------------------------+
```

---

## Supported Server Flavors

| Flavor | Runtime | Default Port | Min RAM | Upstream Binary Source |
|---|---|---|---|---|
| **PaperMC** | Java | `25565` | 512 MB | PaperMC v2 API: versioned build artifact |
| **Purpur** | Java | `25565` | 512 MB | Purpur API: latest build stream per version |
| **Folia** | Java | `25565` | 1024 MB | PaperMC v2 API (Folia project builds) |
| **Vanilla** | Java | `25565` | 512 MB | Mojang Official Version Manifest (Release / Snapshot) |
| **Fabric** | Java | `25565` | 768 MB | FabricMC Meta: loader + server installer |
| **Quilt** | Java | `25565` | 768 MB | QuiltMC Meta: multi-stage installer isolation |
| **PocketMine-MP** | PHP | `19132` | 256 MB | GitHub Releases: official `.phar` release |

---

## Runtime Compatibility Matrices

### Java Compatibility Matrix

MSM enforces a strict Java compatibility policy to prevent server crashes caused by JDK module system and reflection restrictions:

| Minecraft Version Range | Required Java | Compatible Runtime Policy |
|---|---|---|
| `<= 1.16.5` | **Java 8** | **Strict Exact Match Only**. Running Minecraft 1.16 or older on Java 17/21/25 is strictly rejected because legacy internal reflection fails. |
| `1.17` to `1.20.4` | **Java 17** | **Java 17 preferred**. Java 21 is permitted as a certified fallback with a warning. Java 8 is rejected. |
| `>= 1.20.5` | **Java 21** | **Java 21 preferred**. Java 25 is permitted as a certified fallback with a warning. Java 17 or older is rejected. |
| Modern / Future (`>= 26.x`) | **Java 25** | **Java 25 preferred**. Older Java runtimes are rejected. |

### PocketMine-MP PHP Matrix

- PocketMine-MP 5.x requires **PHP 8.2+ with ZTS (Zend Thread Safety) and the `pmmpthread` extension**.
- Standard non-ZTS system PHP builds will not start PocketMine. On Termux, MSM provisions ZTS PHP from `tur-repo`. On Windows and macOS, provision a certified PMMP binary build in `~/.config/msm/php/`.

---

## Installation

### Automated Install

#### Linux, macOS, FreeBSD, and Termux (Android)
```bash
curl -fsSL https://raw.githubusercontent.com/sizwinz/MSM-minecraft-server-manager-termux/main/install.sh | bash
```

#### Windows 10 / 11 (PowerShell)
```powershell
irm https://raw.githubusercontent.com/sizwinz/MSM-minecraft-server-manager-termux/main/install.ps1 | iex
```

---

## CLI Reference & Diagnostics

Run platform diagnostics in non-interactive / CI / debugging mode:

```bash
python msm.py --diagnostics
```

Output example:
```json
{
  "system": "Windows",
  "os_type": "windows",
  "variant": "standard",
  "architecture": "x86_64",
  "raw_arch": "AMD64",
  "release": "10.0.26100",
  "python_version": "3.12.0",
  "is_wsl": false,
  "is_termux": false,
  "capabilities": {
    "supported_backends": ["windows"],
    "default_backend": "windows",
    "supports_screen": false,
    "supports_posix_signals": false,
    "supports_windows_process_groups": true,
    "supports_pocketmine_binary": false,
    "supports_java_provisioning": true,
    "supports_console_attachment": false
  }
}
```

---

## Development & Verification

### Running Automated Checks

```bash
# Linting check
python -m flake8 --jobs=1 .

# Code style check
python -m black --check .

# Full automated test suite (94 tests)
python -m pytest

# Bytecode compilation verification
python -m compileall msm.py core db ui utils tests platforms process
```

---

## License

MSM is licensed under the [MIT License](LICENSE).
