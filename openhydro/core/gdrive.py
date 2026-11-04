import re
import asyncio
import urllib.parse
from typing import Dict, List, Optional, Tuple
import httpx

from .worker import shared_ssl_context_async

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

FOLDER_PATTERNS = [
    r'drive\.google\.com/drive/(?:u/\d+/)?folders/([a-zA-Z0-9_-]{20,})',
    r'drive\.google\.com/embeddedfolderview\?(?:[^&]+&)*id=([a-zA-Z0-9_-]{20,})',
    r'drive\.google\.com/folderview\?(?:[^&]+&)*id=([a-zA-Z0-9_-]{20,})',
]

FILE_PATTERNS = [
    r'drive\.google\.com/file/d/([a-zA-Z0-9_-]{20,})',
    r'drive\.google\.com/open\?(?:[^&]+&)*id=([a-zA-Z0-9_-]{20,})',
    r'drive\.google\.com/uc\?(?:[^&]+&)*id=([a-zA-Z0-9_-]{20,})',
    r'docs\.google\.com/file/d/([a-zA-Z0-9_-]{20,})',
    r'drive\.usercontent\.google\.com/download\?(?:[^&]+&)*id=([a-zA-Z0-9_-]{20,})',
]


class GoogleDriveHandler:
    @staticmethod
    def is_gdrive_url(url: str) -> bool:
        """Checks if URL is any Google Drive URL."""
        if not url or not isinstance(url, str):
            return False
        lower = url.lower()
        return "drive.google.com" in lower or "docs.google.com" in lower or "drive.usercontent.google.com" in lower

    @classmethod
    def is_gdrive_folder(cls, url: str) -> bool:
        """Determines if the URL points to a Google Drive folder."""
        if not cls.is_gdrive_url(url):
            return False
        return cls.extract_folder_id(url) is not None

    @classmethod
    def is_gdrive_file(cls, url: str) -> bool:
        """Determines if the URL points to an individual Google Drive file."""
        if not cls.is_gdrive_url(url):
            return False
        return cls.extract_file_id(url) is not None

    @classmethod
    def extract_folder_id(cls, url: str) -> Optional[str]:
        """Extracts the Google Drive folder ID from various URL structures."""
        for pattern in FOLDER_PATTERNS:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None

    @classmethod
    def extract_file_id(cls, url: str) -> Optional[str]:
        """Extracts the Google Drive file ID from various URL structures."""
        for pattern in FILE_PATTERNS:
            m = re.search(pattern, url)
            if m:
                return m.group(1)
        return None

    @classmethod
    def find_all_file_ids(cls, text: str) -> List[Tuple[str, str]]:
        """
        Extracts all Google Drive file URLs and their IDs from raw text (newlines, commas, spaces).
        Returns list of (file_id, canonical_url).
        """
        results = []
        seen = set()
        # Find all URLs in text
        raw_urls = re.findall(r'https?://[^\s,"\'<>]+', text)
        for u in raw_urls:
            fid = cls.extract_file_id(u)
            if fid and fid not in seen:
                seen.add(fid)
                results.append((fid, f"https://drive.google.com/file/d/{fid}/view"))
        return results

    @classmethod
    async def extract_folder(cls, folder_url_or_id: str) -> Dict:
        """
        Scrapes a shared Google Drive folder and extracts all video/file items
        formatted as a playlist compatible with OpenHydro playlist UI.
        """
        folder_id = cls.extract_folder_id(folder_url_or_id) or folder_url_or_id
        folder_id = folder_id.strip()

        embedded_url = f"https://drive.google.com/embeddedfolderview?id={folder_id}#list"
        headers = {
            "User-Agent": DEFAULT_UA,
            "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        }

        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=await shared_ssl_context_async()) as client:
            resp = await client.get(embedded_url, headers=headers)
            html = resp.text

        # Extract Folder Title
        title = "Google Drive Folder"
        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
        if title_match and title_match.group(1).strip():
            extracted_title = title_match.group(1).strip()
            if extracted_title != "Google Drive - Access Denied":
                title = extracted_title

        sanitized_title = re.sub(r'[\\/*?:"<>|]', "_", title).strip() or "Google Drive Folder"

        # Parse entries in flip-embedded HTML
        entries = []
        raw_items = re.findall(
            r'id="entry-([^"]+)".*?<div class="flip-entry-title">([^<]+)</div>',
            html,
            re.DOTALL
        )

        for idx, (f_id, f_title) in enumerate(raw_items, 1):
            f_title = f_title.strip() or f"File_{f_id}"

            # Search thumbnail for this specific id in html
            thumb_match = re.search(
                rf'id="entry-{re.escape(f_id)}".*?<img\s+src="([^"]+)"',
                html,
                re.DOTALL
            )
            thumb_url = thumb_match.group(1) if thumb_match else ""
            if thumb_url and thumb_url.startswith("//"):
                thumb_url = "https:" + thumb_url

            entries.append({
                "index": idx,
                "id": f_id,
                "title": f_title,
                "url": f"https://drive.google.com/file/d/{f_id}/view",
                "download_url": f"https://drive.google.com/uc?export=download&id={f_id}",
                "thumbnail": thumb_url,
                "duration": 0,
                "is_gdrive": True,
            })

        return {
            "is_playlist": True,
            "is_gdrive": True,
            "title": title,
            "title_sanitized": sanitized_title,
            "uploader": "Google Drive",
            "count": len(entries),
            "entries": entries,
        }

    @classmethod
    async def extract_multiple_files(cls, text: str) -> Dict:
        """
        Parses multiple Google Drive URLs pasted into input and returns playlist structure.
        """
        file_pairs = cls.find_all_file_ids(text)
        entries = []
        for idx, (f_id, canonical_url) in enumerate(file_pairs, 1):
            entries.append({
                "index": idx,
                "id": f_id,
                "title": f"Google Drive File {idx} ({f_id[:8]}...)",
                "url": canonical_url,
                "download_url": f"https://drive.google.com/uc?export=download&id={f_id}",
                "thumbnail": "",
                "duration": 0,
                "is_gdrive": True,
            })

        return {
            "is_playlist": True,
            "is_gdrive": True,
            "title": f"Google Drive Batch ({len(entries)} files)",
            "title_sanitized": f"Google_Drive_Batch_{len(entries)}_files",
            "uploader": "Google Drive Batch",
            "count": len(entries),
            "entries": entries,
        }

    @classmethod
    async def resolve_direct_download(cls, file_url_or_id: str) -> Dict:
        """
        Resolves a Google Drive file link to its actual direct stream/download URL,
        handling large-file virus scan confirmation tokens and returning exact filename and size.
        """
        file_id = cls.extract_file_id(file_url_or_id) or file_url_or_id
        file_id = file_id.strip()

        start_url = f"https://drive.google.com/uc?export=download&id={file_id}"
        headers = {
            "User-Agent": DEFAULT_UA,
            "Accept": "*/*",
        }

        filename = f"drive_{file_id}.bin"
        total_bytes = 0
        supports_ranges = True
        final_url = start_url

        timeout = httpx.Timeout(25.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=await shared_ssl_context_async()) as client:
            # Streamed: only the small HTML warning page is read; a direct file response is
            # closed unread instead of downloading the whole file into memory just to probe it
            async with client.stream("GET", start_url, headers=headers) as resp:
                final_url = str(resp.url)
                content_type = resp.headers.get("content-type", "").lower()
                page = (await resp.aread()).decode("utf-8", errors="replace") if "text/html" in content_type else ""

            # Check if this is an HTML confirmation page (large file virus scan warning)
            if page and ("confirm" in page.lower() or "download-form" in page):
                # Parse hidden inputs and action URL
                inputs = re.findall(r'<input\s+type="hidden"\s+name="([^"]+)"\s+value="([^"]*)"', page)
                params = {name: val for name, val in inputs}
                if "confirm" not in params:
                    params["confirm"] = "t"
                if "id" not in params:
                    params["id"] = file_id

                form_m = re.search(r'<form[^>]*action="([^"]+)"', page)
                action_url = form_m.group(1) if form_m else "https://drive.usercontent.google.com/download"

                query_str = urllib.parse.urlencode(params)
                confirmed_url = f"{action_url}?{query_str}" if query_str else action_url
                final_url = confirmed_url

                # Send a Range 0-0 probe to get the real headers and filename
                probe_headers = {**headers, "Range": "bytes=0-0"}
                async with client.stream("GET", confirmed_url, headers=probe_headers) as probe_resp:
                    pass
                cd = probe_resp.headers.get("content-disposition", "")
                if "filename=" in cd:
                    fn_match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^";\r\n]+)["\']?', cd, re.IGNORECASE)
                    if fn_match:
                        filename = urllib.parse.unquote(fn_match.group(1).strip().strip('"\''))

                cr = probe_resp.headers.get("content-range", "")
                if cr:
                    m_size = re.search(r"/(\d+)", cr)
                    if m_size:
                        total_bytes = int(m_size.group(1))
                        supports_ranges = True
            else:
                # Direct download response without warning
                cd = resp.headers.get("content-disposition", "")
                if "filename=" in cd:
                    fn_match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^";\r\n]+)["\']?', cd, re.IGNORECASE)
                    if fn_match:
                        filename = urllib.parse.unquote(fn_match.group(1).strip().strip('"\''))

                if "content-length" in resp.headers:
                    total_bytes = int(resp.headers["content-length"])

        # Clean filename
        filename = re.sub(r'[\\/*?:"<>|]', "_", filename).strip() or f"drive_{file_id}.bin"

        return {
            "resolved_url": final_url,
            "filename": filename,
            "total_bytes": total_bytes,
            "supports_ranges": supports_ranges,
            "is_gdrive": True,
            "file_id": file_id,
        }
