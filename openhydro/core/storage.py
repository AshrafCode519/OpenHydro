"""
OpenHydro Storage Manager
Pre-allocation, file space management, resumption state persistence, and auto-discovery.
"""

import json
import os
import glob
import time
import sqlite3
import threading
from typing import Optional, Dict, Any, List, Tuple
from .task import DownloadTask, Chunk, TaskStatus

# Status a saved task comes back with after a restart (anything else: PAUSED). Downloads that were
# running or waiting when the app closed (or crashed) go back in the queue; the engine pauses them
# instead when auto-resume is off. A download the user paused stays paused.
RESTORED_STATUS = {
    TaskStatus.COMPLETED.value: TaskStatus.COMPLETED,
    TaskStatus.ERROR.value: TaskStatus.ERROR,
    TaskStatus.CANCELLED.value: TaskStatus.CANCELLED,
    TaskStatus.DOWNLOADING.value: TaskStatus.QUEUED,
    TaskStatus.QUEUED.value: TaskStatus.QUEUED,
}


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
                if hasattr(tarfile, "data_filter"):
                    # Refuses absolute paths, "../" escapes, links pointing outside and device files
                    tf.extractall(extract_dir, filter="data")
                else:
                    base = os.path.realpath(extract_dir)
                    for member in tf.getmembers():
                        target = os.path.realpath(os.path.join(extract_dir, member.name))
                        if os.path.commonpath([base, target]) != base or member.issym() or member.islnk() or member.isdev():
                            raise ValueError(f"عنصر غير آمن داخل الأرشيف: {member.name}")
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

    @staticmethod
    def task_to_record(task: DownloadTask) -> Dict[str, Any]:
        """Serializable snapshot of everything needed to restore and resume a task."""
        return {
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
            "is_hls": task.is_hls,
            "hls_info": task.hls_info,
            "is_extractor": task.is_extractor,
            "selected_format": task.selected_format,
            "is_torrent": task.is_torrent,
            "speed_limit_kbps": task.speed_limit_kbps,
            "torrent_gid": task.torrent_gid,
            "created_at": task.created_at,
            "completed_at": task.completed_at,
            "error_message": task.error_message,
            "chunks": [c.to_dict() for c in task.chunks],
        }

    @classmethod
    def task_from_record(cls, data: Dict[str, Any]) -> DownloadTask:
        task = DownloadTask(
            url=data["url"],
            filename=data["filename"],
            save_path=data["save_path"],
            id=data["id"],
            total_bytes=data.get("total_bytes", 0),
            supports_ranges=data.get("supports_ranges", False),
            num_connections=data.get("num_connections", 8),
            custom_headers=data.get("custom_headers", {}),
            referrer=data.get("referrer"),
            is_hls=data.get("is_hls", False),
            hls_info=data.get("hls_info"),
            is_extractor=data.get("is_extractor", False),
            selected_format=data.get("selected_format"),
            is_torrent=data.get("is_torrent", False),
            speed_limit_kbps=int(data.get("speed_limit_kbps") or 0),
            torrent_gid=data.get("torrent_gid"),
            created_at=data.get("created_at") or time.time(),
            completed_at=data.get("completed_at"),
            error_message=data.get("error_message"),
        )
        # Keep the category chosen at add time (e.g. subtitles are "document", not "other")
        task.category = data.get("category") or task.determine_category()

        # Reconstruct chunks
        task.chunks = [
            Chunk(
                id=cd["id"],
                start_byte=cd["start_byte"],
                end_byte=cd["end_byte"],
                current_byte=cd["current_byte"],
                status="completed" if cd["status"] == "completed" or cd["current_byte"] > cd["end_byte"] else "idle",
            )
            for cd in data.get("chunks", [])
        ]

        # Trust the saved state: torrents write straight to save_path, so "the file exists"
        # does not mean "finished". Only very old records without a status fall back to that guess.
        saved_status = data.get("status")
        if saved_status:
            task.status = RESTORED_STATUS.get(saved_status, TaskStatus.PAUSED)
        elif os.path.exists(task.save_path) and not os.path.exists(cls.get_part_path(task.save_path)):
            task.status = TaskStatus.COMPLETED
        else:
            task.status = TaskStatus.PAUSED
        return task

    # --- Legacy .hydro.meta sidecar files (pre-TaskStore); kept to migrate existing installs ---

    @classmethod
    def save_task_meta(cls, task: DownloadTask):
        """Writes the legacy sidecar metadata file next to the download."""
        if not task.save_path:
            return
        try:
            with open(cls.get_meta_path(task.save_path), "w", encoding="utf-8") as f:
                json.dump(cls.task_to_record(task), f, indent=2)
        except Exception as e:
            print(f"[StorageManager] Error saving task metadata: {e}")

    @classmethod
    def load_task_meta(cls, meta_path: str) -> Optional[DownloadTask]:
        """Loads task state from a legacy .hydro.meta file."""
        if not os.path.exists(meta_path):
            return None
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                return cls.task_from_record(json.load(f))
        except Exception as e:
            print(f"[StorageManager] Error loading metadata from {meta_path}: {e}")
            return None

    @classmethod
    def scan_legacy_meta(cls, download_dir: str) -> List[Tuple[str, DownloadTask]]:
        """Finds legacy .hydro.meta files in download_dir and its subfolders, with their tasks."""
        found = []
        if not os.path.exists(download_dir):
            return found
        pattern = os.path.join(download_dir, "**", "*.hydro.meta")
        for meta_file in glob.glob(pattern, recursive=True):
            task = cls.load_task_meta(meta_file)
            if task:
                found.append((meta_file, task))
        return found

    @classmethod
    def scan_and_load_tasks(cls, download_dir: str) -> List[DownloadTask]:
        return [task for _, task in cls.scan_legacy_meta(download_dir)]

    @classmethod
    def finalize_download(cls, task: DownloadTask):
        """
        Renames .part file to final file (the caller persists the task).
        """
        part_path = cls.get_part_path(task.save_path)

        if os.path.exists(part_path):
            if os.path.exists(task.save_path):
                try:
                    os.remove(task.save_path)
                except OSError:
                    pass
            os.rename(part_path, task.save_path)

        task.status = TaskStatus.COMPLETED


class TaskStore:
    """
    Central SQLite store for download tasks, kept in the app data folder (one row per task id).
    Replaces the .hydro.meta sidecar files, which cluttered the downloads folder, made removed
    tasks reappear, and duplicated a task whenever a torrent got renamed.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.Lock()
        # Saved from the server loop and from the tray/shutdown threads
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at REAL NOT NULL)"
            )
            self._conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
            self._conn.commit()

    def save(self, task: DownloadTask):
        try:
            payload = json.dumps(StorageManager.task_to_record(task))
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO tasks (id, data, updated_at) VALUES (?, ?, ?)",
                    (task.id, payload, time.time()),
                )
                self._conn.commit()
        except Exception as e:
            print(f"[TaskStore] Error saving task {task.id}: {e}")

    def delete(self, task_id: str):
        with self._lock:
            self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()

    def load_all(self) -> List[DownloadTask]:
        with self._lock:
            rows = self._conn.execute("SELECT id, data FROM tasks").fetchall()
        tasks = []
        for task_id, data in rows:
            try:
                tasks.append(StorageManager.task_from_record(json.loads(data)))
            except Exception as e:
                print(f"[TaskStore] Skipping unreadable task {task_id}: {e}")
        return tasks

    def get_setting(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str):
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()
