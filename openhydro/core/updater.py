"""
OpenHydro Auto-Updater System
Checks GitHub Releases for new versions, compares semantic versions,
downloads installers via OpenHydro turbo engine, and launches updates.
"""

import os
import re
import sys
import asyncio
import subprocess
from typing import Dict, Optional, Tuple
import httpx

REPO_OWNER = "AshrafCode519"
REPO_NAME = "OpenHydro"
GITHUB_API_BASE = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}"


def parse_version(v_str: str) -> Tuple[int, ...]:
    """Extracts numeric tuple from version string (e.g. 'v1.1.2' -> (1, 1, 2))."""
    if not v_str:
        return (0, 0, 0)
    cleaned = re.sub(r'^[vV]', '', str(v_str).strip())
    # Extract only digits and dots
    parts = re.findall(r'\d+', cleaned)
    if not parts:
        return (0, 0, 0)
    return tuple(int(p) for p in parts[:4])


def is_newer_version(latest_str: str, current_str: str) -> bool:
    """Returns True if latest_str is strictly newer than current_str."""
    latest = parse_version(latest_str)
    current = parse_version(current_str)
    return latest > current


class UpdateChecker:
    @staticmethod
    async def check_for_updates(current_version: str) -> Dict:
        """
        Queries GitHub API for the latest release or tags and checks if an update exists.
        """
        headers = {
            "User-Agent": f"OpenHydro-App/{current_version}",
            "Accept": "application/vnd.github.v3+json",
        }

        timeout = httpx.Timeout(10.0, connect=5.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                # 1. Try /releases/latest first
                resp = await client.get(f"{GITHUB_API_BASE}/releases/latest", headers=headers)
                
                release_data = None
                if resp.status_code == 200:
                    release_data = resp.json()
                elif resp.status_code == 404:
                    # If /releases/latest is not published yet, check all /releases
                    all_resp = await client.get(f"{GITHUB_API_BASE}/releases", headers=headers)
                    if all_resp.status_code == 200 and all_resp.json():
                        release_data = all_resp.json()[0]

                # If releases exist, parse release data
                if release_data:
                    tag_name = release_data.get("tag_name", "").strip()
                    latest_ver = tag_name.lstrip("vV")
                    has_update = is_newer_version(latest_ver, current_version)

                    # Find best asset for current OS
                    download_url = release_data.get("html_url")
                    asset_name = ""
                    asset_size = 0

                    assets = release_data.get("assets", [])
                    is_windows = sys.platform.startswith("win")
                    is_linux = sys.platform.startswith("linux")

                    if is_windows:
                        # 1. Strictly prioritize dedicated Windows Setup Installer (.exe)
                        setup_asset = next(
                            (a for a in assets if a.get("name", "").lower().endswith(".exe")),
                            None
                        )
                        if setup_asset:
                            download_url = setup_asset.get("browser_download_url")
                            asset_name = setup_asset.get("name")
                            asset_size = setup_asset.get("size", 0)
                        else:
                            # 2. Fallback to portable .zip only if no installer exists
                            zip_asset = next(
                                (a for a in assets if a.get("name", "").lower().endswith(".zip")),
                                None
                            )
                            if zip_asset:
                                download_url = zip_asset.get("browser_download_url")
                                asset_name = zip_asset.get("name")
                                asset_size = zip_asset.get("size", 0)
                    elif is_linux:
                        linux_asset = next(
                            (a for a in assets if a.get("name", "").lower().endswith(".tar.gz") or a.get("name", "").lower().endswith(".appimage")),
                            None
                        )
                        if linux_asset:
                            download_url = linux_asset.get("browser_download_url")
                            asset_name = linux_asset.get("name")
                            asset_size = linux_asset.get("size", 0)

                    return {
                        "has_update": has_update,
                        "current_version": current_version,
                        "latest_version": latest_ver,
                        "tag_name": tag_name,
                        "release_name": release_data.get("name") or f"OpenHydro {tag_name}",
                        "release_notes": release_data.get("body") or "تحديث جديد يتضمن تحسينات وميزات جديدة.",
                        "published_at": release_data.get("published_at") or "",
                        "html_url": release_data.get("html_url") or f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases",
                        "download_url": download_url,
                        "asset_name": asset_name or f"OpenHydro-{tag_name}.zip",
                        "asset_size": asset_size,
                    }

                # 2. Fallback: check /tags if no releases have been published yet
                tags_resp = await client.get(f"{GITHUB_API_BASE}/tags", headers=headers)
                if tags_resp.status_code == 200 and tags_resp.json():
                    latest_tag = tags_resp.json()[0].get("name", "")
                    latest_ver = latest_tag.lstrip("vV")
                    has_update = is_newer_version(latest_ver, current_version)

                    return {
                        "has_update": has_update,
                        "current_version": current_version,
                        "latest_version": latest_ver,
                        "tag_name": latest_tag,
                        "release_name": f"OpenHydro {latest_tag}",
                        "release_notes": "يتوفر إصدار أحدث على GitHub يتضمن آخر التحديثات والإصلاحات.",
                        "published_at": "",
                        "html_url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/tag/{latest_tag}",
                        "download_url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/tag/{latest_tag}",
                        "asset_name": "",
                        "asset_size": 0,
                    }

                # No updates found or cannot reach GitHub
                return {
                    "has_update": False,
                    "current_version": current_version,
                    "latest_version": current_version,
                    "release_name": f"OpenHydro v{current_version}",
                    "release_notes": "أنت تستخدم أحدث إصدار متاح حالياً.",
                    "html_url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}",
                    "download_url": "",
                }

        except Exception as e:
            return {
                "has_update": False,
                "current_version": current_version,
                "error": str(e),
                "html_url": f"https://github.com/{REPO_OWNER}/{REPO_NAME}",
            }

    @staticmethod
    def launch_installer(file_path: str, silent: bool = False) -> bool:
        """
        Launches the downloaded installer or opens the folder containing the update.
        Supports silent background updates via /VERYSILENT.
        """
        if not file_path or not os.path.exists(file_path):
            return False

        try:
            if sys.platform.startswith("win"):
                if file_path.lower().endswith(".exe"):
                    args = [file_path]
                    if silent:
                        args.extend(["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
                    # Launch installer detached so OpenHydro can be closed safely
                    subprocess.Popen(
                        args,
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                        close_fds=True,
                    )
                    return True
                else:
                    # Open Explorer and select the downloaded archive
                    subprocess.Popen(f'explorer.exe /select,"{os.path.abspath(file_path)}"')
                    return True
            elif sys.platform.startswith("linux"):
                subprocess.Popen(["xdg-open", os.path.dirname(file_path)])
                return True
        except Exception as e:
            print(f"[Updater] Error launching installer: {e}")
            return False
        return False

