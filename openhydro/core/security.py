"""
OpenHydro Local Security Helpers
Persistent per-install secrets (remote-control token, aria2 RPC secret) and
filename / directory sanitization for anything that reaches the disk.
"""

import os
import re
import sys
import json
import secrets
from typing import Optional

INVALID_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|\x00-\x1f]')


def get_app_data_dir() -> str:
    """Per-user application data folder (%LOCALAPPDATA%\\OpenHydro or ~/.config/openhydro)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        path = os.path.join(base, "OpenHydro")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
        path = os.path.join(base, "openhydro")
    os.makedirs(path, exist_ok=True)
    return path


def load_or_create_secret(name: str, secrets_path: Optional[str] = None) -> str:
    """
    Returns a random secret stored under `name`, generating and persisting it on first use
    so that QR links and the aria2 daemon keep working across restarts.
    """
    path = secrets_path or os.path.join(get_app_data_dir(), "secrets.json")
    data = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}

    value = data.get(name)
    if not value:
        value = secrets.token_urlsafe(24)
        data[name] = value
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            if os.name == "posix":
                os.chmod(path, 0o600)
        except OSError as e:
            print(f"[Security] Could not persist secret '{name}': {e}")
    return value


def sanitize_filename(name: Optional[str], default: str = "download.bin") -> str:
    """Reduces a user or server supplied name to a single safe path component (no traversal)."""
    cleaned = INVALID_FILENAME_CHARS.sub("_", name or "").strip().rstrip(". ")
    if cleaned in ("", ".", ".."):
        return default
    return cleaned


def is_within_directory(base_dir: str, target: str) -> bool:
    """True if `target` resolves to `base_dir` itself or a path inside it."""
    base = os.path.realpath(base_dir)
    resolved = os.path.realpath(target)
    try:
        return os.path.commonpath([base, resolved]) == base
    except ValueError:
        # Different drives on Windows
        return False
