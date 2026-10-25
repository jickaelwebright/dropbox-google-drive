"""CLI entry point for the Dropbox → Google Drive migration tool."""

from __future__ import annotations

import argparse
import logging
import sys
import webbrowser
from pathlib import Path

from .config import load_config
from .db import MigrationDB
from .dropbox_client import DropboxClient
from .gdrive_client import GDriveClient
from .migrator import Migrator
from .dashboard import run_dashboard


def setup_logging(verbose: bool = False) -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)

    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / f"migration_{ts}.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def progress_callback(current: int, total: int, path: str, status: str) -> None:
    pct = (current / total * 100) if total else 0
    icon = {"transferred": "+", "skipped": "~", "failed": "!"}
    print(f"  [{icon.get(status, '?')}] {current}/{total} ({pct:.1f}%) {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Dropbox → Google Drive Migration Tool")
    parser.add_argument("--src", default="", help="Dropbox source path (default: root)")
    parser.add_argument("--dest", default="", help="Google Drive destination folder name")
    parser.add_argument("--dry-run", action="store_true", help="Scan and show plan without transferring")
    parser.add_argument("--scan-only", action="store_true", help="Only scan Dropbox and populate database")
    parser.add_argument("--conflict", choices=["skip", "overwrite", "rename"], default="", help="Conflict resolution")
    parser.add_argument("--include-shared", action="store_true", help="Include shared folders")
    parser.add_argument("--limit", type=int, default=0, help="Max files to transfer (0=unlimited)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    parser.add_argument("--no-dashboard", action="store_true", help="Disable web dashboard")
    parser.add_argument("--dashboard-port", type=int, default=5050, help="Dashboard port (default: 5050)")
    parser.add_argument("--dashboard-only", action="store_true", help="Only run the dashboard (view existing data)")
    parser.add_argument("--db", default="migration.db", help="SQLite database path")
    parser.add_argument("--config", default="config.ini", help="Config file path")
    parser.add_argument("--retry-failed", action="store_true", help="Retry previously failed files")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger(__name__)

    cfg = load_config(Path(args.config))
    db = MigrationDB(Path(args.db))

    if args.dashboard_only:
        port = args.dashboard_port
        print(f"\n  Dashboard: http://localhost:{port}\n")
        webbrowser.open(f"http://localhost:{port}")
        from .dashboard import create_app
        app = create_app(db)
        app.run(host="0.0.0.0", port=port, debug=False)
        return

    if not args.no_dashboard:
        port = args.dashboard_port
        run_dashboard(db, port)
        print(f"\n  Dashboard: http://localhost:{port}")

    dest = args.dest or cfg["dest_folder"]
    conflict = args.conflict or cfg["conflict"]
    include_shared = args.include_shared or cfg["include_shared"]

    if not cfg["dropbox_app_key"]:
        print("\nERROR: Dropbox app key not configured.")
        print("Set DROPBOX_APP_KEY env var or add to config.ini")
        sys.exit(1)

    print("\n  Authenticating Dropbox...")
    dbx = DropboxClient(cfg["dropbox_app_key"], cfg["dropbox_app_secret"])
    dbx.authenticate()

    if args.scan_only or args.dry_run:
        migrator = Migrator(dbx, None, db, dest, conflict)
        count = migrator.scan(args.src, include_shared)
        if args.dry_run:
            plan = migrator.dry_run()
            print(f"\n  DRY RUN RESULTS:")
            print(f"    Total files: {plan['total_files']}")
            print(f"    Pending transfer: {plan['pending']}")
            print(f"    Already done: {plan['already_transferred']}")
            print(f"    Total size: {_fmt_bytes(plan['total_bytes'])}")
            if plan['pending_files_sample']:
                print(f"\n    Sample files:")
                for p in plan['pending_files_sample']:
                    print(f"      {p}")
        else:
            print(f"\n  Scan complete: {count} files indexed in database")
        if not args.no_dashboard:
            print(f"\n  Dashboard: http://localhost:{args.dashboard_port}")
            input("\n  Press Enter to exit...")
        return

    print("  Authenticating Google Drive...")
    sa_key = cfg["google_service_account"]
    if sa_key and Path(sa_key).exists():
        gdrive = GDriveClient.from_service_account(Path(sa_key))
    else:
        gdrive = GDriveClient.from_oauth()

    about = gdrive.get_about()
    user = about.get("user", {})
    quota = about.get("storageQuota", {})
    print(f"  Google Drive: {user.get('displayName', '?')} ({user.get('emailAddress', '?')})")
    if quota.get("limit"):
        used = int(quota.get("usage", 0))
        limit = int(quota["limit"])
        print(f"  Storage: {_fmt_bytes(used)} / {_fmt_bytes(limit)}")

    migrator = Migrator(dbx, gdrive, db, dest, conflict)

    if args.retry_failed:
        failed = db.get_failed_files()
        for f in failed:
            db.upsert_file(f["dropbox_path"], f["filename"], f["size_bytes"], status="pending")
        print(f"  Reset {len(failed)} failed files to pending")

    print("\n  Scanning Dropbox...")
    count = migrator.scan(args.src, include_shared)
    print(f"  Found {count} files")

    stats = db.get_stats()
    pending = stats.get("pending", 0)
    if pending == 0:
        print("\n  Nothing to transfer. All files already migrated.")
        if not args.no_dashboard:
            input("\n  Press Enter to exit...")
        return

    print(f"\n  Starting migration: {pending} files to transfer")
    print(f"  Destination: {dest}")
    print(f"  Conflict mode: {conflict}")
    print()

    result = migrator.migrate(limit=args.limit, progress_cb=progress_callback)

    print(f"\n  MIGRATION COMPLETE")
    print(f"    Transferred: {result['transferred']}")
    print(f"    Skipped:     {result['skipped']}")
    print(f"    Failed:      {result['failed']}")
    print(f"    Data moved:  {_fmt_bytes(result['bytes'])}")

    if result['failed'] > 0:
        print(f"\n  Run with --retry-failed to retry failed files")

    if not args.no_dashboard:
        print(f"\n  Dashboard: http://localhost:{args.dashboard_port}")
        input("\n  Press Enter to exit...")

    db.close()


def _fmt_bytes(b: int) -> str:
    if not b:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(b) < 1024:
            return f"{b:.1f} {unit}" if b != int(b) else f"{b} {unit}"
        b /= 1024
    return f"{b:.1f} PB"


if __name__ == "__main__":
    main()
