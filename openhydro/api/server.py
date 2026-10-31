"""
OpenHydro FastAPI Application Server
Provides REST API endpoints and WebSocket telemetry.
"""

import os
import shutil
import asyncio
from contextlib import asynccontextmanager
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from openhydro.core import DownloadEngine, TaskStatus


class AddTaskRequest(BaseModel):
    url: str
    filename: Optional[str] = None
    save_dir: Optional[str] = None
    num_connections: int = Field(default=8, ge=1, le=32)
    custom_headers: Optional[Dict[str, str]] = None
    referrer: Optional[str] = None
    auto_start: bool = True
    format_id: Optional[str] = None


class SpeedLimitRequest(BaseModel):
    max_kbps: int = 0  # 0 = unlimited


class RefreshUrlRequest(BaseModel):
    url: str


class SchedulerRequest(BaseModel):
    max_active: int = Field(default=3, ge=1, le=10)
    scheduled_time: Optional[str] = None  # Format "HH:MM"
    auto_shutdown: bool = False


class PlaylistItemRequest(BaseModel):
    url: str
    title: str
    index: int
    download_url: Optional[str] = None
    is_gdrive: bool = False


class PlaylistProbeRequest(BaseModel):
    url: str


class PlaylistDownloadRequest(BaseModel):
    playlist_title: str
    format_id: str = "bestvideo[height<=720]+bestaudio/best"
    items: List[PlaylistItemRequest]
    num_connections: int = Field(default=8, ge=1, le=32)


class UpdateDownloadRequest(BaseModel):
    download_url: str
    filename: Optional[str] = None


class UpdateInstallRequest(BaseModel):
    task_id: Optional[str] = None
    file_path: Optional[str] = None


