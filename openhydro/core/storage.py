"""
OpenHydro Storage Manager
Pre-allocation, file space management, resumption state persistence, and auto-discovery.
"""

import json
import os
import glob
from typing import Optional, Dict, Any, List
from .task import DownloadTask, Chunk, TaskStatus


class StorageManager:
    CATEGORY_FOLDERS = {
        "video": "Videos",
        "program": "Programs",
        "audio": "Music",
        "compressed": "Compressed",
        "document": "Documents",
        "torrent": "Torrents",
        "other": "",
    }

    @classmethod
    def get_category_dir(cls, base_dir: str, category: str) -> str:
        """Returns the specific category subfolder path inside base_dir."""
        sub = cls.CATEGORY_FOLDERS.get(category, "")
        if sub:
            target = os.path.join(base_dir, sub)
            os.makedirs(target, exist_ok=True)
            return target
        return base_dir

    @staticmethod
    def get_default_download_dir() -> str:
        # Standard user downloads folder
        home = os.path.expanduser("~")
        downloads = os.path.join(home, "Downloads")
        if not os.path.exists(downloads):
            downloads = os.path.join(home, "OpenHydroDownloads")
            os.makedirs(downloads, exist_ok=True)
        return downloads

    @staticmethod
    def get_meta_path(target_path: str) -> str:
        return target_path + ".hydro.meta"

    @staticmethod
    def get_part_path(target_path: str) -> str:
        return target_path + ".hydro.part"

    @classmethod
    def extract_archive(cls, archive_path: str, extract_dir: Optional[str] = None) -> str:
        """
        Safely extracts ZIP, TAR, TGZ compressed archives into a dedicated destination folder.
        """
        import zipfile
        import tarfile

        if not os.path.exists(archive_path):
            raise FileNotFoundError(f"Archive not found: {archive_path}")

        base_name, _ = os.path.splitext(os.path.basename(archive_path))
        if base_name.endswith(".tar"):
            base_name, _ = os.path.splitext(base_name)

        if not extract_dir:
            extract_dir = os.path.join(os.path.dirname(archive_path), base_name)
        os.makedirs(extract_dir, exist_ok=True)

        if zipfile.is_zipfile(archive_path):
            with zipfile.ZipFile(archive_path, 'r') as zf:
                zf.extractall(extract_dir)
            return extract_dir
        elif tarfile.is_tarfile(archive_path):
            with tarfile.open(archive_path, 'r:*') as tf:
                tf.extractall(extract_dir)
            return extract_dir
        else:
            raise ValueError("صيغة الأرشيف غير مدعومة حالياً (يدعم .zip و .tar و .tar.gz)")

    @classmethod
    def preallocate_file(cls, filepath: str, total_bytes: int):
        """
        Pre-allocates the file on disk to guarantee continuous space and avoid fragmentation.
        """
        dir_path = os.path.dirname(filepath)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        # Open in binary write/update mode and set exact file size
        mode = "r+b" if os.path.exists(filepath) else "w+b"
        with open(filepath, mode) as f:
            if total_bytes > 0:
                f.truncate(total_bytes)
                f.flush()

    @classmethod
    def save_task_meta(cls, task: DownloadTask):
        """
        Persists download metadata to allow pause/resume even across system reboots.
        """
        if not task.save_path:
            return
        meta_path = cls.get_meta_path(task.save_path)
        try:
            data = {
                "id": task.id,
                "url": task.url,
                "filename": task.filename,
                "save_path": task.save_path,
                "total_bytes": task.total_bytes,
                "supports_ranges": task.supports_ranges,
                "num_connections": task.num_connections,
                "status": task.status.value,
                "category": task.category or task.determine_category(),
                "custom_headers": task.custom_headers,
                "referrer": task.referrer,
                "is_hls": getattr(task, "is_hls", False),
                "hls_info": getattr(task, "hls_info", None),
                "is_extractor": getattr(task, "is_extractor", False),
                "selected_format": getattr(task, "selected_format", None),
                "is_torrent": getattr(task, "is_torrent", False),
                "torrent_gid": getattr(task, "torrent_gid", None),
                "created_at": getattr(task, "created_at", None),
                "completed_at": getattr(task, "completed_at", None),
                "chunks": [c.to_dict() for c in task.chunks],
            }
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[StorageManager] Error saving task metadata: {e}")

    @classmethod
    def load_task_meta(cls, meta_path: str) -> Optional[DownloadTask]:
        """
        Loads task state from a given .hydro.meta file path.
        """
        if not os.path.exists(meta_path):
            return None

        try:
            import time
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            task = DownloadTask(
                url=data["url"],
                filename=data["filename"],
                save_path=data["save_path"],
                id=data["id"],
                total_bytes=data.get("total_bytes", 0),
                supports_ranges=data.get("supports_ranges", False),
                num_connections=data.get("num_connections", 8),
                category=data.get("category") or "other",
                custom_headers=data.get("custom_headers", {}),
                referrer=data.get("referrer"),
                is_hls=data.get("is_hls", False),
                hls_info=data.get("hls_info"),
                is_extractor=data.get("is_extractor", False),
                selected_format=data.get("selected_format"),
                is_torrent=data.get("is_torrent", False),
                torrent_gid=data.get("torrent_gid"),
                created_at=data.get("created_at") or time.time(),
                completed_at=data.get("completed_at"),
            )
            task.category = task.determine_category()

            # Reconstruct chunks
            chunks = []
            for cd in data.get("chunks", []):
                chunk = Chunk(
                    id=cd["id"],
                    start_byte=cd["start_byte"],
                    end_byte=cd["end_byte"],
                    current_byte=cd["current_byte"],
                    status="completed" if cd["status"] == "completed" or cd["current_byte"] > cd["end_byte"] else "idle",
                )
                chunks.append(chunk)
            task.chunks = chunks

            # Check if actual final file already exists
            if os.path.exists(task.save_path) and not os.path.exists(cls.get_part_path(task.save_path)):
                task.status = TaskStatus.COMPLETED
            else:
                task.status = TaskStatus.PAUSED

            return task
        except Exception as e:
            print(f"[StorageManager] Error loading metadata from {meta_path}: {e}")
            return None

    @classmethod
    def scan_and_load_tasks(cls, download_dir: str) -> List[DownloadTask]:
        """
        Scans download_dir and category subfolders for all .hydro.meta files and restores tasks so downloads persist.
        """
        restored = []
        if not os.path.exists(download_dir):
            return restored

        pattern = os.path.join(download_dir, "**", "*.hydro.meta")
        for meta_file in glob.glob(pattern, recursive=True):
            task = cls.load_task_meta(meta_file)
            if task:
                restored.append(task)
        return restored

    @classmethod
    def finalize_download(cls, task: DownloadTask):
        """
        Renames .part file to final file and updates persistent task metadata.
        """
        part_path = cls.get_part_path(task.save_path)

        if os.path.exists(part_path):
            if os.path.exists(task.save_path):
                try:
                    os.remove(task.save_path)
                except OSError:
                    pass
            os.rename(part_path, task.save_path)

        # Update metadata to preserve completed task history across restarts
        task.status = TaskStatus.COMPLETED
        cls.save_task_meta(task)
