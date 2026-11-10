import os
import sys
import json
import uuid
import asyncio
import logging
from pathlib import Path
from typing import Tuple, Dict, Any, Optional
import yt_dlp

from config import BASE_DIR, DOWNLOAD_DIR, MAX_FILE_SIZE_BYTES

logger = logging.getLogger(__name__)

# Initialize static-ffmpeg if available so ffmpeg is in PATH
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
    logger.info("static-ffmpeg initialized successfully for TeraBox.")
except Exception as e:
    logger.warning(f"Could not initialize static-ffmpeg: {e}")

# TeraBox domain aliases and mirrors
TERABOX_DOMAINS = [
    "terabox.com", "teraboxapp.com", "terabox.app", "1024tera.com",
    "4funbox.com", "4funbox.co", "mirrobox.com", "nephobox.com",
    "freeterabox.com", "1024terabox.com", "terasharefile.com",
    "teraboxshare.com", "momerybox.com", "terabox.fun",
    "terafileshare.com", "teraboxlink.com", "terasharelink.com",
    "dubox.com", "tibibox.com", "gibibox.com"
]

# Path to Puppeteer scraper
SCRAPER_SCRIPT = BASE_DIR / "terabox_scraper.js"

# Potential paths for puppeteer node_modules
NODE_MODULES_PATHS = [
    str(BASE_DIR / "node_modules"),
    r"C:\Users\rohit\OneDrive\Desktop\teradown-scraper\node_modules",
    os.path.expanduser("~/.npm-global/lib/node_modules"),
]


def is_terabox_url(url: str) -> bool:
    """Returns True if the URL is a supported TeraBox or TeraShare mirror link."""
    url_lower = url.lower()
    return any(domain in url_lower for domain in TERABOX_DOMAINS)


def get_terabox_api_key() -> str:
    """Returns active TeraBox API key from persistent config."""
    try:
        from web_app import load_admin_config
        return load_admin_config().get("terabox_api_key", "")
    except Exception:
        return os.getenv("TERABOX_API_KEY", "")


def set_terabox_api_key(api_key: str):
    """Sets active TeraBox API key persistently."""
    try:
        from web_app import load_admin_config, save_admin_config
        cfg = load_admin_config()
        cfg["terabox_api_key"] = api_key.strip()
        save_admin_config(cfg)
    except Exception:
        os.environ["TERABOX_API_KEY"] = api_key.strip()


async def extract_terabox_info(url: str) -> Tuple[bool, Dict[str, Any], Optional[str]]:
    """
    Extracts stream and direct download URLs from TeraBox using the Puppeteer scraper.
    Returns (success, info_dict, error_message).
    """
    if not SCRAPER_SCRIPT.exists():
        return False, {}, f"Scraper script not found at {SCRAPER_SCRIPT}"

    # Build environment with NODE_PATH pointing to node_modules
    env = os.environ.copy()
    existing_node_path = env.get("NODE_PATH", "")
    valid_paths = [p for p in NODE_MODULES_PATHS if os.path.exists(p)]
    env["NODE_PATH"] = os.pathsep.join(valid_paths + ([existing_node_path] if existing_node_path else []))

    try:
        # Run node terabox_scraper.js "<url>" asynchronously
        process = await asyncio.create_subprocess_exec(
            "node",
            str(SCRAPER_SCRIPT),
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )

        stdout, stderr = await process.communicate()
        raw_output = stdout.decode("utf-8", errors="ignore").strip()

        # Parse JSON output from the scraper first to get clean user-friendly messages
        json_start = raw_output.find("{")
        json_end = raw_output.rfind("}")
        if json_start != -1 and json_end != -1:
            try:
                data = json.loads(raw_output[json_start:json_end + 1])
                if not data.get("success"):
                    clean_err = data.get("error", "Unable to extract TeraBox video (Link may be expired or deleted).")
                    logger.warning(f"TeraBox Scraper reported: {clean_err}")
                    return False, {}, f"TeraBox Error: {clean_err}"
            except Exception:
                pass

        if process.returncode != 0 or not raw_output:
            err_msg = stderr.decode("utf-8", errors="ignore").strip() or raw_output or "Scraper failed"
            logger.error(f"TeraBox Scraper error: {err_msg}")
            return False, {}, f"Scraper error: {err_msg}"
        if json_start == -1 or json_end == -1:
            return False, {}, f"Invalid output from scraper: {raw_output[:200]}"

        data = json.loads(raw_output[json_start:json_end + 1])

        if not data.get("success"):
            return False, {}, data.get("error", "Unable to extract TeraBox link.")

        title = data.get("title") or "TeraBox_Video"
        media_url = data.get("media_url") or data.get("download_url") or data.get("stream_url")

        if not media_url:
            return False, {}, "No stream or download URL found for this TeraBox link."

        info = {
            "id": uuid.uuid4().hex[:8],
            "title": title,
            "uploader": "TeraBox",
            "play_url": media_url,
            "stream_url": data.get("stream_url"),
            "download_url": data.get("download_url"),
            "original_url": url,
            "thumbnail": data.get("thumbnail"),
            "duration": data.get("duration"),
            "size": data.get("size"),
            "size_bytes": data.get("size_bytes"),
            "quality": data.get("quality"),
            "formats": [],
            "engine": "puppeteer",
        }
        return True, info, None

    except Exception as e:
        logger.exception(f"Exception while running TeraBox scraper: {e}")
        return False, {}, str(e)


async def download_terabox_media(
    url: str,
    quality: str = "best",
    format_selector: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Downloads media from TeraBox by first scraping the direct stream URL,
    then downloading and merging it with yt-dlp.
    Returns (success, file_path, info_dict, error_message).
    """
    success, info, err = await extract_terabox_info(url)
    if not success:
        return False, None, None, err

    media_url = info["play_url"]
    file_id = info["id"]
    filename = info["title"]

    # Sanitize title for file system
    safe_title = "".join([c for c in filename if c.isalnum() or c in " ._-"]).rstrip()
    if not safe_title or safe_title == ".mp4":
        safe_title = "terabox_video"
    if not safe_title.endswith(".mp4"):
        safe_title += ".mp4"

    output_path = str(DOWNLOAD_DIR / f"{file_id}_{safe_title}")

    # Use yt-dlp to download the direct MP4 or HLS (.m3u8) stream
    def _download():
        import shutil
        opts = {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'outtmpl': output_path,
            'merge_output_format': 'mp4',
            'concurrent_fragment_downloads': 16,
            'buffersize': 1024 * 1024 * 16,
            'http_chunk_size': 20 * 1024 * 1024,
            'retries': 15,
            'fragment_retries': 15,
            'socket_timeout': 45,
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/126.0.0.0 Safari/537.36'
                ),
            },
        }

        ffmpeg_bin = shutil.which("ffmpeg")
        if ffmpeg_bin:
            opts['ffmpeg_location'] = ffmpeg_bin

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                ydl.download([media_url])
                if os.path.exists(output_path):
                    return True, output_path, info, None
                
                # Check for possible extension variations
                for f in DOWNLOAD_DIR.glob(f"{file_id}_*"):
                    if f.exists() and f.stat().st_size > 0:
                        return True, str(f), info, None

                return False, None, info, "Downloaded file not found on disk."
            except Exception as e:
                return False, None, info, str(e)

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _download)
