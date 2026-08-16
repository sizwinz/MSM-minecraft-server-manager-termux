"""Structured logging with terminal output."""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from pathlib import Path

from ui.colors import C


class EnhancedLogger:
    """Log to file and stdout with lightweight structured context."""

    def __init__(
        self,
        log_file: str | os.PathLike[str],
        max_size: int = 50 * 1024 * 1024,
        retention_days: int = 30,
    ):
        self.log_file = Path(log_file)
        self.max_size = max_size
        self.retention_days = retention_days
        self._setup_logging()

    def _setup_logging(self) -> None:
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_log_if_needed()
        self.logger = logging.getLogger("MSM")
        self.logger.setLevel(logging.DEBUG)
        self.logger.handlers.clear()
        handler = logging.FileHandler(self.log_file, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s [%(levelname)8s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        self.logger.addHandler(handler)
        self.logger.propagate = False

    def _rotate_log_if_needed(self) -> None:
        if not self.log_file.exists() or self.log_file.stat().st_size <= self.max_size:
            return
        backup_file = self.log_file.with_suffix(
            f"{self.log_file.suffix}.{int(time.time())}"
        )
        self.log_file.replace(backup_file)
        cutoff = time.time() - (self.retention_days * 24 * 60 * 60)
        for file in self.log_file.parent.glob(f"{self.log_file.name}.*"):
            if file.stat().st_ctime < cutoff:
                file.unlink(missing_ok=True)

    def log(self, level: str, message: str, **kwargs: object) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        color_map = {
            "DEBUG": C.DIM,
            "INFO": C.BLUE,
            "SUCCESS": C.GREEN,
            "WARNING": C.YELLOW,
            "ERROR": C.RED,
            "CRITICAL": C.BG_RED + C.WHITE,
        }
        normalized_level = level.upper()
        color = color_map.get(normalized_level, C.RESET)
        suffix = f" {C.DIM}{kwargs}{C.RESET}" if kwargs else ""
        print(
            f"{C.DIM}[{timestamp}]{C.RESET} "
            f"{color}[{normalized_level:>8s}]{C.RESET} {message}{suffix}"
        )
        payload = f"{message} | {kwargs}" if kwargs else message
        self.logger.log(getattr(logging, normalized_level, logging.INFO), payload)
