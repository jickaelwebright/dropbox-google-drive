"""SQLite persistence for migration state tracking."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path("migration.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS migration_files (
    dropbox_path    TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    size_bytes      INTEGER NOT NULL,
    dropbox_modified TEXT,
    gdrive_file_id  TEXT,
    gdrive_folder_id TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
    error_message   TEXT,
    transferred_at  TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS migration_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    total_files     INTEGER DEFAULT 0,
    transferred     INTEGER DEFAULT 0,
    skipped         INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0,
    bytes_transferred INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS migration_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER,
    timestamp       TEXT NOT NULL,
    level           TEXT NOT NULL,
    message         TEXT NOT NULL,
    dropbox_path    TEXT,
    FOREIGN KEY (run_id) REFERENCES migration_runs(id)
);
"""


class MigrationDB:
    def __init__(self, db_path: Path = DEFAULT_DB) -> None:
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._lock = threading.Lock()
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def start_run(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.execute("INSERT INTO migration_runs (started_at, status) VALUES (?, 'running')", (now,))
            self._conn.commit()
            return cur.lastrowid

    def finish_run(self, run_id: int, stats: dict) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE migration_runs SET finished_at=?, total_files=?, transferred=?, skipped=?, failed=?, bytes_transferred=?, status='completed' WHERE id=?",
                (now, stats.get("total", 0), stats.get("transferred", 0), stats.get("skipped", 0), stats.get("failed", 0), stats.get("bytes", 0), run_id),
            )
            self._conn.commit()

    def upsert_file(self, dropbox_path: str, filename: str, size: int, modified: str | None = None, status: str = "pending") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                """INSERT INTO migration_files (dropbox_path, filename, size_bytes, dropbox_modified, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(dropbox_path) DO UPDATE SET size_bytes=excluded.size_bytes, dropbox_modified=excluded.dropbox_modified, status=CASE WHEN migration_files.status='transferred' THEN 'transferred' ELSE excluded.status END""",
                (dropbox_path, filename, size, modified, status, now),
            )
            self._conn.commit()

    def mark_transferred(self, dropbox_path: str, gdrive_file_id: str, gdrive_folder_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute(
                "UPDATE migration_files SET status='transferred', gdrive_file_id=?, gdrive_folder_id=?, transferred_at=?, error_message=NULL WHERE dropbox_path=?",
                (gdrive_file_id, gdrive_folder_id, now, dropbox_path),
            )
            self._conn.commit()

    def mark_failed(self, dropbox_path: str, error: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE migration_files SET status='failed', error_message=? WHERE dropbox_path=?", (error, dropbox_path))
            self._conn.commit()

    def mark_skipped(self, dropbox_path: str, reason: str = "") -> None:
        with self._lock:
            self._conn.execute("UPDATE migration_files SET status='skipped', error_message=? WHERE dropbox_path=?", (reason, dropbox_path))
            self._conn.commit()

    def add_log(self, run_id: int | None, level: str, message: str, dropbox_path: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._conn.execute("INSERT INTO migration_log (run_id, timestamp, level, message, dropbox_path) VALUES (?, ?, ?, ?, ?)", (run_id, now, level, message, dropbox_path))
            self._conn.commit()

    def get_stats(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                """SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status='transferred' THEN 1 ELSE 0 END) as transferred,
                    SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failed,
                    SUM(CASE WHEN status='skipped' THEN 1 ELSE 0 END) as skipped,
                    SUM(size_bytes) as total_bytes,
                    SUM(CASE WHEN status='transferred' THEN size_bytes ELSE 0 END) as transferred_bytes
                FROM migration_files"""
            ).fetchone()
        return dict(row) if row else {}

    def get_pending_files(self, limit: int = 0) -> list[dict]:
        with self._lock:
            q = "SELECT * FROM migration_files WHERE status='pending' ORDER BY dropbox_path"
            if limit > 0:
                q += f" LIMIT {limit}"
            rows = self._conn.execute(q).fetchall()
        return [dict(r) for r in rows]

    def get_failed_files(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM migration_files WHERE status='failed' ORDER BY dropbox_path").fetchall()
        return [dict(r) for r in rows]

    def get_recent_logs(self, limit: int = 100) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM migration_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_runs(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM migration_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def is_transferred(self, dropbox_path: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT status FROM migration_files WHERE dropbox_path=?", (dropbox_path,)).fetchone()
        return row is not None and row["status"] == "transferred"

    def close(self) -> None:
        self._conn.close()
