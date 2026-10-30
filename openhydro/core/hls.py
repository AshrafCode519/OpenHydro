"""
OpenHydro HLS (.m3u8) Stream Parser & Downloader
Handles live/VOD HLS playlist parsing, multi-segment concurrent downloading,
and stream assembly (direct TS concat + FFmpeg remuxing).
"""

import os
import re
import shutil
import asyncio
import subprocess
from urllib.parse import urljoin
from typing import List, Dict, Optional, Tuple
import httpx

from .limiter import RateLimiter
from .worker import DEFAULT_USER_AGENT


class HLSStreamParser:
    @staticmethod
    def is_m3u8_url(url: str) -> bool:
        """Checks if URL points to an HLS playlist."""
        clean = url.split("?")[0].lower()
        return clean.endswith(".m3u8") or ".m3u8" in clean

    @classmethod
    async def fetch_and_parse(cls, url: str, headers: Optional[Dict[str, str]] = None) -> Dict:
        """
        Fetches the m3u8 playlist and parses it.
        Returns whether it's a master playlist or media playlist, with available qualities or segments.
        """
        req_headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            **(headers or {}),
        }

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=req_headers)
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code} fetching playlist")
            text = resp.text

        base_url = str(resp.url)
        return cls.parse_content(text, base_url)

    @classmethod
    def parse_content(cls, content: str, base_url: str) -> Dict:
        lines = [line.strip() for line in content.splitlines() if line.strip()]

        if not any(line.startswith("#EXTM3U") for line in lines[:3]):
            raise ValueError("Not a valid M3U8 playlist")

        is_master = any("#EXT-X-STREAM-INF" in line for line in lines)

        if is_master:
            # Master playlist: extract available video qualities
            qualities = []
            current_info = {}
            for i, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF:"):
                    # Extract bandwidth and resolution
                    bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                    res_match = re.search(r'RESOLUTION=(\d+x\d+)', line)
                    name_match = re.search(r'NAME="([^"]+)"', line)

                    current_info = {
                        "bandwidth": int(bw_match.group(1)) if bw_match else 0,
                        "resolution": res_match.group(1) if res_match else "Auto",
                        "name": name_match.group(1) if name_match else None,
                    }
                elif current_info and not line.startswith("#"):
                    stream_url = urljoin(base_url, line)
                    current_info["url"] = stream_url
                    qualities.append(current_info)
                    current_info = {}

            # Sort qualities by bandwidth desc
            qualities.sort(key=lambda x: x["bandwidth"], reverse=True)
            return {
                "type": "master",
                "qualities": qualities,
            }

        else:
            # Media playlist: extract TS segments
            segments = []
            current_duration = 0.0
            for line in lines:
                if line.startswith("#EXTINF:"):
                    dur_match = re.search(r'#EXTINF:([\d.]+)', line)
                    current_duration = float(dur_match.group(1)) if dur_match else 0.0
                elif not line.startswith("#"):
                    seg_url = urljoin(base_url, line)
                    segments.append({
                        "url": seg_url,
                        "duration": current_duration,
                    })
                    current_duration = 0.0

            total_duration = sum(s["duration"] for s in segments)
            return {
                "type": "media",
                "segments": segments,
                "total_segments": len(segments),
                "total_duration_seconds": total_duration,
            }


