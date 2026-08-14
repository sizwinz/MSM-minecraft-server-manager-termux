"""Application-wide constants."""

from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path

VERSION = "6.0"

CONFIG_DIR = Path(os.path.expanduser("~/.config/msm"))
CONFIG_FILE = CONFIG_DIR / "config.json"
DATABASE_FILE = CONFIG_DIR / "msm.db"
LOG_FILE = CONFIG_DIR / "msm.log"

VERSIONS_PER_PAGE = 15
PAPER_VERSION_LOOKBACK = 20
REQUEST_TIMEOUT = (15, 45)
MAX_RETRIES = 5
RETRY_BACKOFF = 2
NGROK_TIMEOUT = 20
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

MAX_RAM_PERCENTAGE = 80
MONITOR_INTERVAL = 60
AUTO_RESTART_POLL_INTERVAL = 15
AUTO_RESTART_DELAY_SECONDS = 5
BACKUP_POLL_INTERVAL = 30
DEFAULT_BACKUP_INTERVAL_HOURS = 6
MAX_LOG_SIZE = 50 * 1024 * 1024
LOG_RETENTION_DAYS = 30

MAX_FILENAME_LENGTH = 255
ALLOWED_FILENAME_CHARS = re.compile(r"^[a-zA-Z0-9_.-]+$")
INVALID_FILENAME_CHARS = re.compile(r"[^a-zA-Z0-9_.-]")
COLLAPSE_DOTS_PATTERN = re.compile(r"\.{2,}")
WORLD_SUFFIX_PATTERN = re.compile(r"^world(?:[_.-].+)?$", re.IGNORECASE)

BACKUP_COMPRESSION = zipfile.ZIP_DEFLATED
BACKUP_COMPRESSION_LEVEL = 6

PID_FILE_NAME = ".msm.pid"
SESSION_FILE_NAME = ".msm.session"
TUNNEL_PID_FILE_NAME = ".msm.tunnel.pid"
PLAYIT_SECRET_FILE_NAME = ".msm.playit.secret"
SERVER_PROPERTIES_FILE = "server.properties"
EULA_FILE = "eula.txt"

SUPPORTED_TUNNEL_PROVIDERS = ("ngrok", "playit")
SUPPORTED_TUNNEL_PROTOCOLS = ("tcp", "udp")
DEFAULT_TUNNEL_BINARIES = {
    "ngrok": "ngrok",
    "playit": "playit-cli",
}

PLAYIT_ENDPOINT_FILE_NAME = ".msm.playit.endpoint"
NGROK_ENDPOINT_FILE_NAME = ".msm.ngrok.endpoint"

TUNNEL_STATUS_DISABLED = "disabled"
TUNNEL_STATUS_BINARY_MISSING = "binary_missing"
TUNNEL_STATUS_SECRET_MISSING = "secret_missing"
TUNNEL_STATUS_AUTH_MISSING = "auth_missing"
TUNNEL_STATUS_PROCESS_RUNNING = "process_running"
TUNNEL_STATUS_READY = "ready"
TUNNEL_STATUS_MAPPING_MISSING = "mapping_missing"
TUNNEL_STATUS_FAILED = "failed"
TUNNEL_STATUS_WRONG_PROTOCOL = "wrong_protocol"
TUNNEL_STATUS_NOT_RUNNING = "not_running"

_java_home = os.environ.get("JAVA_HOME")
# Termux stores its JVM under a path relative to the Termux prefix,
# not the Linux standard /usr/lib/jvm.  Only add this entry when we are
# actually inside a Termux environment to avoid confusing path lookups on
# standard Linux / WSL systems.
_termux_jvm = Path("/data/data/com.termux/files/usr/lib/jvm")
COMMON_JAVA_HOME_BASES = [
    *([Path(_java_home)] if _java_home else []),
    *([_termux_jvm] if _termux_jvm.exists() else []),
    Path(os.path.expanduser("~/.config/msm/java")),
    Path("/usr/lib/jvm"),
    Path("/usr/lib64/jvm"),
    Path("/usr/lib/jvm/java-17-openjdk-amd64"),
    Path("/usr/lib/jvm/java-21-openjdk-amd64"),
]

SERVER_FLAVORS = {
    "paper": {
        "name": "PaperMC",
        "description": "High-performance server with plugin support",
        "api_base": "https://fill.papermc.io/v3/projects/paper",
        "supports_versions": True,
        "supports_snapshots": True,
        "jar_pattern": "paper-{version}-{build}.jar",
        "default_port": 25565,
        "type": "java",
        "min_ram": 512,
    },
    "purpur": {
        "name": "Purpur",
        "description": "Paper fork with extra gameplay controls",
        "api_base": "https://api.purpurmc.org/v2/purpur",
        "supports_versions": True,
        "supports_snapshots": False,
        "jar_pattern": "purpur-{version}-{build}.jar",
        "default_port": 25565,
        "type": "java",
        "min_ram": 512,
    },
    "folia": {
        "name": "Folia",
        "description": "Regionized multi-threaded Paper fork",
        "api_base": "https://fill.papermc.io/v3/projects/folia",
        "supports_versions": True,
        "supports_snapshots": False,
        "jar_pattern": "folia-{version}-{build}.jar",
        "default_port": 25565,
        "type": "java",
        "min_ram": 1024,
    },
    "vanilla": {
        "name": "Vanilla Minecraft",
        "description": "Official Mojang server",
        "api_base": "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
        "supports_versions": True,
        "supports_snapshots": True,
        "jar_pattern": "server.jar",
        "default_port": 25565,
        "type": "java",
        "min_ram": 512,
    },
    "fabric": {
        "name": "Fabric",
        "description": "Lightweight modding platform",
        "api_base": "https://meta.fabricmc.net/v2/versions",
        "supports_versions": True,
        "supports_snapshots": True,
        "jar_pattern": "fabric-server-launch.jar",
        "default_port": 25565,
        "type": "java",
        "min_ram": 768,
    },
    "quilt": {
        "name": "Quilt",
        "description": "Fabric-compatible modern fork",
        "api_base": "https://meta.quiltmc.org/v3/versions",
        "supports_versions": True,
        "supports_snapshots": True,
        "jar_pattern": "quilt-server-launch.jar",
        "default_port": 25565,
        "type": "java",
        "min_ram": 768,
    },
    "pocketmine": {
        "name": "PocketMine-MP",
        "description": "Bedrock Edition server software",
        "api_base": "https://api.github.com/repos/pmmp/PocketMine-MP/releases",
        "supports_versions": True,
        "supports_snapshots": True,
        "jar_pattern": "PocketMine-MP.phar",
        "default_port": 19132,
        "type": "php",
        "min_ram": 256,
    },
}
