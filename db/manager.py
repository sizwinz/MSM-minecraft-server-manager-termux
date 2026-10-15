"""SQLite persistence for sessions, metrics, and backups."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


class DatabaseManager:
    """Thread-friendly database manager with WAL enabled."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._init_database()

    def _create_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=30000;")
        return conn

    def _init_database(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS server_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_name TEXT NOT NULL,
                    flavor TEXT NOT NULL,
                    version TEXT NOT NULL,
                    start_time TIMESTAMP NOT NULL,
                    end_time TIMESTAMP,
                    duration INTEGER,
                    peak_players INTEGER DEFAULT 0,
                    crash_count INTEGER DEFAULT 0,
                    restart_count INTEGER DEFAULT 0,
                    ram_usage_avg REAL,
                    cpu_usage_avg REAL
                );
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_name TEXT NOT NULL,
                    timestamp TIMESTAMP NOT NULL,
                    ram_usage REAL NOT NULL,
                    cpu_usage REAL NOT NULL,
                    player_count INTEGER DEFAULT 0,
                    tps REAL DEFAULT 20.0,
                    mspt REAL DEFAULT 0.0
                );
                CREATE TABLE IF NOT EXISTS backup_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_name TEXT NOT NULL,
                    backup_path TEXT NOT NULL,
                    backup_size INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    backup_type TEXT DEFAULT 'manual'
                );
                CREATE TABLE IF NOT EXISTS error_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_name TEXT,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    stack_trace TEXT,
                    timestamp TIMESTAMP NOT NULL,
                    severity TEXT DEFAULT 'ERROR'
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_server
                    ON server_sessions(server_name);
                CREATE INDEX IF NOT EXISTS idx_metrics_server_time
                    ON performance_metrics(server_name, timestamp);
                CREATE INDEX IF NOT EXISTS idx_backups_server
                    ON backup_history(server_name);
                CREATE INDEX IF NOT EXISTS idx_errors_time
                    ON error_log(timestamp);
                """)

    @contextmanager
    def get_connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._create_connection()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def log_session_start(self, server_name: str, flavor: str, version: str) -> int:
        with self.get_connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO server_sessions (server_name, flavor, version, start_time)
                VALUES (?, ?, ?, ?)
                """,
                (server_name, flavor, version, datetime.now().isoformat()),
            )
            return int(cursor.lastrowid)

    def log_session_end(self, session_id: int) -> None:
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT start_time FROM server_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return
            start_time = datetime.fromisoformat(row["start_time"])
            duration = int((datetime.now() - start_time).total_seconds())
            conn.execute(
                """
                UPDATE server_sessions
                SET end_time = ?, duration = ?
                WHERE id = ?
                """,
                (datetime.now().isoformat(), duration, session_id),
            )

    def get_last_open_session(self, server_name: str) -> int | None:
        with self.get_connection() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM server_sessions
                WHERE server_name = ? AND end_time IS NULL
                ORDER BY id DESC
                LIMIT 1
                """,
                (server_name,),
            ).fetchone()
            return int(row["id"]) if row else None

    def increment_crash_count(self, session_id: int) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE server_sessions
                SET crash_count = crash_count + 1
                WHERE id = ?
                """,
                (session_id,),
            )

    def increment_restart_count(self, session_id: int) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                UPDATE server_sessions
                SET restart_count = restart_count + 1
                WHERE id = ?
                """,
                (session_id,),
            )

    def log_performance_metric(
        self,
        server_name: str,
        ram_usage: float,
        cpu_usage: float,
        player_count: int = 0,
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO performance_metrics
                    (server_name, timestamp, ram_usage, cpu_usage, player_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    server_name,
                    datetime.now().isoformat(),
                    ram_usage,
                    cpu_usage,
                    player_count,
                ),
            )

    def log_backup(
        self,
        server_name: str,
        backup_path: str,
        backup_size: int,
        backup_type: str = "manual",
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO backup_history
                    (
                        server_name,
                        backup_path,
                        backup_size,
                        created_at,
                        backup_type
                    )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    server_name,
                    backup_path,
                    backup_size,
                    datetime.now().isoformat(),
                    backup_type,
                ),
            )

    def log_error(
        self,
        server_name: str | None,
        error_type: str,
        error_message: str,
        stack_trace: str | None = None,
        severity: str = "ERROR",
    ) -> None:
        with self.get_connection() as conn:
            conn.execute(
                """
                INSERT INTO error_log
                    (server_name, error_type, error_message, stack_trace, timestamp, severity)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    server_name,
                    error_type,
                    error_message,
                    stack_trace,
                    datetime.now().isoformat(),
                    severity,
                ),
            )

    def get_server_statistics(self, server_name: str) -> dict[str, Any]:
        with self.get_connection() as conn:
            session_stats = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_sessions,
                    AVG(duration) AS avg_duration,
                    SUM(duration) AS total_uptime,
                    SUM(crash_count) AS total_crashes,
                    SUM(restart_count) AS total_restarts
                FROM server_sessions
                WHERE server_name = ? AND end_time IS NOT NULL
                """,
                (server_name,),
            ).fetchone()
            perf_stats = conn.execute(
                """
                SELECT
                    AVG(ram_usage) AS avg_ram_usage,
                    AVG(cpu_usage) AS avg_cpu_usage,
                    MAX(player_count) AS peak_players
                FROM performance_metrics
                WHERE server_name = ?
                  AND julianday(timestamp) > julianday(?)
                """,
                (server_name, (datetime.now() - timedelta(hours=24)).isoformat()),
            ).fetchone()
            return {
                "total_sessions": (
                    session_stats["total_sessions"] if session_stats else 0
                ),
                "avg_duration": session_stats["avg_duration"] if session_stats else 0,
                "total_uptime": session_stats["total_uptime"] if session_stats else 0,
                "total_crashes": session_stats["total_crashes"] if session_stats else 0,
                "total_restarts": (
                    session_stats["total_restarts"] if session_stats else 0
                ),
                "avg_ram_usage_24h": perf_stats["avg_ram_usage"] if perf_stats else 0,
                "avg_cpu_usage_24h": perf_stats["avg_cpu_usage"] if perf_stats else 0,
                "peak_players_24h": perf_stats["peak_players"] if perf_stats else 0,
            }