class HLSDownloader:
    @staticmethod
    def find_ffmpeg() -> Optional[str]:
        """Locates ffmpeg executable in PATH or standard winget locations."""
        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            return ffmpeg_bin

        # Common Windows locations
        potential_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg.Essentials_*\ffmpeg-*\bin\ffmpeg.exe"),
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
        ]
        import glob
        for p in potential_paths:
            matches = glob.glob(p)
            if matches and os.path.exists(matches[0]):
                return matches[0]
        return None

    @classmethod
    async def download_segments(
        cls,
        segments: List[Dict],
        temp_dir: str,
        num_connections: int = 8,
        rate_limiter: Optional[RateLimiter] = None,
        on_progress: Optional[callable] = None,
        is_cancelled: Optional[callable] = None,
    ) -> List[str]:
        """
        Downloads all segments concurrently into temp_dir.
        Returns list of downloaded file paths in correct order.
        """
        os.makedirs(temp_dir, exist_ok=True)
        num_connections = max(1, min(num_connections, 16))
        semaphore = asyncio.Semaphore(num_connections)

        total_segments = len(segments)
        completed_count = 0
        total_downloaded_bytes = 0
        downloaded_files = [None] * total_segments

        async def worker(idx: int, seg_info: Dict):
            nonlocal completed_count, total_downloaded_bytes
            if is_cancelled and is_cancelled():
                return

            seg_path = os.path.join(temp_dir, f"segment_{idx:05d}.ts")

            # Resume support: skip if already downloaded
            if os.path.exists(seg_path) and os.path.getsize(seg_path) > 0:
                downloaded_files[idx] = seg_path
                completed_count += 1
                total_downloaded_bytes += os.path.getsize(seg_path)
                if on_progress:
                    on_progress(completed_count, total_segments, total_downloaded_bytes)
                return

            async with semaphore:
                retries = 3
                for attempt in range(retries):
                    try:
                        timeout = httpx.Timeout(20.0, connect=10.0)
                        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                            async with client.stream("GET", seg_info["url"]) as resp:
                                if resp.status_code == 200:
                                    with open(seg_path, "wb") as f:
                                        async for chunk in resp.aiter_bytes(chunk_size=32 * 1024):
                                            if is_cancelled and is_cancelled():
                                                return
                                            if rate_limiter:
                                                await rate_limiter.acquire(len(chunk))
                                            f.write(chunk)
                                            total_downloaded_bytes += len(chunk)

                                    downloaded_files[idx] = seg_path
                                    completed_count += 1
                                    if on_progress:
                                        on_progress(completed_count, total_segments, total_downloaded_bytes)
                                    return
                    except Exception as e:
                        if attempt == retries - 1:
                            print(f"[HLS] Error segment {idx}: {e}")
                        await asyncio.sleep(1.0)

        tasks = [worker(i, seg) for i, seg in enumerate(segments)]
        await asyncio.gather(*tasks)

        return [f for f in downloaded_files if f and os.path.exists(f)]

    @classmethod
    def assemble_stream(cls, segment_files: List[str], final_path: str) -> bool:
        """
        Concatenates all TS segments into the final video file.
        Uses FFmpeg for fast remuxing into .mp4 if available, or direct binary concat.
        """
        if not segment_files:
            return False

        ffmpeg_exe = cls.find_ffmpeg()

        # If ffmpeg is available and target extension is .mp4, remux cleanly without re-encoding
        if ffmpeg_exe and final_path.lower().endswith(".mp4"):
            concat_list_path = os.path.join(os.path.dirname(final_path), "concat_list.txt")
            try:
                with open(concat_list_path, "w", encoding="utf-8") as f:
                    for seg in segment_files:
                        safe_path = os.path.abspath(seg).replace("\\", "/")
                        f.write(f"file '{safe_path}'\n")

                cmd = [
                    ffmpeg_exe,
                    "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", concat_list_path,
                    "-c", "copy",
                    "-bsf:a", "aac_adtstoasc",
                    final_path
                ]
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if res.returncode == 0:
                    if os.path.exists(concat_list_path):
                        os.remove(concat_list_path)
                    return True
            except Exception as e:
                print(f"[HLS] FFmpeg remux failed: {e}. Falling back to binary merge...")

        # Native Direct Binary Concatenation (Always works without external dependencies)
        try:
            with open(final_path, "wb") as outfile:
                for seg in segment_files:
                    with open(seg, "rb") as infile:
                        shutil.copyfileobj(infile, outfile, length=64 * 1024)
            return True
        except Exception as e:
            print(f"[HLS] Direct concat error: {e}")
            return False

