# Dropbox → Google Drive Migration Tool

CLI + web dashboard tool to migrate files from Dropbox to Google Drive with full progress tracking.

## Features

- **Resumable migrations** — SQLite tracks every file; interrupted runs pick up where they left off
- **Web dashboard** — real-time donut charts, progress bars, file counts, run history, error logs
- **Shared folders** — migrate personal files and shared folders
- **Conflict handling** — skip, overwrite, or rename duplicates
- **Dry run** — preview what will transfer without moving anything
- **Retry failed** — re-attempt previously failed files
- **Logging** — timestamped log files in `logs/`

## Setup

### 1. Install Dependencies

```bash
python -m venv .venv
.venv\Scripts\activate    # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt
```

### 2. Dropbox API Credentials

1. Go to [Dropbox App Console](https://www.dropbox.com/developers/apps)
2. Create a new app → **Scoped access** → **Full Dropbox**
3. In **Permissions** tab, enable: `files.content.read`, `files.metadata.read`, `sharing.read`
4. Copy the **App key** (and **App secret** if available)
5. Set in `config.ini` or as environment variables:
   ```
   DROPBOX_APP_KEY=your_key
   DROPBOX_APP_SECRET=your_secret
   ```

### 3. Google Drive API Credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project → Enable **Google Drive API**
3. Go to **APIs & Services → Credentials**
4. Create **OAuth 2.0 Client ID** (Desktop application)
5. Download the JSON and save as `credentials.json` in the project root

### 4. Configure

```bash
cp config.ini.template config.ini
# Edit config.ini with your Dropbox credentials
```

## Usage

```bash
# Full migration (opens dashboard at http://localhost:5050)
python -m src.main

# Scan only — index Dropbox files without transferring
python -m src.main --scan-only

# Dry run — show what would transfer
python -m src.main --dry-run

# Migrate specific folder
python -m src.main --src "/Photos" --dest "My Photos Backup"

# Include shared folders
python -m src.main --include-shared

# Limit to 100 files per run
python -m src.main --limit 100

# Retry previously failed files
python -m src.main --retry-failed

# Dashboard only (view existing migration data)
python -m src.main --dashboard-only

# No dashboard (console only)
python -m src.main --no-dashboard

# Verbose logging
python -m src.main --verbose
```

## Dashboard

Auto-opens at `http://localhost:5050` with:
- File count cards (total, transferred, pending, failed, skipped)
- Donut charts (progress, processed, error rate)
- Progress bar
- Run history table
- Failed files table
- Real-time log viewer
- Auto-refreshes every 5 seconds

## Project Structure

```
dropbox-google-drive/
├── src/
│   ├── main.py           # CLI entry point
│   ├── config.py          # Config loader
│   ├── dropbox_client.py  # Dropbox API wrapper
│   ├── gdrive_client.py   # Google Drive API wrapper
│   ├── db.py              # SQLite state tracking
│   ├── migrator.py        # Migration orchestration
│   └── dashboard.py       # Flask web dashboard
├── config.ini.template
├── requirements.txt
├── .gitignore
└── README.md
```
