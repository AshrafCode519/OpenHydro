"""
OpenHydro Video Platform Extractor (YouTube, TikTok, Facebook, Twitter, etc.)
Extracts real video streams, qualities, titles, and handles downloads via yt-dlp, FFmpeg, and youtube-transcript-api.
Provides full Video, Audio (MP3/M4A), and Subtitles/Text Transcript (SRT/TXT) extraction just like IDM.
"""

import os
import re
import glob
import time
import asyncio
import threading
from typing import Dict, List, Optional, Callable
from urllib.parse import urlparse, parse_qs

from .hls import HLSDownloader

VIDEO_DOMAINS = [
    "youtube.com", "youtu.be",
    "tiktok.com",
    "facebook.com", "fb.watch", "fb.com",
    "twitter.com", "x.com",
    "instagram.com",
    "vimeo.com",
    "dailymotion.com",
    "bilibili.com",
    "reddit.com",
]

# Containers yt-dlp may produce, preferred first (without ffmpeg it cannot merge into .mp4)
VIDEO_EXTS = ("mp4", "mkv", "webm", "mov", "m4v", "flv")
AUDIO_EXTS = ("m4a", "opus", "webm", "ogg", "aac", "mp3")
SUBTITLE_EXTS = ("srt", "vtt", "ass")

# YouTube's challenges need a JavaScript runtime and yt-dlp only looks for Deno by default:
# enable every runtime it supports so an installed Node.js or Bun is used too
JS_RUNTIMES = {"deno": {}, "node": {}, "bun": {}}


