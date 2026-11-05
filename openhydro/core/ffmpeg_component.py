"""
OpenHydro's own ffmpeg (Windows), downloaded the first time a download needs it
yt-dlp needs ffmpeg to merge a video stream with its audio (every quality a site does not serve as
one file, i.e. most of YouTube) and to make MP3s; without it yt-dlp aborts those downloads. Nothing is
bundled with OpenHydro: this machine fetches the build yt-dlp recommends, once, from its GitHub
release, verified against the SHA-256 GitHub publishes for it.

    <app data>/components/ffmpeg/bin/   ffmpeg.exe, ffprobe.exe and their DLLs
    <app data>/components/ffmpeg/info.json
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from typing import Callable, Dict, Optional

from .security import get_app_data_dir

RELEASE_API = "https://api.github.com/repos/yt-dlp/FFmpeg-Builds/releases/latest"
ASSET_NAME = "ffmpeg-master-latest-win64-gpl-shared.zip"
SKIPPED_FILES = {"ffplay.exe"}  # only ffmpeg and ffprobe are used

_lock = threading.Lock()
_progress = {"done": 0, "total": 0}  # of the download in progress (while _lock is held)


def supported() -> bool:
    return sys.platform == "win32"


def component_dir() -> str:
    return os.path.join(get_app_data_dir(), "components", "ffmpeg")


def installed_path() -> Optional[str]:
    exe = os.path.join(component_dir(), "bin", "ffmpeg.exe")
    return exe if os.path.isfile(exe) else None


def installed_info() -> Optional[Dict]:
    try:
        with open(os.path.join(component_dir(), "info.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status() -> Dict:
    """For the settings page: the ffmpeg OpenHydro uses, if any, and a download in progress."""
    from .hls import HLSDownloader
    path = HLSDownloader.find_ffmpeg()
    own = installed_path()
    is_own = bool(path and own and os.path.samefile(path, own))
    installing = _lock.locked()
    return {
        "supported": supported(),
        "available": bool(path),
        "source": None if not path else "openhydro" if is_own else "system",
        "version": (installed_info() or {}).get("version") if is_own else None,
        "installing": installing,
        "progress": dict(_progress) if installing else None,
    }


def _get_json(url: str) -> Dict:
    from .worker import shared_ssl_context
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=30, context=shared_ssl_context()) as resp:
        return json.load(resp)


def _open(url: str):
    from .worker import shared_ssl_context
    return urllib.request.urlopen(url, timeout=60, context=shared_ssl_context())


def release_asset() -> Dict:
    """Download URL, size and SHA-256 of the current build (from GitHub's API, not the file's host)."""
    release = _get_json(RELEASE_API)
    asset = next((a for a in release.get("assets", []) if a.get("name") == ASSET_NAME), None)
    if not asset:
        raise RuntimeError(f"{ASSET_NAME} is missing from the latest ffmpeg release")
    algorithm, _, digest = (asset.get("digest") or "").partition(":")
    if algorithm != "sha256" or len(digest) != 64:
        raise RuntimeError("GitHub did not publish a SHA-256 for the ffmpeg build")
    return {"url": asset["browser_download_url"], "size": int(asset.get("size") or 0), "sha256": digest.lower()}


def _version(exe: str) -> str:
    """Runs the new ffmpeg: a build that does not start is never installed."""
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    result = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=30, creationflags=flags)
    first_line = (result.stdout or "").splitlines()[0] if result.stdout else ""
    if result.returncode != 0 or not first_line.startswith("ffmpeg version"):
        raise RuntimeError(f"the downloaded ffmpeg does not run: {(result.stderr or first_line)[:200]}")
    return first_line.split()[2]


def ensure(on_progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Path of OpenHydro's ffmpeg.exe, downloading it first if needed (one download at a time)."""
    with _lock:
        existing = installed_path()
        if existing:
            return existing
        if not supported():
            raise RuntimeError("OpenHydro downloads ffmpeg on Windows only; install it from your system's packages")

        asset = release_asset()
        root = component_dir()
        os.makedirs(root, exist_ok=True)
        archive = os.path.join(root, "download.zip.part")
        building = os.path.join(root, "bin.new")
        try:
            digest = hashlib.sha256()
            done = 0
            _progress.update(done=0, total=asset["size"])
            with _open(asset["url"]) as resp, open(archive, "wb") as out:
                while True:
                    block = resp.read(1024 * 1024)
                    if not block:
                        break
                    out.write(block)
                    digest.update(block)
                    done += len(block)
                    _progress["done"] = done
                    if on_progress:
                        on_progress(done, asset["size"] or done)
            if digest.hexdigest() != asset["sha256"]:
                raise ValueError("the downloaded ffmpeg does not match GitHub's SHA-256")

            shutil.rmtree(building, ignore_errors=True)
            os.makedirs(building)
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    # Only "<build>/bin/<file>", written by plain name: no other path in the archive
                    # (not even a Windows "..\") can place a file anywhere
                    parts = member.filename.replace("\\", "/").split("/")
                    if member.is_dir() or len(parts) != 3 or parts[1] != "bin" or parts[0] in ("", ".", ".."):
                        continue
                    name = parts[2]
                    if name in SKIPPED_FILES or name in ("", ".", "..") or ":" in name:
                        continue
                    with bundle.open(member) as src, open(os.path.join(building, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
            version = _version(os.path.join(building, "ffmpeg.exe"))

            final = os.path.join(root, "bin")
            shutil.rmtree(final, ignore_errors=True)
            os.replace(building, final)
            with open(os.path.join(root, "info.json"), "w", encoding="utf-8") as f:
                json.dump({"version": version, "sha256": asset["sha256"], "source": asset["url"],
                           "installed_at": time.time()}, f)
            return os.path.join(final, "ffmpeg.exe")
        finally:
            shutil.rmtree(building, ignore_errors=True)
            try:
                os.remove(archive)
            except OSError:
                pass
