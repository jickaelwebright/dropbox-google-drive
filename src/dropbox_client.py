"""Dropbox API client — OAuth2 PKCE flow, recursive listing, streaming download."""

from __future__ import annotations

import io
import json
import logging
import time
from pathlib import Path

import dropbox
from dropbox.exceptions import ApiError, AuthError
from dropbox.files import FileMetadata, FolderMetadata

logger = logging.getLogger(__name__)

TOKEN_FILE = Path("dropbox_token.json")
_MAX_RETRIES = 3
_BACKOFF = [1, 3, 8]


class DropboxClient:
    def __init__(self, app_key: str, app_secret: str = "") -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._dbx: dropbox.Dropbox | None = None

    def authenticate(self) -> None:
        if TOKEN_FILE.exists():
            data = json.loads(TOKEN_FILE.read_text())
            refresh_token = data.get("refresh_token")
            if refresh_token:
                self._dbx = dropbox.Dropbox(
                    oauth2_refresh_token=refresh_token,
                    app_key=self._app_key,
                    app_secret=self._app_secret if self._app_secret else None,
                )
                try:
                    acct = self._dbx.users_get_current_account()
                    logger.info("Authenticated as %s", acct.name.display_name)
                    return
                except AuthError:
                    logger.warning("Cached token invalid, re-authenticating")

        flow = dropbox.DropboxOAuth2FlowNoRedirect(
            self._app_key,
            consumer_secret=self._app_secret if self._app_secret else None,
            use_pkce=True,
            token_access_type="offline",
        )
        url = flow.start()
        print(f"\n1. Go to: {url}")
        print("2. Click 'Allow' and copy the authorization code.")
        code = input("3. Enter the code here: ").strip()

        result = flow.finish(code)
        self._dbx = dropbox.Dropbox(
            oauth2_refresh_token=result.refresh_token,
            app_key=self._app_key,
            app_secret=self._app_secret if self._app_secret else None,
        )
        TOKEN_FILE.write_text(json.dumps({
            "refresh_token": result.refresh_token,
            "access_token": result.access_token,
        }))
        acct = self._dbx.users_get_current_account()
        logger.info("Authenticated as %s", acct.name.display_name)

    @property
    def dbx(self) -> dropbox.Dropbox:
        if not self._dbx:
            raise RuntimeError("Not authenticated. Call authenticate() first.")
        return self._dbx

    def get_space_usage(self) -> dict:
        usage = self.dbx.users_get_space_usage()
        used = usage.used
        allocated = usage.allocation.get_individual().allocated if hasattr(usage.allocation, "get_individual") else 0
        return {"used_bytes": used, "allocated_bytes": allocated}

    def list_folder_recursive(self, path: str = "") -> list[FileMetadata]:
        files: list[FileMetadata] = []
        try:
            result = self._retry(lambda: self.dbx.files_list_folder(path, recursive=True))
        except ApiError as e:
            logger.error("Failed listing %s: %s", path, e)
            return files

        while True:
            for entry in result.entries:
                if isinstance(entry, FileMetadata):
                    files.append(entry)
            if not result.has_more:
                break
            result = self._retry(lambda: self.dbx.files_list_folder_continue(result.cursor))
        return files

    def list_shared_folders(self) -> list[dict]:
        folders = []
        result = self._retry(lambda: self.dbx.sharing_list_folders())
        while True:
            for sf in result.entries:
                folders.append({
                    "name": sf.name,
                    "shared_folder_id": sf.shared_folder_id,
                    "path_lower": sf.path_lower if hasattr(sf, "path_lower") else None,
                })
            if result.cursor is None:
                break
            result = self._retry(lambda: self.dbx.sharing_list_folders_continue(result.cursor))
        return folders

    def download_to_buffer(self, path: str) -> tuple[io.BytesIO, int]:
        md, resp = self._retry(lambda: self.dbx.files_download(path))
        buf = io.BytesIO(resp.content)
        return buf, md.size

    def download_to_file(self, path: str, local_path: Path) -> int:
        md, resp = self._retry(lambda: self.dbx.files_download(path))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(resp.content)
        return md.size

    def _retry(self, call):
        for attempt in range(_MAX_RETRIES):
            try:
                return call()
            except Exception as e:
                if attempt == _MAX_RETRIES - 1:
                    raise
                wait = _BACKOFF[attempt]
                logger.warning("Dropbox API error (attempt %d/%d): %s, retry in %ds", attempt + 1, _MAX_RETRIES, e, wait)
                time.sleep(wait)
