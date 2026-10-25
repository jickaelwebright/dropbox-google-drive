"""Configuration loader — INI file with env var overrides."""

from __future__ import annotations

import configparser
import os
from pathlib import Path

DEFAULT_CONFIG = Path("config.ini")


def load_config(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = {
        "dropbox_app_key": "",
        "dropbox_app_secret": "",
        "google_service_account": "",
        "dest_folder": "Dropbox Migration",
        "conflict": "skip",
        "include_shared": False,
    }

    if config_path.exists():
        parser = configparser.ConfigParser()
        parser.read(str(config_path))
        if parser.has_section("dropbox"):
            cfg["dropbox_app_key"] = parser.get("dropbox", "app_key", fallback="")
            cfg["dropbox_app_secret"] = parser.get("dropbox", "app_secret", fallback="")
        if parser.has_section("google"):
            cfg["google_service_account"] = parser.get("google", "service_account_key", fallback="")
        if parser.has_section("migration"):
            cfg["dest_folder"] = parser.get("migration", "dest_folder", fallback="Dropbox Migration")
            cfg["conflict"] = parser.get("migration", "conflict", fallback="skip")
            cfg["include_shared"] = parser.getboolean("migration", "include_shared", fallback=False)

    cfg["dropbox_app_key"] = os.environ.get("DROPBOX_APP_KEY", cfg["dropbox_app_key"])
    cfg["dropbox_app_secret"] = os.environ.get("DROPBOX_APP_SECRET", cfg["dropbox_app_secret"])

    return cfg
