"""Google Drive API v3 client — reused from php-google-drive-updater patterns."""

from __future__ import annotations

import logging
import mimetypes
import random
import threading
import time
from pathlib import Path

from google.auth.transport.requests import Request, AuthorizedSession
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive"]
_RETRYABLE = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF = [1, 2, 4]
_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3/files"
_CHUNK_SIZE = 5 * 1024 * 1024


class GDriveClient:
    def __init__(self, credentials) -> None:
        self._credentials = credentials
        self._service = build("drive", "v3", credentials=credentials)
        self._files = self._service.files()
        self._tls = threading.local()
        self._folder_cache: dict[str, str] = {}

    @classmethod
    def from_oauth(cls, credentials_file: Path = Path("credentials.json"), token_file: Path = Path("token.json")) -> GDriveClient:
        creds = None
        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        if creds and creds.valid:
            return cls(creds)
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not credentials_file.exists():
                raise FileNotFoundError(f"credentials.json not found at {credentials_file}")
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)
        token_file.write_text(creds.to_json(), encoding="utf-8")
        return cls(creds)

    @classmethod
    def from_service_account(cls, key_path: Path) -> GDriveClient:
        creds = service_account.Credentials.from_service_account_file(str(key_path), scopes=SCOPES)
        return cls(creds)

    def _session(self) -> AuthorizedSession:
        if not hasattr(self._tls, "session"):
            self._tls.session = AuthorizedSession(self._credentials)
        return self._tls.session

    def get_about(self) -> dict:
        return self._retry(lambda: self._service.about().get(fields="user,storageQuota").execute())

    def ensure_folder_path(self, path: str, root_id: str = "root") -> str:
        parts = [p for p in path.split("/") if p]
        parent = root_id
        built = ""
        for part in parts:
            built = f"{built}/{part}" if built else part
            cache_key = f"{parent}/{part}"
            if cache_key in self._folder_cache:
                parent = self._folder_cache[cache_key]
                continue
            parent = self._get_or_create_folder(part, parent)
            self._folder_cache[cache_key] = parent
        return parent

    def _get_or_create_folder(self, name: str, parent_id: str) -> str:
        escaped = name.replace("'", "\\'")
        q = f"name='{escaped}' and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        result = self._retry(lambda: self._files.list(q=q, fields="files(id)", pageSize=1).execute())
        files = result.get("files", [])
        if files:
            return files[0]["id"]
        meta = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
        created = self._retry(lambda: self._files.create(body=meta, fields="id").execute())
        logger.info("Created folder: %s", name)
        return created["id"]

    def file_exists(self, name: str, parent_id: str) -> str | None:
        escaped = name.replace("'", "\\'")
        q = f"name='{escaped}' and '{parent_id}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'"
        result = self._retry(lambda: self._files.list(q=q, fields="files(id)", pageSize=1).execute())
        files = result.get("files", [])
        return files[0]["id"] if files else None

    def upload_from_buffer(self, buf, filename: str, parent_id: str, size: int, progress_cb=None) -> str:
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        session = self._session()

        init = self._req_retry(lambda: session.request(
            "POST", f"{_UPLOAD_BASE}?uploadType=resumable",
            json={"name": filename, "parents": [parent_id]},
            headers={"X-Upload-Content-Type": mime, "X-Upload-Content-Length": str(size)},
        ))
        init.raise_for_status()
        upload_url = init.headers["Location"]

        if size == 0:
            resp = self._req_retry(lambda: session.put(upload_url, data=b"", headers={"Content-Range": "bytes */0", "Content-Type": mime}))
            resp.raise_for_status()
            return resp.json()["id"]

        uploaded = 0
        buf.seek(0)
        while uploaded < size:
            chunk = buf.read(_CHUNK_SIZE)
            if not chunk:
                break
            end = uploaded + len(chunk) - 1
            resp = self._req_retry(lambda c=chunk, u=uploaded, e=end: session.put(
                upload_url, data=c,
                headers={"Content-Range": f"bytes {u}-{e}/{size}", "Content-Type": mime},
            ))
            if resp.status_code in (200, 201):
                if progress_cb:
                    progress_cb(size)
                return resp.json()["id"]
            if resp.status_code == 308:
                rng = resp.headers.get("Range", "")
                uploaded = (int(rng.split("-")[1]) + 1) if rng else 0
                buf.seek(uploaded)
                if progress_cb:
                    progress_cb(uploaded)
            else:
                resp.raise_for_status()
        raise RuntimeError(f"Upload ended without 200/201 for {filename}")

    def _req_retry(self, call):
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = call()
                if resp.status_code not in _RETRYABLE:
                    return resp
                wait = _BACKOFF[attempt] + random.uniform(0, 0.5)
                logger.warning("Drive API %s (attempt %d), retry in %.1fs", resp.status_code, attempt + 1, wait)
                time.sleep(wait)
            except Exception as exc:
                last_exc = exc
                time.sleep(_BACKOFF[attempt])
        if last_exc:
            raise last_exc
        return call()

    def _retry(self, call):
        last_exc = None
        for attempt, wait in enumerate(_BACKOFF):
            try:
                return call()
            except HttpError as exc:
                if exc.status_code in _RETRYABLE:
                    time.sleep(wait + random.uniform(0, 0.5))
                    last_exc = exc
                else:
                    raise
        raise last_exc
