<div align="center">

# ⛏️ MSM — Minecraft Server Manager v6.0

**Terminal-native server management for Termux and Linux.**  
Multi-server. Persistent SQLite tracking. Zero-GUI workflow.

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://python.org)
[![Platform](https://img.shields.io/badge/Platform-Termux%20%7C%20Linux-orange?logo=android)](https://termux.dev)
[![Stars](https://img.shields.io/github/stars/sizwinz/MSM-minecraft-server-manager-termux?style=flat&color=yellow)](https://github.com/sizwinz/MSM-minecraft-server-manager-termux/stargazers)

</div>

---

## What MSM Is

MSM manages multiple Minecraft server instances from a single TUI. It downloads server binaries from upstream APIs, starts them in isolated `screen` sessions, tracks performance in SQLite, handles world backups with zip-slip-safe extraction, and delivers a full CLI for routine administration — from a phone running Termux or a Linux box.

**This README reflects the current codebase exactly. Nothing here is aspirational.**

---

## Table of Contents

- [Features](#features)
- [Supported Server Flavors](#supported-server-flavors)
- [Requirements](#requirements)
- [Installation](#installation)
  - [Quick Install](#quick-install)
  - [Manual Install (Termux)](#manual-install-termux)
  - [Manual Install (Debian/Ubuntu/WSL)](#manual-install-debianubuntuwsl)
- [Basic Workflow](#basic-workflow)
- [CLI Menu Reference](#cli-menu-reference)
- [Feature Details](#feature-details)
  - [Multi-Server Runtime Model](#multi-server-runtime-model)
  - [Process Management](#process-management)
  - [Server Startup](#server-startup)
  - [Monitoring and Statistics](#monitoring-and-statistics)
  - [Auto-Restart](#auto-restart)
  - [World Backups](#world-backups)
  - [Command Delivery](#command-delivery)
  - [Tunnel Support](#tunnel-support)
  - [Java Detection](#java-detection)
  - [Version Selection UI](#version-selection-ui)
  - [Configuration Management](#configuration-management)
- [Files and Directories](#files-and-directories)
- [Configuration Reference](#configuration-reference)
- [Project Layout](#project-layout)
- [Security Notes](#security-notes)
- [Development](#development)
- [Known Limitations](#known-limitations)
- [License](#license)

---

## Features

**What MSM does:**

- Manage multiple named server definitions from one CLI
- Download server binaries directly from upstream APIs (PaperMC, Mojang, Fabric, Quilt, GitHub)
- Start each server in its own `screen` session (`mc_<n>`) with PID tracking via `.msm.pid`
- Track sessions, crashes, restarts, backups, and CPU/RAM metrics in SQLite with WAL mode
- Optional per-server auto-restart while MSM is running
- Manual and scheduled world backups (ZIP/DEFLATE level 6, zip-slip blocked)
- Edit `server.properties` and `eula.txt` from within the CLI
- RCON command delivery with `screen -X stuff` fallback
- `ngrok` and `playit` tunnel management with per-server log files

**What MSM does not do:**

- Keep backup scheduling or auto-restart alive after the MSM process exits
- Collect live player counts, TPS, or MSPT (schema columns exist, collection not implemented)
- Run natively on Windows (`screen` and POSIX behavior are hard dependencies)

---

## Supported Server Flavors

| Flavor | Runtime | Default Port | Min RAM | Binary Source |
|---|---|---|---|---|
| **PaperMC** | Java | 25565 | 512 MB | PaperMC API — versioned build metadata |
| **Purpur** | Java | 25565 | 512 MB | Purpur API — latest build per version |
| **Folia** | Java | 25565 | 1024 MB | PaperMC API (Folia project) |
| **Vanilla** | Java | 25565 | 512 MB | Mojang version manifest |
| **Fabric** | Java | 25565 | 768 MB | FabricMC meta — latest loader + installer |
| **Quilt** | Java | 25565 | 768 MB | QuiltMC meta — latest loader |
| **PocketMine-MP** | PHP | 19132 | 256 MB | GitHub releases — `.phar` assets |

Paper, Folia, and Purpur version metadata is fetched concurrently (up to 8 workers, last 20 upstream versions).

---

## Requirements

### Runtime Dependencies

| Dependency | Purpose |
|---|---|
| Python 3.10+ | MSM runtime |
| `psutil >= 5.9` | CPU/RAM sampling and PID lifecycle checks |
| `requests >= 2.31` | HTTP downloads and upstream API calls |
| `screen` | Server session isolation |
| Internet access | Binary downloads and version API metadata |

### Java Version Matrix

| Minecraft Version | Required Java |
|---|---|
| `1.16.x` and older | Java 8 |
| `1.17` – `1.20.4` | Java 17 |
| `1.20.5+` | Java 21 |

The installer automates Java 17 and 21 where packages are available. Java 8 is not installed automatically; configure `java_homes.8` manually if you need very old Minecraft versions.

### Optional

| Tool | Purpose |
|---|---|
| `ngrok` | TCP tunnel via ngrok |
| `playit` / `playit-cli` | Tunnel via playit.gg |
| `php` | PocketMine-MP only |

---

## Installation

### Quick Install

```bash
curl -fsSL https://raw.githubusercontent.com/sizwinz/MSM-minecraft-server-manager-termux/main/install.sh | bash
```

The installer supports Termux and Debian/Ubuntu/WSL. It:

- Uses `pkg` on Termux and `apt-get` on Debian/Ubuntu/WSL
- Installs Python, Git, `screen`, Java 17/21, PHP, and Playit support
- Reuses the current checkout when run from the repo
- Otherwise clones to `~/MSM-minecraft-server-manager-termux`
- Creates `.venv` and installs `requirements.txt`
- Sets `msm.py` executable

Set `MSM_INSTALL_DIR=/custom/path` before running the script to choose a different install directory.

**After install:**

```bash
cd MSM-minecraft-server-manager-termux
source .venv/bin/activate
python msm.py
```

**MSM data lives at:**

```
~/.config/msm/
├── config.json     # server configurations (atomic writes via .tmp -> replace)
├── msm.db          # SQLite: sessions, metrics, backups, error log
└── msm.log         # rotating log — 50 MB max, 30-day retention
```

---

### Manual Install (Termux)

```bash
pkg update && pkg upgrade -y
pkg install -y python git screen openjdk-17 openjdk-21 php python-psutil tur-repo playit

git clone https://github.com/sizwinz/MSM-minecraft-server-manager-termux.git
cd MSM-minecraft-server-manager-termux

python -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python msm.py
```

---

### Manual Install (Debian/Ubuntu/WSL)

Install dependencies:

```bash
sudo apt-get update
sudo apt-get install -y git screen python3 python3-pip python3-venv curl gnupg ca-certificates
sudo apt-get install -y openjdk-17-jre-headless openjdk-21-jre-headless php-cli
curl -fsSL https://playit-cloud.github.io/ppa/key.gpg -o /tmp/playit.gpg
sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/playit.gpg /tmp/playit.gpg
echo "deb [signed-by=/etc/apt/trusted.gpg.d/playit.gpg] https://playit-cloud.github.io/ppa/data ./" | sudo tee /etc/apt/sources.list.d/playit-cloud.list
sudo apt-get update
sudo apt-get install -y playit
```

Then install MSM as your normal user:

```bash
git clone https://github.com/sizwinz/MSM-minecraft-server-manager-termux.git
cd MSM-minecraft-server-manager-termux

python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python msm.py
```

---

## Basic Workflow

```
1. python msm.py          -> launch MSM (checks for screen on startup)
2. Create server profile  -> name sanitized to [a-zA-Z0-9_.-]
3. Install binary         -> fetched from upstream API into ~/minecraft-<n>/
4. Configure server       -> RAM, port, RCON, tunnel, backup interval
5. Start server           -> spawns in screen session named mc_<n>
6. Manage live            -> console attach, commands, stats, world backups
```

---

## CLI Menu Reference

| Action | Description |
|---|---|
| Start server | Writes `server.properties` + `eula.txt`, spawns `screen -dmS mc_<n>` |
| Stop server | Sends `stop` via RCON or `screen -X stuff`; force-quits the screen session if needed |
| Install / Update server | Prompts flavor + version, streams binary to server directory |
| Configure server | RAM, port, MOTD, online-mode, RCON, tunnel, backup settings |
| Edit server.properties | Load/set/delete individual properties; syncs relevant keys back to `config.json` |
| Edit eula.txt | Toggle EULA acceptance |
| Attach to console | Runs `screen -r mc_<n>`; detach with Ctrl+A then D |
| World manager | Manual backup, list, restore (blocked while running), delete |
| Send command | RCON first, `screen -X stuff` fallback |
| Statistics | All-time sessions, uptime, crashes, restarts; 24 h RAM/CPU/peak players |
| Create new server | Sanitizes name, creates profile and server directory |
| Switch server | Numbered list of configured servers |
| Exit | Optionally stops all running servers before exit |

---

## Feature Details

### Multi-Server Runtime Model

`RuntimeManager` keeps one `ServerInstance` per server name. Instances are lazy-initialized on first access and reused. On startup, `RuntimeManager.__init__` calls `resume_background_services()` for every server whose PID file indicates it is still running.

Each `ServerInstance` owns:

- Its own `screen` session (`mc_<sanitized-name>`)
- Its own `.msm.pid`, `.msm.session`, `.msm.tunnel.pid` files
- Its own monitor, auto-restart, and backup daemon threads
- Its own per-instance `threading.Event` stop signals
- Its own tunnel `subprocess.Popen` handle and log file handle

No module-level globals hold process or session state.

---

### Process Management

MSM builds a shell one-liner to write the real server PID before `exec`:

```bash
screen -dmS mc_<n> sh -c "echo $$ > .msm.pid; exec java ... -jar server.jar nogui"
```

MSM then polls `.msm.pid` for up to 10 seconds (250 ms intervals) and validates the PID with `psutil.Process.is_running()` before declaring startup successful.

Per-server state files in the server directory:

```
.msm.pid              # Java/PHP process PID (written by the wrapper before exec)
.msm.session          # MSM session ID — links to SQLite server_sessions.id
.msm.tunnel.pid       # tunnel agent PID
.msm.ngrok.log        # ngrok stdout+stderr
.msm.playit.log       # playit agent stdout+stderr
.msm.playit.secret    # playit agent secret (written by claim exchange)
```

---

### Server Startup

Java servers launch with these JVM flags:

```
java  -Xmx<RAM>M  -Xms<RAM>M
      -XX:+UseG1GC
      -XX:+ParallelRefProcEnabled
      -XX:MaxGCPauseMillis=200
      -jar server.jar nogui
```

PocketMine-MP launches as `php PocketMine-MP.phar`.

MSM resolves the server artifact by checking for `server.jar` first, then any `.jar` alphabetically, then `.phar` for PocketMine-MP. `server.properties` and `eula.txt` are rewritten before every start. RCON fields in `server.properties` are injected from the `rcon` block in `config.json`; if RCON is enabled and a password is set, `enable-rcon=true` and `rcon.password` are added automatically.

---

### Monitoring and Statistics

Each active server gets a daemon monitor thread that uses `psutil.Process.oneshot()` to batch-read CPU and RAM:

| Metric | Sample rate |
|---|---|
| RAM usage % | Every 60 seconds |
| CPU usage % | Every 60 seconds |
| Session start / end | On server start / stop events |
| Crash count | On unexpected exits detected by auto-restart thread |
| Restart count | On each auto-restart trigger |
| Backup history | On each completed backup |

Metrics commit to SQLite (`performance_metrics` table) under WAL mode with a 30-second busy timeout. The statistics view aggregates all-time session data plus a 24-hour rolling window for performance metrics.

---

### Auto-Restart

Controlled per server via `auto_restart: bool`. A separate daemon thread polls independently of the monitor:

| Behavior | Value |
|---|---|
| Poll interval | 15 seconds |
| Delay before restart | 5 seconds |
| Crash / restart counters | Incremented in SQLite per unexpected exit |
| Session lifecycle | Previous session closed; new session ID written to `.msm.session` |

> **Important:** Auto-restart is tied to the MSM process. Exit MSM and it stops. Servers remain alive in `screen`. Supervision resumes automatically on next launch via `RuntimeManager.resume_running_servers()`.

---

### World Backups

Backups are ZIP archives (DEFLATE, compression level 6) stored under `~/minecraft-<server-name>/backups/`.

**World discovery order:**

1. Read `level-name` from `server.properties` (defaults to `world`)
2. Check for `<level-name>`, `<level-name>_nether`, `<level-name>_the_end`
3. Fallback: any directory matching `^world(?:[_.-].+)?$` (case-insensitive)

| Behavior | Detail |
|---|---|
| Manual backups | Available from world manager at any time |
| Scheduled backups | Per-server interval; backup thread polls every 30 s; only runs while MSM is alive and server is online |
| Threading | Offloaded to a worker thread; CLI shows a `\|/-\` spinner |
| Restore guard | Raises `RuntimeError` if server is currently running |
| Zip-slip protection | Every archive member path resolved against destination; symlink entries blocked |
| Disk space check | 500 MB free required before backup or binary install |

---

### Command Delivery

MSM tries delivery methods in this order:

| Method | Condition |
|---|---|
| RCON | `rcon.enabled = true` AND password is non-empty |
| `screen -X stuff` | RCON disabled, password absent, or RCON connection/auth failed |

The RCON client is a minimal Source-style implementation (packet types 2 and 3) with a 5-second socket timeout. It covers command execution only, not console streaming. RCON failures log a warning and fall through to `screen` automatically.

---

### Tunnel Support

MSM spawns the tunnel as a child process and captures stdout+stderr to `.msm.<provider>.log`.

#### ngrok

```
ngrok tcp <port> --log stdout
  -> stdout -> .msm.ngrok.log
  -> public URL queried from http://127.0.0.1:4040/api/tunnels
     (20 s timeout, matched by port number)
```

Store your authtoken via the setup wizard:

```bash
ngrok config add-authtoken <your-token>
```

#### playit

The setup wizard links the local agent, then can create or update the Playit tunnel through the Playit account API. Agent linking uses:

```
playit-cli --stdout claim generate
  -> claim code

playit-cli --stdout claim url <code>
  -> browser URL for account linking

playit-cli --stdout --secret_path .msm.playit.secret claim exchange <code>
  -> writes agent secret to .msm.playit.secret
```

After the agent is linked, MSM shows this Playit authorization page:

```
https://playit.gg/account/setup/wizard/new-account/third-party/third-party-select?partner=other
```

Open it, choose the third-party/other flow, paste the one-time auth code into MSM, and MSM will call the Playit API to create or update a tunnel for the current server. Java servers are mapped as Minecraft Java over TCP; PocketMine-MP is mapped as Minecraft Bedrock over UDP. The local target defaults to `127.0.0.1:<server-port>`.

If you choose to save the Playit login secret, it is stored locally at:

```
~/.config/msm/playit_session.json
```

Once configured, MSM starts the managed agent:

```
playit-cli --stdout --secret_path .msm.playit.secret start
```

The agent log is scanned (reverse line order) for `tunnel_address=<endpoint>` or hostname/IP:port patterns matching `*.playit.gg` or `*.ply.gg`. Claim URLs are matched against `https://playit.gg/claim/<token>`.

> If `.msm.playit.secret` does not exist, MSM skips tunnel start and logs a warning. Run the setup wizard first.

MSM validates the tunnel process is still alive 1 second after launch. Immediate exit triggers a log tail dump to aid diagnosis.

**Termux install for playit:**

```bash
pkg update && pkg upgrade -y
pkg install -y tur-repo playit
```

---

### Java Detection

`get_java_path()` resolves a Java binary in this order:

1. `java_homes.<major_version>` in `config.json` -> `<path>/bin/java`
2. `java` on `PATH`
3. Common JVM base directories, each tried with three naming patterns:

| Pattern | Example |
|---|---|
| `openjdk-<ver>/bin/java` | `/usr/lib/jvm/openjdk-21/bin/java` |
| `java-<ver>-openjdk/bin/java` | `/usr/lib/jvm/java-21-openjdk/bin/java` |
| `jdk-<ver>/bin/java` | `/usr/lib/jvm/jdk-21/bin/java` |

Base directories: `$JAVA_HOME`, `~/../usr/lib/jvm` (Termux path), `/usr/lib/jvm`, `/usr/lib64/jvm`.

Each candidate runs `java -version` and the major version is parsed from the quoted version string. A mismatch logs both the required and detected versions. Duplicate paths are skipped.

---

### Version Selection UI

The version picker supports pagination and snapshot toggling:

```
15 versions per page
n -> next page
p -> previous page
s -> toggle snapshots on/off
0 -> cancel
```

Snapshot detection: presence of `snapshot`, `pre`, or `rc` in the version string (case-insensitive), or `"type": "snapshot"` in the Mojang manifest.

---

### Configuration Management

`ConfigManager` performs a recursive deep merge on every load: `DEFAULT_CONFIG` and `DEFAULT_SERVER_CONFIG` fill in missing keys without touching existing values. New config fields introduced in future versions migrate automatically.

Config writes are atomic: content goes to `config.json.tmp`, then `Path.replace()` swaps it in. A crash mid-write cannot produce a partially-written config. If `config.json` fails JSON parsing, it is backed up as `config.json.bak_<unix_timestamp>` and MSM starts from defaults.

---

## Files and Directories

### Application Data

```
~/.config/msm/
├── config.json          # atomic writes; deep-merge migrated on every load
├── msm.db               # server_sessions | performance_metrics
│                        # backup_history  | error_log
└── msm.log              # 50 MB size limit, 30-day file retention
```

### Per-Server Directory

```
~/minecraft-<sanitized-name>/
├── server.jar           # or *.phar for PocketMine-MP
├── server.properties    # rewritten before every start
├── eula.txt             # rewritten before every start
├── backups/
│   └── world_backup_YYYYMMDD_HHMMSS.zip
├── .msm.pid
├── .msm.session
├── .msm.tunnel.pid
├── .msm.ngrok.log
├── .msm.playit.log
└── .msm.playit.secret
```

Server name sanitization strips non-`[a-zA-Z0-9_.-]` characters, collapses consecutive dots, and strips leading/trailing dots and dashes. An empty result falls back to a random 8-character UUID prefix. Screen session name format: `mc_<sanitized-name>`.

---

## Configuration Reference

MSM creates and migrates `config.json` automatically. Annotated example:

```json
{
  "current_server": "survival",

  "java_homes": {
    "17": "/usr/lib/jvm/java-17-openjdk",
    "21": "/usr/lib/jvm/java-21-openjdk"
  },

  "tunnel_defaults": {
    "provider": "ngrok",
    "binary_path": "ngrok",
    "autostart": false
  },

  "servers": {
    "survival": {
      "server_flavor": "paper",
      "server_version": "1.21.1",
      "eula_accepted": true,
      "ram_mb": 2048,
      "auto_restart": true,

      "backup_settings": {
        "enabled": true,
        "interval_hours": 6
      },

      "tunnel": {
        "enabled": false,
        "provider": "ngrok",
        "binary_path": "ngrok",
        "autostart": false,
        "playit_tunnel_id": null,
        "last_endpoint": null
      },

      "rcon": {
        "enabled": false,
        "host": "127.0.0.1",
        "port": 25575,
        "password": ""
      },

      "server_settings": {
        "motd": "survival Server",
        "port": 25565,
        "max-players": 20,
        "online-mode": "true",
        "enable-rcon": "false",
        "rcon.port": 25575
      }
    }
  }
}
```

`server_settings` keys are written verbatim to `server.properties`. RCON-related properties (`enable-rcon`, `rcon.port`, `rcon.password`) are injected separately from the `rcon` block when RCON is enabled and a password is set.

---

## Project Layout

```
msm.py              # entrypoint — calls ui.cli.main()
core/
  config.py         # ConfigManager: load/save/mutate with deep-merge migration
  constants.py      # VERSION="6.0", paths, timeouts, SERVER_FLAVORS registry
  runtime.py        # RuntimeManager: one ServerInstance per configured server
  server.py         # ServerInstance: lifecycle, threads, backups, tunnels
db/
  manager.py        # DatabaseManager: WAL SQLite, sessions/metrics/backups/errors
ui/
  cli.py            # all menus, wizards, spinner, connection summary
  colors.py         # ANSI ColorScheme (C.*); disable_colors() strips all escapes
utils/
  archive.py        # create_backup_archive, safe_extract_zip, discover_world_directories
  logging_utils.py  # EnhancedLogger: rotating file log + colored stdout
  network.py        # version catalogs, concurrent build fetchers, binary downloads
  playit_api.py     # Playit account API auth and tunnel create/update helpers
  properties.py     # load_properties / write_properties for key=value files
  rcon.py           # RCONClient: Source RCON types 2 and 3, 5 s timeout
  system.py         # Java detection, sanitize_input, PID helpers, disk/IP utils
  tunnels.py        # playit command builders and log regex extractors
tests/
  test_network.py             # Paper concurrent fetcher, Vanilla snapshot filter
  test_playit_api.py          # Playit account API request/response helpers
  test_security_and_java.py   # zip-slip block, Java version matrix, JAVA_HOME edge cases
  test_tunnels.py             # playit command builders, endpoint/claim URL extraction
```

---

## Security Notes

| Area | Implementation |
|---|---|
| Session isolation | Per-`ServerInstance` state; no shared globals for PIDs or sessions |
| PID tracking | `.msm.pid` written by the server process itself; validated via `psutil` |
| Database | WAL mode, `synchronous=NORMAL`, `busy_timeout=30000 ms`, foreign keys enforced |
| Archive safety | ZIP member paths resolved against destination; symlink entries rejected |
| Subprocess | Argument-list calls throughout; `shell=False` enforced everywhere |
| Java validation | `java -version` parsed; major version matched before launching any server |
| Path sanitization | Server names constrained to `[a-zA-Z0-9_.-]` before use in paths and screen names |
| Config atomicity | `config.json.tmp` written then atomically replaced; corruption backs up with timestamp |

---

## Development

### Setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt -r requirements-dev.txt
```

| Package | Version | Role |
|---|---|---|
| `psutil` | >= 5.9 | runtime |
| `requests` | >= 2.31 | runtime |
| `black` | >= 24.10 | dev |
| `flake8` | >= 7.1 | dev |
| `pytest` | >= 8.3 | dev |

### Checks (identical to CI)

```bash
python -m flake8 --jobs=1 .       # max-line-length=100; excludes .git __pycache__ .venv
python -m black --check .
python -m pytest
python -m compileall msm.py core db ui utils tests
```

### CI Pipeline

GitHub Actions runs on every push:

```
flake8      -> style and lint (100-char limit)
black       -> format enforcement
pytest      -> unit tests
compileall  -> bytecode syntax validation across all modules
```

Test temporaries go to `.test_tmp/` (gitignored; cleaned up per test).

### Implementation Constraints

- Runtime state belongs inside `ServerInstance`. No global process or session variables.
- New config fields go into `DEFAULT_CONFIG` / `DEFAULT_SERVER_CONFIG` so existing installs migrate via deep merge.
- All ZIP extraction must use `safe_extract_zip`.
- All subprocess calls must use argument lists with `shell=False`.
- User-exposed config changes should go through `ConfigManager.mutate()`.

---

## Known Limitations

- **Thread lifetime:** Exiting MSM stops the monitor, auto-restart, and scheduled backup threads. Servers keep running in `screen`. Supervision resumes on next MSM launch.
- **playit account auth:** Tunnel creation is automated, but Playit still requires a browser-based one-time authorization code before MSM can use the account API.
- **PocketMine-MP:** Binary download and process start work. The configure/install UI is built around Java server fields; some options are irrelevant for PHP servers.
- **Live metrics:** TPS, MSPT, and player counts are not collected. The SQLite schema has `tps`, `mspt`, and `player_count` columns, but the monitor loop does not populate them.
- **Platform:** `screen` and POSIX process behavior are hard dependencies. Unit tests and CI run cross-platform; actual server hosting does not.
- **HTTP reliability:** `requests` retries 5 times with backoff factor 2 on 429/5xx. Aggressive upstream rate-limiting may still cause install failures on slow connections.

---

## License

MIT — see [`LICENSE`](LICENSE).

---

<div align="center">

Made for Termux. Built for control.

</div>
