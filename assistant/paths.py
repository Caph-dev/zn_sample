"""Platform-specific paths for assistant-owned user data."""
from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIR_NAME = "ZnSampleAssistant"
USER_SUBDIRECTORIES = ("config", "exports", "logs", "backups", "runtime")


def user_data_dir() -> Path:
    """Return the platform-standard application data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME


def ensure_user_dirs() -> Path:
    """Create all assistant-owned directories and return the application root."""
    application_directory = user_data_dir()
    application_directory.mkdir(parents=True, exist_ok=True)
    for subdirectory_name in USER_SUBDIRECTORIES:
        (application_directory / subdirectory_name).mkdir(parents=True, exist_ok=True)
    return application_directory


def database_path() -> Path:
    return user_data_dir() / "assistant.sqlite3"


def runtime_dir() -> Path:
    return user_data_dir() / "runtime"