class VideoExtractor:
    @staticmethod
    def normalize_url(url: str) -> str:
        """Normalizes video URLs (e.g. mobile Facebook to desktop, fb.watch redirects)."""
        if not url or not isinstance(url, str):
            return url or ""

        # Normalize mobile Facebook variants to www.facebook.com
        clean_url = re.sub(
            r'https?://(?:m|web|touch|mbasic)\.facebook\.com',
            'https://www.facebook.com',
            url.strip()
        )

        # If Facebook share or fb.watch redirect link, resolve canonical destination
        if "facebook.com/share" in clean_url or "fb.watch" in clean_url:
            try:
                from curl_cffi import requests
                r = requests.get(
                    clean_url,
                    impersonate="chrome120",
                    allow_redirects=True,
                    timeout=5,
                    headers={"Accept-Language": "en-US,en;q=0.9"}
                )
                if r.status_code == 200 and r.url and "share" not in r.url:
                    clean_url = str(r.url)
            except Exception:
                pass

        return clean_url

    @staticmethod
    def is_video_site(url: str) -> bool:
        """Determines if the URL belongs to a supported video streaming platform."""
        try:
            # Whole domain or a subdomain of it: a plain substring test let "x.com" match dropbox.com
            host = (urlparse(url).hostname or "").lower()
            return any(host == d or host.endswith("." + d) for d in VIDEO_DOMAINS)
        except Exception:
            return False

    @staticmethod
    def has_site_extractor(url: str) -> bool:
        """
        yt-dlp has an extractor made for this site (it knows over 1800), not only its generic one: a
        page there is a video page, not a file. Blocking (first call compiles the patterns): use a thread.
        """
        try:
            from yt_dlp.extractor import gen_extractor_classes
            return any(ie.ie_key() != "Generic" and ie.suitable(url) for ie in gen_extractor_classes())
        except Exception:
            return False

    @staticmethod
    def is_playlist_url(url: str) -> bool:
        """Determines if the URL points to a full playlist, album, Google Drive folder, or batch list."""
        try:
            from .gdrive import GoogleDriveHandler
            if GoogleDriveHandler.is_gdrive_folder(url) or len(GoogleDriveHandler.find_all_file_ids(url)) > 1:
                return True
            lower = url.lower()
            # Exact "list" query parameter, so e.g. "?checklist=1" on a file link is not a playlist
            params = parse_qs(urlparse(lower).query)
            return ("list" in params and "index" not in params) or "youtube.com/playlist" in lower or "/sets/" in lower
        except Exception:
            return False

    @staticmethod
    def extract_youtube_video_id(url: str) -> Optional[str]:
        """Extracts the 11-character YouTube video ID from various URL formats."""
        match = re.search(r'(?:v=|\/|embed\/|youtu\.be\/|\/v\/|\/e\/|watch\?v=)([0-9A-Za-z_-]{11})', url)
        return match.group(1) if match else None

    @classmethod
    async def extract_info(cls, url: str) -> Dict:
        """
        Extracts video metadata, title, duration, video qualities, audio formats,
        and available subtitle/transcript options asynchronously.
        """
        from .gdrive import GoogleDriveHandler
        if GoogleDriveHandler.is_gdrive_file(url):
            ginfo = await GoogleDriveHandler.resolve_direct_download(url)
            return {
                "is_platform_video": False,
                "is_gdrive": True,
                "title": ginfo["filename"],
                "filename": ginfo["filename"],
                "total_bytes": ginfo["total_bytes"],
                "supports_ranges": ginfo["supports_ranges"],
                "resolved_url": ginfo["resolved_url"],
            }
        url = cls.normalize_url(url)
        return await asyncio.to_thread(cls._sync_extract, url)

    @classmethod
    async def extract_playlist(cls, url: str) -> Dict:
        """
        Extracts playlist metadata, title, video count, and video list asynchronously.
        Supports YouTube playlists, Google Drive folders, and batch Drive file lists.
        """
        from .gdrive import GoogleDriveHandler
        if GoogleDriveHandler.is_gdrive_folder(url):
            return await GoogleDriveHandler.extract_folder(url)
        if len(GoogleDriveHandler.find_all_file_ids(url)) > 1:
            return await GoogleDriveHandler.extract_multiple_files(url)
        url = cls.normalize_url(url)
        return await asyncio.to_thread(cls._sync_extract_playlist, url)

    @classmethod
    def _sync_extract_playlist(cls, url: str) -> Dict:
        import yt_dlp

        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "js_runtimes": JS_RUNTIMES,
            "no_warnings": True,
            "skip_download": True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        title = info.get("title") or "Playlist"
        title_sanitized = re.sub(r'[\\/*?:"<>|]', "_", title).strip() or "Playlist"
        raw_entries = info.get("entries") or []

        entries = []
        for idx, entry in enumerate(raw_entries, 1):
            if not entry:
                continue
            v_id = entry.get("id") or ""
            v_title = entry.get("title") or f"Video {idx}"
            v_url = entry.get("url")
            if not v_url or not v_url.startswith("http"):
                v_url = f"https://www.youtube.com/watch?v={v_id}" if v_id else ""
            v_thumb = entry.get("thumbnail")
            if not v_thumb and v_id:
                v_thumb = f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg"

            entries.append({
                "index": idx,
                "id": v_id,
                "title": v_title,
                "url": v_url,
                "duration": entry.get("duration", 0),
                "thumbnail": v_thumb or "",
            })

        return {
            "is_playlist": True,
            "title": title,
            "title_sanitized": title_sanitized,
            "uploader": info.get("uploader") or info.get("channel") or "",
            "count": len(entries),
            "entries": entries,
        }

    # yt-dlp metadata extraction takes many seconds on YouTube. A download used to extract three
    # times (quality menu, add, download); the result is now reused while its links stay valid.
    _info_cache: Dict[str, tuple] = {}
    INFO_CACHE_SECONDS = 15 * 60

    @classmethod
    def _remember_info(cls, url: str, info: Dict):
        now = time.time()
        cls._info_cache = {u: e for u, e in cls._info_cache.items() if now - e[0] < cls.INFO_CACHE_SECONDS}
        cls._info_cache[url] = (now, info)

    @classmethod
    def _recent_info(cls, url: str) -> Optional[Dict]:
        entry = cls._info_cache.get(url)
        if entry and time.time() - entry[0] < cls.INFO_CACHE_SECONDS:
            return entry[1]
        return None

    @classmethod
    def _sync_extract(cls, url: str) -> Dict:
        import yt_dlp

        info = cls._recent_info(url)
        if info is None:
            ydl_opts = {
                "quiet": True,
                "js_runtimes": JS_RUNTIMES,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                    "Sec-Fetch-Mode": "navigate",
                },
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.sanitize_info(ydl.extract_info(url, download=False))
            cls._remember_info(url, info)

        title = info.get("title", "video")
        title_sanitized = re.sub(r'[\\/*?:"<>|]', "_", title)

        # Filter clean, useful video and audio formats
        raw_formats = info.get("formats", [])
        qualities = []

        # Check for Facebook-specific formats (HD / SD)
        has_fb_hd = any(f.get("format_id") == "hd" for f in raw_formats)
        has_fb_sd = any(f.get("format_id") == "sd" for f in raw_formats)

        if has_fb_hd:
            qualities.append({
                "label": "🎬 HD عالي الجودة (Facebook HD)",
                "height": 1080,
                "format_id": "hd/bestvideo+bestaudio/best",
                "type": "video",
                "ext": "mp4",
            })
        if has_fb_sd:
            qualities.append({
                "label": "🎬 SD جودة قياسية (Facebook SD)",
                "height": 480,
                "format_id": "sd/bestvideo+bestaudio/best",
                "type": "video",
                "ext": "mp4",
            })

        # 1. Preset target video resolutions (with safe /best fallback)
        targets = [
            ("2160p (4K UHD)", 2160, "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best"),
            ("1440p (2K QHD)", 1440, "bestvideo[height<=1440]+bestaudio/best[height<=1440]/best"),
            ("1080p (FHD)", 1080, "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"),
            ("720p (HD)", 720, "bestvideo[height<=720]+bestaudio/best[height<=720]/best"),
            ("480p (SD)", 480, "bestvideo[height<=480]+bestaudio/best[height<=480]/best"),
            ("360p (عادي)", 360, "bestvideo[height<=360]+bestaudio/best[height<=360]/best"),
        ]

        max_height = 0
        for f in raw_formats:
            h = f.get("height") or 0
            if h > max_height:
                max_height = h

        for label, h, fmt_selector in targets:
            if max_height >= h or (max_height == 0 and not (has_fb_hd or has_fb_sd) and h == 360):
                qualities.append({
                    "label": f"🎬 {label}",
                    "height": h,
                    "format_id": fmt_selector,
                    "type": "video",
                    "ext": "mp4",
                })

        if not qualities:
            qualities.append({
                "label": "🎬 أعلى دقة متاحة (Best Quality)",
                "height": 1080,
                "format_id": "bestvideo+bestaudio/best",
                "type": "video",
                "ext": "mp4",
            })

        # 2. Add Audio-Only options (MP3 & M4A)
        qualities.append({
            "label": "🎵 استخراج الصوت عالي الدقة (MP3 - 320 kbps)",
            "height": 0,
            "format_id": "audio_mp3",
            "type": "audio",
            "is_audio": True,
            "ext": "mp3",
        })
        qualities.append({
            "label": "🎵 استخراج الصوت الأصلي (M4A Audio)",
            "height": 0,
            "format_id": "audio_m4a",
            "type": "audio",
            "is_audio": True,
            "ext": "m4a",
        })

        # 3. Subtitles / Captions / Transcripts. YouTube always gets the options (its transcript API
        # is tried at download time); the separate availability request cost seconds for nothing.
        yt_id = cls.extract_youtube_video_id(url)
        has_subtitles = bool(info.get("subtitles") or info.get("automatic_captions"))

        if has_subtitles or yt_id:
            qualities.append({
                "label": "📝 تحميل ملف الترجمة والنصوص (SRT Subtitles)",
                "height": 0,
                "format_id": "subtitle_srt",
                "type": "subtitle",
                "is_subtitle": True,
                "ext": "srt",
            })
            qualities.append({
                "label": "📄 تفريغ النص بالكامل (Text Transcript .txt)",
                "height": 0,
                "format_id": "subtitle_txt",
                "type": "text",
                "is_text": True,
                "ext": "txt",
            })

        approx_size = info.get("filesize") or info.get("filesize_approx") or 0

        return {
            "is_platform_video": True,
            "title": title,
            "filename": f"{title_sanitized}.mp4",
            "thumbnail": info.get("thumbnail"),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader"),
            "total_bytes": approx_size,
            "qualities": qualities,
            "has_subtitles": has_subtitles or bool(yt_id),
            # Something to download was found (qualities always carry a "best" fallback)
            "has_media": bool(raw_formats or info.get("url")),
        }

    @classmethod
    async def download(
        cls,
        url: str,
        output_path: str,
        format_selector: str = "bestvideo+bestaudio/best",
        num_connections: int = 16,
        on_progress: Optional[Callable] = None,
        is_cancelled: Optional[Callable] = None,
        http_headers: Optional[Dict[str, str]] = None,
        rate_limiter=None,
    ) -> Optional[str]:
        """
        Executes download for Video, Audio, or Subtitle/Text transcript with multi-threaded concurrent fragment downloads.
        Returns the path of the file actually written (its extension may differ from output_path), or None on failure.
        """
        url = cls.normalize_url(url)
        return await asyncio.to_thread(
            cls._sync_download, url, output_path, format_selector, num_connections, on_progress, is_cancelled, http_headers,
            rate_limiter,
        )

    @staticmethod
    def _outtmpl(out_base: str) -> str:
        # "%" is yt-dlp template syntax: a title such as "100% Real" must be escaped
        return out_base.replace("%", "%%") + ".%(ext)s"

    @staticmethod
    def _compatible_format_sort(format_selector: str) -> List[str]:
        """
        yt-dlp ranking: H.264 video and AAC audio win among formats of the same resolution, since an
        mp4 with AV1/VP9 + Opus does not play in Windows' default player or on many phones and TVs.
        Resolution stays first, so a 4K pick (no H.264 at that size) is never downgraded to 1080p.
        """
        cap = re.search(r"height<=(\d+)", format_selector)
        return [f"res:{cap.group(1)}" if cap else "res", "vcodec:h264", "acodec:aac"]

    @staticmethod
    def needs_ffmpeg(format_selector: Optional[str]) -> bool:
        """Merging a video and an audio stream, or making an MP3 (the default picks a merge too)."""
        return not format_selector or format_selector == "audio_mp3" or "+" in format_selector

    @staticmethod
    def without_merging(format_selector: str) -> str:
        """
        The same request using only formats a site serves as one file: yt-dlp aborts a merge (or an
        MP3) when ffmpeg is missing, so a lower quality is the alternative to a failed download.
        """
        if format_selector == "audio_mp3":
            return "audio_m4a"  # the original audio stream, no conversion
        if format_selector in ("audio_m4a", "subtitle_srt", "subtitle_txt"):
            return format_selector
        single_file = [choice for choice in format_selector.split("/") if "+" not in choice]
        return "/".join(single_file) or "best"

    @staticmethod
    def _locate_output(out_base: str, extensions) -> Optional[str]:
        """
        Returns "<out_base>.<ext>" for the first extension that exists. Exact names only: never a
        format fragment (.f137.mp4), a temp file (.part/.ytdl) or another download sharing the prefix.
        """
        for ext in extensions:
            candidate = f"{out_base}.{ext}"
            if os.path.exists(candidate):
                return candidate
        return None

    @classmethod
    def _sync_download(
        cls,
        url: str,
        output_path: str,
        format_selector: str,
        num_connections: int,
        on_progress: Optional[Callable],
        is_cancelled: Optional[Callable],
        http_headers: Optional[Dict[str, str]] = None,
        rate_limiter=None,
    ) -> Optional[str]:
        import yt_dlp

        ffmpeg_bin = HLSDownloader.find_ffmpeg()

        # Fallback safety: ensure video format_selector always has fallback
        special_formats = ("audio_mp3", "audio_m4a", "subtitle_srt", "subtitle_txt")
        if not format_selector:
            format_selector = "bestvideo+bestaudio/best"
        elif format_selector not in special_formats and not (format_selector.endswith("/best") or format_selector.endswith("/b")):
            format_selector = f"{format_selector}/bestvideo+bestaudio/best"
        if not ffmpeg_bin:
            format_selector = cls.without_merging(format_selector)

        # The speed limits (global and this download's own). yt-dlp reports each block it writes, from
        # every fragment thread: waiting here holds its download to the limit
        reported: Dict[str, int] = {}
        reported_lock = threading.Lock()

        def throttle(d):
            with reported_lock:
                key = d.get("filename") or ""  # per file: the video, then the audio
                done = d.get("downloaded_bytes") or 0
                new_bytes = done - reported.get(key, 0)
                reported[key] = max(done, reported.get(key, 0))
            if new_bytes > 0:
                rate_limiter.acquire_blocking(new_bytes)

        def progress_hook(d):
            if is_cancelled and is_cancelled():
                raise asyncio.CancelledError("Download cancelled by user")
            if rate_limiter and d["status"] == "downloading":
                throttle(d)

            if d["status"] == "downloading" and on_progress:
                downloaded = d.get("downloaded_bytes", 0)
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                speed = d.get("speed") or 0.0
                eta = d.get("eta") or 0
                on_progress(downloaded, total, speed, eta)

        # Base path without extension
        out_base, _ = os.path.splitext(output_path)

        # --- Case 1: Subtitle or Text Transcript Download ---
        if format_selector in ("subtitle_srt", "subtitle_txt"):
            yt_id = cls.extract_youtube_video_id(url)
            if yt_id:
                try:
                    from youtube_transcript_api import YouTubeTranscriptApi
                    from youtube_transcript_api.formatters import SRTFormatter, TextFormatter

                    try:
                        transcript = YouTubeTranscriptApi().fetch(yt_id, ['ar', 'ar-orig', 'en'])
                    except Exception:
                        tlist = YouTubeTranscriptApi().list(yt_id)
                        first_t = next(iter(tlist), None)
                        if first_t:
                            transcript = first_t.fetch()
                        else:
                            raise RuntimeError("No transcript found for video")

                    if format_selector == "subtitle_srt":
                        content = SRTFormatter().format_transcript(transcript)
                        output_path = out_base + ".srt"
                    else:
                        content = TextFormatter().format_transcript(transcript)
                        output_path = out_base + ".txt"

                    parent = os.path.dirname(output_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(output_path, "w", encoding="utf-8") as f:
                        f.write(content)

                    if on_progress:
                        sz = len(content.encode("utf-8"))
                        on_progress(sz, sz, 1024 * 1024, 0)
                    return output_path
                except Exception as e:
                    print(f"[VideoExtractor] YouTube transcript error: {e}")
                    return None

            # Non-YouTube subtitles via yt-dlp
            wanted = "srt" if format_selector == "subtitle_srt" else "vtt"
            ydl_opts = {
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitlesformat": wanted,
                "outtmpl": cls._outtmpl(out_base),
                "quiet": True,
                "js_runtimes": JS_RUNTIMES,
                "no_warnings": True,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            except Exception as e:
                print(f"[VideoExtractor] Subtitle download failed: {e}")
                return None
            # yt-dlp names subtitle files "<base>.<language>.<ext>" and may fall back to another format
            for ext in (wanted,) + tuple(x for x in SUBTITLE_EXTS if x != wanted):
                matches = sorted(glob.glob(glob.escape(out_base) + f".*.{ext}"))
                if matches:
                    return matches[0]
            return None

        common_opts = {
            "outtmpl": cls._outtmpl(out_base),
            "concurrent_fragment_downloads": max(8, num_connections or 16),
            "buffersize": 1024 * 1024,
            "http_chunk_size": 10 * 1024 * 1024,
            "retries": 10,
            "fragment_retries": 10,
            "quiet": True,
            "js_runtimes": JS_RUNTIMES,
            "no_warnings": True,
            # Progress reaches the UI through the hook; the console bar only bloated openhydro.log
            "noprogress": True,
            "progress_hooks": [progress_hook],
        }
        if ffmpeg_bin:
            common_opts["ffmpeg_location"] = os.path.dirname(ffmpeg_bin)
        if http_headers:
            common_opts["http_headers"] = dict(http_headers)

        # --- Case 2: Audio Only (MP3 320kbps) ---
        if format_selector == "audio_mp3":
            ydl_opts = {
                **common_opts,
                "format": "bestaudio/best",
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }],
            }
            expected_exts = ("mp3",)

        # --- Case 3: Audio Only (Original M4A) ---
        elif format_selector == "audio_m4a":
            ydl_opts = {**common_opts, "format": "bestaudio[ext=m4a]/bestaudio/best"}
            # Keep the real container if no m4a stream exists (renaming .opus to .m4a mislabels it)
            expected_exts = AUDIO_EXTS

        # --- Case 4: Video Stream with Selected Resolution ---
        else:
            ydl_opts = {
                **common_opts,
                "format": format_selector,
                "format_sort": cls._compatible_format_sort(format_selector),
                "merge_output_format": "mp4",
                "http_headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
                    **(http_headers or {}),
                },
            }
            expected_exts = VIDEO_EXTS

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = cls._recent_info(url)
                if info is None:
                    ydl.download([url])
                else:
                    try:
                        # Same path as yt-dlp's --load-info-json: formats are re-selected for this
                        # request, but the slow page/player extraction is not repeated
                        ydl.process_ie_result(dict(info), download=True)
                    except yt_dlp.utils.DownloadError:
                        cls._info_cache.pop(url, None)  # links expired: extract afresh
                        ydl.download([url])
            return cls._locate_output(out_base, expected_exts)
        except Exception as e:
            print(f"[VideoExtractor] Download failed ({format_selector}): {e}")
            return None
