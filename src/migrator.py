"""Migration engine — walks Dropbox, streams to Google Drive, tracks in SQLite."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from dropbox.files import FileMetadata

from .db import MigrationDB
from .dropbox_client import DropboxClient
from .gdrive_client import GDriveClient

logger = logging.getLogger(__name__)


class Migrator:
    def __init__(self, dbx: DropboxClient, gdrive: GDriveClient, db: MigrationDB, dest_folder: str = "Dropbox Migration", conflict: str = "skip") -> None:
        self._dbx = dbx
        self._gdrive = gdrive
        self._db = db
        self._dest_folder = dest_folder
        self._conflict = conflict
        self._run_id: int | None = None

    def scan(self, src_path: str = "", include_shared: bool = False) -> int:
        logger.info("Scanning Dropbox path: '%s'", src_path or "/")
        self._db.add_log(self._run_id, "INFO", f"Scanning Dropbox: {src_path or '/'}")

        files = self._dbx.list_folder_recursive(src_path)
        count = 0
        for f in files:
            modified = f.server_modified.isoformat() if hasattr(f, "server_modified") and f.server_modified else None
            self._db.upsert_file(f.path_display, f.name, f.size, modified)
            count += 1

        if include_shared:
            shared = self._dbx.list_shared_folders()
            for sf in shared:
                path = sf.get("path_lower") or f"/{sf['name']}"
                shared_files = self._dbx.list_folder_recursive(path)
                for f in shared_files:
                    modified = f.server_modified.isoformat() if hasattr(f, "server_modified") and f.server_modified else None
                    self._db.upsert_file(f.path_display, f.name, f.size, modified, status="pending")
                    count += 1

        logger.info("Scan complete: %d files found", count)
        self._db.add_log(self._run_id, "INFO", f"Scan complete: {count} files found")
        return count

    def dry_run(self) -> dict:
        stats = self._db.get_stats()
        pending = self._db.get_pending_files()
        return {
            "total_files": stats.get("total", 0),
            "pending": stats.get("pending", 0),
            "already_transferred": stats.get("transferred", 0),
            "total_bytes": stats.get("total_bytes", 0),
            "pending_files_sample": [f["dropbox_path"] for f in pending[:20]],
        }

    def migrate(self, limit: int = 0, progress_cb=None) -> dict:
        self._run_id = self._db.start_run()
        self._db.add_log(self._run_id, "INFO", "Migration started")

        pending = self._db.get_pending_files(limit=limit)
        total = len(pending)
        transferred = 0
        skipped = 0
        failed = 0
        bytes_done = 0

        root_id = self._gdrive.ensure_folder_path(self._dest_folder)

        for i, file_rec in enumerate(pending):
            dbx_path = file_rec["dropbox_path"]
            filename = file_rec["filename"]
            size = file_rec["size_bytes"]

            if self._db.is_transferred(dbx_path):
                skipped += 1
                continue

            try:
                rel_dir = os.path.dirname(dbx_path).lstrip("/")
                if rel_dir:
                    folder_id = self._gdrive.ensure_folder_path(rel_dir, root_id)
                else:
                    folder_id = root_id

                existing = self._gdrive.file_exists(filename, folder_id)
                if existing and self._conflict == "skip":
                    self._db.mark_skipped(dbx_path, "already exists")
                    skipped += 1
                    self._db.add_log(self._run_id, "INFO", f"Skipped (exists): {dbx_path}", dbx_path)
                    if progress_cb:
                        progress_cb(i + 1, total, dbx_path, "skipped")
                    continue

                buf, actual_size = self._dbx.download_to_buffer(dbx_path)
                gdrive_id = self._gdrive.upload_from_buffer(buf, filename, folder_id, actual_size)

                self._db.mark_transferred(dbx_path, gdrive_id, folder_id)
                transferred += 1
                bytes_done += actual_size
                self._db.add_log(self._run_id, "INFO", f"Transferred: {dbx_path} ({actual_size} bytes)", dbx_path)

                if progress_cb:
                    progress_cb(i + 1, total, dbx_path, "transferred")

            except Exception as e:
                self._db.mark_failed(dbx_path, str(e))
                failed += 1
                self._db.add_log(self._run_id, "ERROR", f"Failed: {dbx_path} — {e}", dbx_path)
                logger.error("Failed to migrate %s: %s", dbx_path, e)
                if progress_cb:
                    progress_cb(i + 1, total, dbx_path, "failed")

        stats = {"total": total, "transferred": transferred, "skipped": skipped, "failed": failed, "bytes": bytes_done}
        self._db.finish_run(self._run_id, stats)
        self._db.add_log(self._run_id, "INFO", f"Migration complete: {transferred} transferred, {skipped} skipped, {failed} failed")
        return stats