def create_app(engine: DownloadEngine) -> FastAPI:
    from .websocket import TelemetryHub

    hub = TelemetryHub()
    app_state = {"loop": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_state["loop"] = asyncio.get_running_loop()
        await engine.scheduler.start()
        yield
        await engine.scheduler.stop()

    app = FastAPI(title="OpenHydro Core API", version="1.0.0", lifespan=lifespan)

    # Enable CORS and Private Network Access
    @app.middleware("http")
    async def add_pna_header(request, call_next):
        response = await call_next(request)
        response.headers["Access-Control-Allow-Private-Network"] = "true"
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    def engine_event_handler(event_type: str, task_dict: dict):
        running_loop = app_state["loop"]
        if running_loop and running_loop.is_running():
            asyncio.run_coroutine_threadsafe(
                hub.broadcast({"type": event_type, "data": task_dict}),
                running_loop,
            )

    engine.register_listener(engine_event_handler)

    # --- REST Endpoints ---

    @app.get("/api/probe")
    async def probe_url_endpoint(url: str = Query(..., description="Target file URL")):
        info = await engine.probe_url(url)
        return info

    @app.get("/api/playlist/probe")
    async def probe_playlist_endpoint(url: str = Query(..., description="Target playlist URL")):
        from openhydro.core.extractor import VideoExtractor
        try:
            info = await VideoExtractor.extract_playlist(url)
            return info
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/playlist/probe")
    async def probe_playlist_post_endpoint(req: PlaylistProbeRequest):
        from openhydro.core.extractor import VideoExtractor
        try:
            info = await VideoExtractor.extract_playlist(req.url)
            return info
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/playlist/download")
    async def download_playlist_endpoint(req: PlaylistDownloadRequest):
        import re
        sanitized_title = re.sub(r'[\\/*?:"<>|]', "_", req.playlist_title).strip() or "Playlist"
        # Determine playlist category: audio if audio format, else video
        playlist_cat = "audio" if req.format_id in ("audio_mp3", "audio_m4a") else "video"
        base_cat_dir = StorageManager.get_category_dir(engine.download_dir, playlist_cat)
        target_dir = os.path.join(base_cat_dir, sanitized_title)
        os.makedirs(target_dir, exist_ok=True)

        added_tasks = []
        for item in req.items:
            clean_item_title = re.sub(r'[\\/*?:"<>|]', "_", item.title).strip()
            _, existing_ext = os.path.splitext(clean_item_title)

            ext = ".mp4"
            if req.format_id == "audio_mp3":
                ext = ".mp3"
            elif req.format_id == "audio_m4a":
                ext = ".m4a"
            elif req.format_id == "subtitle_srt":
                ext = ".srt"
            elif req.format_id == "subtitle_txt":
                ext = ".txt"
            elif (existing_ext and len(existing_ext) <= 5) or item.is_gdrive:
                ext = ""

            fn = f"{item.index:02d} - {clean_item_title}{ext}"
            task_url = item.download_url or item.url
            task = await engine.add_download(
                url=task_url,
                filename=fn,
                save_dir=target_dir,
                num_connections=req.num_connections,
                auto_start=True,
                format_id=req.format_id,
            )
            added_tasks.append(task.to_dict())

        return {
            "status": "success",
            "enqueued_count": len(added_tasks),
            "folder": target_dir,
            "tasks": added_tasks,
        }

    @app.get("/api/tasks")
    async def list_tasks():
        return [task.to_dict() for task in engine.tasks.values()]

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str):
        task = engine.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task.to_dict()

    @app.post("/api/tasks")
    async def add_task(req: AddTaskRequest):
        task = await engine.add_download(
            url=req.url,
            filename=req.filename,
            save_dir=req.save_dir,
            num_connections=req.num_connections,
            custom_headers=req.custom_headers,
            referrer=req.referrer,
            auto_start=req.auto_start,
            format_id=req.format_id,
        )
        return task.to_dict()

    @app.post("/api/tasks/{task_id}/pause")
    async def pause_task(task_id: str):
        task = engine.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        await engine.pause_task(task_id)
        return {"status": "paused"}

    @app.post("/api/tasks/{task_id}/resume")
    async def resume_task(task_id: str):
        task = engine.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        await engine.resume_task(task_id)
        return {"status": "resuming"}

    @app.post("/api/tasks/{task_id}/refresh_url")
    async def refresh_url(task_id: str, req: RefreshUrlRequest):
        task = engine.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        success = await engine.refresh_task_url(task_id, req.url)
        return {"status": "updated" if success else "failed"}

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, delete_file: bool = False):
        task = engine.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        await engine.cancel_task(task_id, delete_file=delete_file)
        return {"status": "cancelled"}

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: str, delete_file: bool = False):
        task = engine.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        await engine.cancel_task(task_id, delete_file=delete_file)
        with engine._lock:
            if task_id in engine.tasks:
                del engine.tasks[task_id]
        return {"status": "deleted"}

    @app.post("/api/tasks/{task_id}/open_file")
    async def open_task_file(task_id: str):
        import subprocess
        task = engine.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="المهمة غير موجودة")
        if not os.path.exists(task.save_path):
            raise HTTPException(status_code=404, detail="الملف غير موجود على القرص")
        try:
            if sys.platform == "win32":
                os.startfile(task.save_path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", task.save_path])
            else:
                subprocess.Popen(["xdg-open", task.save_path])
            return {"status": "success", "path": task.save_path}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/tasks/{task_id}/open_folder")
    async def open_task_folder(task_id: str):
        import subprocess
        task = engine.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="المهمة غير موجودة")
        folder = os.path.dirname(task.save_path)
        if not os.path.exists(folder):
            raise HTTPException(status_code=404, detail="المجلد غير موجود على القرص")
        try:
            if sys.platform == "win32":
                if os.path.exists(task.save_path):
                    subprocess.Popen(f'explorer /select,"{os.path.abspath(task.save_path)}"')
                else:
                    os.startfile(folder)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", task.save_path] if os.path.exists(task.save_path) else ["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
            return {"status": "success", "folder": folder}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/settings/limiter")
    async def set_limiter(req: SpeedLimitRequest):
        bytes_per_sec = req.max_kbps * 1024
        engine.rate_limiter.set_limit(bytes_per_sec)
        return {"max_kbps": req.max_kbps}

    @app.get("/api/settings/limiter")
    async def get_limiter():
        return {"max_kbps": engine.rate_limiter.max_bytes_per_second // 1024}

    @app.post("/api/settings/scheduler")
    async def set_scheduler(req: SchedulerRequest):
        engine.scheduler.set_max_active(req.max_active)
        engine.scheduler.set_schedule_start(req.scheduled_time)
        engine.scheduler.set_auto_shutdown(req.auto_shutdown)
        return {
            "max_active": engine.scheduler.max_active_downloads,
            "scheduled_time": engine.scheduler.scheduled_time,
            "auto_shutdown": engine.scheduler.shutdown_on_complete,
        }

    @app.get("/api/settings/scheduler")
    async def get_scheduler():
        return {
            "max_active": engine.scheduler.max_active_downloads,
            "scheduled_time": engine.scheduler.scheduled_time,
            "auto_shutdown": engine.scheduler.shutdown_on_complete,
        }

    @app.get("/api/system")
    async def get_system_info():
        total, used, free = shutil.disk_usage(engine.download_dir)
        total_speed = sum(t.speed for t in engine.tasks.values() if t.status == TaskStatus.DOWNLOADING)
        return {
            "download_dir": engine.download_dir,
            "disk_total_bytes": total,
            "disk_free_bytes": free,
            "active_downloads": sum(1 for t in engine.tasks.values() if t.status == TaskStatus.DOWNLOADING),
            "total_speed": total_speed,
        }

    # Browser Extension Info & Explorer Launcher
    @app.get("/api/version")
    async def get_app_version():
        import openhydro
        return {"version": openhydro.__version__}

    @app.get("/api/updates/check")
    async def check_updates():
        import openhydro
        from openhydro.core.updater import UpdateChecker
        info = await UpdateChecker.check_for_updates(openhydro.__version__)
        return info

    @app.post("/api/updates/download")
    async def download_update(req: UpdateDownloadRequest):
        task = await engine.add_download(
            url=req.download_url,
            filename=req.filename,
            save_dir=engine.download_dir,
            num_connections=16,
            auto_start=True,
        )
        return task.to_dict()

    @app.post("/api/updates/install")
    async def install_update(req: UpdateInstallRequest):
        from openhydro.core.updater import UpdateChecker
        target_path = req.file_path
        if req.task_id and req.task_id in engine.tasks:
            target_path = engine.tasks[req.task_id].save_path

        if not target_path or not os.path.exists(target_path):
            raise HTTPException(status_code=404, detail="ملف التحديث غير موجود على القرص")

        success = UpdateChecker.launch_installer(target_path)
        return {"status": "success" if success else "failed", "path": target_path}

    @app.get("/api/extension/info")
    async def get_extension_info():
        import subprocess
        if getattr(sys, "frozen", False):
            base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
            exe_dir = os.path.dirname(sys.executable)
            candidates = [
                os.path.join(exe_dir, "extension"),
                os.path.join(exe_dir, "openhydro", "extension"),
                os.path.join(exe_dir, "_internal", "extension"),
                os.path.join(exe_dir, "_internal", "openhydro", "extension"),
                os.path.join(base_dir, "extension"),
                os.path.join(base_dir, "openhydro", "extension"),
                os.path.join(base_dir, "_internal", "extension"),
                os.path.join(base_dir, "_internal", "openhydro", "extension"),
            ]
            ext_dir = next((c for c in candidates if os.path.exists(c)), candidates[0])
        else:
            candidates = [
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "extension")),
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "openhydro", "extension")),
                os.path.abspath(os.path.join(os.getcwd(), "openhydro", "extension")),
                os.path.abspath(os.path.join(os.getcwd(), "extension")),
            ]
            ext_dir = next((c for c in candidates if os.path.exists(c)), candidates[0])

        import openhydro
        return {
            "path": ext_dir,
            "exists": os.path.exists(ext_dir),
            "version": openhydro.__version__
        }

    @app.post("/api/extension/open_folder")
    async def open_extension_folder():
        import subprocess
        info = await get_extension_info()
        ext_dir = info["path"]
        if not os.path.exists(ext_dir):
            os.makedirs(ext_dir, exist_ok=True)
            
        try:
            if sys.platform == "win32":
                os.startfile(ext_dir)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", ext_dir])
            else:
                subprocess.Popen(["xdg-open", ext_dir])
            return {"status": "success", "path": ext_dir}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # Browser extension / Remote Push trigger
    @app.post("/api/remote/push")
    async def remote_push(req: AddTaskRequest):
        async def _async_add():
            try:
                await engine.add_download(
                    url=req.url,
                    filename=req.filename,
                    save_dir=req.save_dir,
                    num_connections=req.num_connections,
                    custom_headers=req.custom_headers,
                    referrer=req.referrer,
                    auto_start=req.auto_start,
                    format_id=req.format_id,
                )
            except Exception as e:
                print(f"[RemotePush] Error in async add: {e}")

        asyncio.create_task(_async_add())
        return {"status": "accepted"}

    # WebSocket for real-time telemetry
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        await hub.connect(websocket)
        try:
            # Send initial snapshot of all tasks
            snapshot = [t.to_dict() for t in engine.tasks.values()]
            await websocket.send_json({"type": "snapshot", "data": snapshot})

            while True:
                data = await websocket.receive_text()
                # Keep-alive ping/pong
                if data == "ping":
                    await websocket.send_text("pong")
        except WebSocketDisconnect:
            hub.disconnect(websocket)
        except Exception:
            hub.disconnect(websocket)

    # Mount Web Static Files
    if getattr(sys, "frozen", False):
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates = [
            os.path.join(base_dir, "openhydro", "web"),
            os.path.join(base_dir, "_internal", "openhydro", "web"),
            os.path.join(os.path.dirname(sys.executable), "_internal", "openhydro", "web"),
        ]
        web_dir = next((c for c in candidates if os.path.exists(c)), candidates[0])
    else:
        web_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "web"))

    if os.path.exists(web_dir):
        app.mount("/static", StaticFiles(directory=web_dir), name="static")

        @app.get("/")
        async def serve_index():
            return FileResponse(os.path.join(web_dir, "index.html"))

    return app
