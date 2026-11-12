import os
import re
import json
import uuid
import shutil
import asyncio
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import yt_dlp

# Base directory for the web app
BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
WEBSITE_DIR = BASE_DIR / "website"
WEBSITE_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = DATA_DIR / "terabox_config.json"

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Rohit-admin5064@")
ADMIN_TOKEN = "DownTG_Admin_Token_Rohit5064"


def load_admin_config() -> Dict[str, Any]:
    cfg = {
        "terabox_api_key": os.getenv("TERABOX_API_KEY", ""),
        "terabox_endpoint": os.getenv("TERABOX_ENDPOINT", "https://gregarious-haupia-5bced4.netlify.app/api/extract"),
    }
    if CONFIG_FILE.exists():
        try:
            stored = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                cfg.update(stored)
        except Exception:
            pass
    return cfg


def save_admin_config(cfg: Dict[str, Any]):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    if "terabox_api_key" in cfg and cfg["terabox_api_key"]:
        os.environ["TERABOX_API_KEY"] = cfg["terabox_api_key"]
    if "terabox_endpoint" in cfg and cfg["terabox_endpoint"]:
        os.environ["TERABOX_ENDPOINT"] = cfg["terabox_endpoint"]

# Path to TeraBox Scraper
TERABOX_SCRAPER = BASE_DIR / "terabox_scraper.js"
if not TERABOX_SCRAPER.exists() and (BASE_DIR.parent / "DownTG-Bot" / "terabox_scraper.js").exists():
    TERABOX_SCRAPER = BASE_DIR.parent / "DownTG-Bot" / "terabox_scraper.js"

# Initialize static-ffmpeg if available so ffmpeg is in PATH
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("DownTG-Web")

app = FastAPI(
    title="DownTG Universal Web Downloader",
    description="High-Speed Universal Video & Audio Downloader for YouTube, Instagram, Facebook, TeraBox, Twitter/X, Reddit, TikTok, and 1000+ sites.",
    version="2.6.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ExtractRequest(BaseModel):
    url: str


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class AdminConfigRequest(BaseModel):
    terabox_api_key: Optional[str] = None
    terabox_endpoint: Optional[str] = None
    cookies_text: Optional[str] = None


TERABOX_DOMAINS = [
    "terabox.com", "teraboxapp.com", "terabox.app", "1024tera.com",
    "4funbox.com", "4funbox.co", "mirrobox.com", "nephobox.com",
    "freeterabox.com", "1024terabox.com", "terasharefile.com",
    "teraboxshare.com", "momerybox.com", "terabox.fun",
    "terafileshare.com", "teraboxlink.com", "terasharelink.com",
    "dubox.com", "tibibox.com", "gibibox.com"
]


def is_terabox_url(url: str) -> bool:
    """Check if URL belongs to TeraBox / TeraShare."""
    return any(d in url.lower() for d in TERABOX_DOMAINS)


def get_platform_badge(url: str) -> str:
    """Returns platform emoji and label."""
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        if "/shorts/" in u:
            return "🔴 YouTube Shorts"
        return "▶️ YouTube"
    elif "instagram.com" in u:
        if "/reel/" in u or "/reels/" in u:
            return "📸 Instagram Reel"
        return "📸 Instagram"
    elif "facebook.com" in u or "fb.watch" in u or "fb.com" in u:
        if "/reel/" in u or "/reels/" in u:
            return "🔵 Facebook Reel"
        return "🔵 Facebook"
    elif "tiktok.com" in u:
        return "🎵 TikTok"
    elif "twitter.com" in u or "x.com" in u:
        return "🐦 X / Twitter"
    elif "reddit.com" in u or "redd.it" in u:
        return "🤖 Reddit"
    elif "pinterest.com" in u or "pin.it" in u:
        return "📌 Pinterest"
    elif any(d in u for d in TERABOX_DOMAINS):
        return "📦 TeraBox"
    elif "vimeo.com" in u:
        return "🎥 Vimeo"
    elif "twitch.tv" in u:
        return "🟣 Twitch"
    elif "soundcloud.com" in u:
        return "☁️ SoundCloud"
    return "🌐 Universal Video"


def format_duration(seconds: Optional[Any]) -> str:
    if not seconds:
        return "N/A"
    try:
        seconds = int(float(seconds))
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60
        return f"{hours}:{minutes:02d}:{secs:02d}" if hours > 0 else f"{minutes}:{secs:02d}"
    except (ValueError, TypeError):
        return str(seconds)


def format_bytes(size: Optional[int]) -> str:
    if not size or size <= 0:
        return ""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def get_valid_cookie_file(candidates: List[Path], domain_keyword: str) -> Optional[str]:
    """Validates that a cookie file actually contains cookies for the target domain."""
    for cp in candidates:
        if cp.exists() and cp.is_file() and cp.stat().st_size > 0:
            try:
                content = cp.read_text(encoding="utf-8", errors="ignore")
                if domain_keyword.lower() in content.lower():
                    return str(cp)
            except Exception:
                continue
    return None


def get_ydl_opts_for_url(
    url: str,
    quality: str = "best",
    download: bool = False,
    output_path: Optional[str] = None
) -> Dict[str, Any]:
    """Generates optimized yt-dlp configuration customized for target domain."""
    u_lower = url.lower()

    opts = {
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'noplaylist': True,
        'windowsfilenames': True,
        'http_headers': {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/126.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate',
        },
        # High speed parallel network options
        'concurrent_fragment_downloads': 16,
        'buffersize': 1024 * 1024 * 16,
        'http_chunk_size': 20 * 1024 * 1024,
        'retries': 5,
        'fragment_retries': 5,
        'socket_timeout': 15 if not download else 45,
    }

    # Locate FFmpeg binary
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        for candidate in [
            BASE_DIR / "ffmpeg.exe",
            BASE_DIR / "bin" / "ffmpeg.exe",
            BASE_DIR.parent / "DownTG-Bot" / "ffmpeg.exe",
            Path("C:/ffmpeg/bin/ffmpeg.exe"),
            Path("C:/Program Files/ffmpeg/bin/ffmpeg.exe"),
        ]:
            if candidate.exists():
                ffmpeg_bin = str(candidate)
                break
    if ffmpeg_bin:
        opts['ffmpeg_location'] = str(ffmpeg_bin)

    # Locate Node.js runtime for JavaScript challenge solving
    node_bin = shutil.which("node") or (
        r"C:\Program Files\nodejs\node.exe"
        if os.path.exists(r"C:\Program Files\nodejs\node.exe")
        else None
    )
    if node_bin:
        opts['js_runtimes'] = {'node': {'path': str(node_bin)}}

    # Platform-specific options & cookies:
    # 1. YouTube (Uses modern mweb/web client and verifies cookies belong to youtube.com)
    if "youtube.com" in u_lower or "youtu.be" in u_lower:
        opts['extractor_args'] = {
            'youtube': {
                'player_client': ['mweb', 'web'],
            }
        }
        cookie_candidates = [
            BASE_DIR / "youtube_cookies.txt",
            BASE_DIR / "cooky" / "youtube" / "cookies.txt",
            BASE_DIR / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cooky" / "youtube" / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cookies.txt",
        ]
        valid_cookie = get_valid_cookie_file(cookie_candidates, "youtube.com")
        if valid_cookie:
            opts['cookiefile'] = valid_cookie
            logger.info(f"Using verified YouTube cookies: {valid_cookie}")
        else:
            logger.info("No verified YouTube cookies found; proceeding in cookie-free mode.")

    # 2. Instagram (Mobile user agent and verified instagram.com cookies)
    elif "instagram.com" in u_lower:
        opts['http_headers'] = {
            'User-Agent': (
                'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) '
                'AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 '
                'Instagram 314.0.0.19.109'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
            'Referer': 'https://www.instagram.com/',
        }
        cookie_candidates = [
            BASE_DIR / "cookies.txt",
            BASE_DIR / "instagram_cookies.txt",
            BASE_DIR / "cooky" / "instagram" / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cooky" / "instagram" / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cookies.txt",
        ]
        valid_cookie = get_valid_cookie_file(cookie_candidates, "instagram.com")
        if valid_cookie:
            opts['cookiefile'] = valid_cookie
            logger.info(f"Using verified Instagram cookies: {valid_cookie}")

    # 3. Facebook
    elif "facebook.com" in u_lower or "fb.watch" in u_lower or "fb.com" in u_lower:
        opts['http_headers']['Referer'] = 'https://www.facebook.com/'
        cookie_candidates = [
            BASE_DIR / "cooky" / "facebook" / "cookies.txt",
            BASE_DIR / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cooky" / "facebook" / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cookies.txt",
        ]
        valid_cookie = get_valid_cookie_file(cookie_candidates, "facebook.com")
        if valid_cookie:
            opts['cookiefile'] = valid_cookie
            logger.info(f"Using verified Facebook cookies: {valid_cookie}")

    # 4. Twitter / X
    elif "twitter.com" in u_lower or "x.com" in u_lower:
        opts['http_headers']['Referer'] = 'https://twitter.com/'
        cookie_candidates = [
            BASE_DIR / "cooky" / "twitter" / "cookies.txt",
            BASE_DIR / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cooky" / "twitter" / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cookies.txt",
        ]
        valid_cookie = get_valid_cookie_file(cookie_candidates, "twitter.com") or get_valid_cookie_file(cookie_candidates, "x.com")
        if valid_cookie:
            opts['cookiefile'] = valid_cookie
            logger.info(f"Using verified Twitter/X cookies: {valid_cookie}")

    # 5. Reddit
    elif "reddit.com" in u_lower or "redd.it" in u_lower:
        opts['http_headers']['Referer'] = 'https://www.reddit.com/'
        cookie_candidates = [
            BASE_DIR / "cooky" / "reddit" / "cookies.txt",
            BASE_DIR / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cooky" / "reddit" / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cookies.txt",
        ]
        valid_cookie = get_valid_cookie_file(cookie_candidates, "reddit.com")
        if valid_cookie:
            opts['cookiefile'] = valid_cookie
            logger.info(f"Using verified Reddit cookies: {valid_cookie}")

    # 6. TikTok
    elif "tiktok.com" in u_lower:
        opts['http_headers']['Referer'] = 'https://www.tiktok.com/'
        cookie_candidates = [
            BASE_DIR / "cooky" / "tiktok" / "cookies.txt",
            BASE_DIR / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cooky" / "tiktok" / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cookies.txt",
        ]
        valid_cookie = get_valid_cookie_file(cookie_candidates, "tiktok.com")
        if valid_cookie:
            opts['cookiefile'] = valid_cookie
            logger.info(f"Using verified TikTok cookies: {valid_cookie}")

    # Generic cookies fallback
    else:
        cookie_candidates = [
            BASE_DIR / "cooky" / "generic" / "cookies.txt",
            BASE_DIR / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cooky" / "generic" / "cookies.txt",
            BASE_DIR.parent / "DownTG-Bot" / "cookies.txt",
        ]
        for cp in cookie_candidates:
            if cp.exists() and cp.is_file() and cp.stat().st_size > 0:
                opts['cookiefile'] = str(cp)
                break

    # Configure formats for download
    if download:
        if output_path:
            opts['outtmpl'] = output_path

        if quality in ("audio", "mp3"):
            opts['format'] = 'bestaudio/ba/b/best'
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '320',
            }]
        elif quality == "m4a":
            opts['format'] = 'bestaudio[ext=m4a]/bestaudio/ba/b/best'
            opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '256',
            }]
        elif quality in {"2160", "1440", "1080", "720", "480", "360"}:
            opts['format'] = f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/bestvideo+bestaudio/best'
            opts['merge_output_format'] = 'mp4'
        else:  # "best" - Maximum original uncompressed quality
            opts['format'] = 'bestvideo+bestaudio/best[ext=mp4]/bestvideo*+bestaudio*/best'
            opts['merge_output_format'] = 'mp4'

    return opts


async def scrape_terabox_direct(url: str) -> Dict[str, Any]:
    """Runs the TeraBox scraper to extract the direct stream URL."""
    if not TERABOX_SCRAPER.exists():
        raise Exception("TeraBox scraper script not found.")

    cfg = load_admin_config()
    env = os.environ.copy()
    if cfg.get("terabox_api_key"):
        env["TERABOX_API_KEY"] = cfg["terabox_api_key"]
    if cfg.get("terabox_endpoint"):
        env["TERABOX_ENDPOINT"] = cfg["terabox_endpoint"]

    process = await asyncio.create_subprocess_exec(
        "node",
        str(TERABOX_SCRAPER),
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await process.communicate()
    raw = stdout.decode("utf-8", errors="ignore").strip()

    json_start = raw.find("{")
    json_end = raw.rfind("}")
    if json_start == -1 or json_end == -1:
        raise Exception(f"TeraBox scraper returned invalid output: {raw[:150]}")

    data = json.loads(raw[json_start:json_end + 1])
    if not data.get("success"):
        raise Exception(data.get("error", "Failed to extract TeraBox link."))

    return data


def extract_instagram_embed_sync(url: str) -> Optional[Dict[str, Any]]:
    """Extracts public Instagram reel or post direct video URL via public embed."""
    match = re.search(r'/(?:reel|reels|p)/([A-Za-z0-9_-]+)', url)
    if not match:
        return None
    shortcode = match.group(1)
    embed_url = f"https://www.instagram.com/reel/{shortcode}/embed/captioned/"
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/126.0.0.0 Safari/537.36'
        ),
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }
    req = urllib.request.Request(embed_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            html = response.read().decode('utf-8', errors='ignore')
            v_match = re.search(r'\\"video_url\\":\s*\\"([^\\"]+)\\"', html)
            if not v_match:
                v_match = re.search(r'"video_url":\s*"([^"]+)"', html)
            if not v_match:
                v_match = re.search(r'<video[^>]+src="([^"]+)"', html)

            if v_match:
                video_url = v_match.group(1).replace(r'\u0026', '&').replace('\\/', '/')
                thumb_match = re.search(r'\\"display_url\\":\s*\\"([^\\"]+)\\"', html) or re.search(r'<img[^>]+class="EmbeddedMediaImage"[^>]+src="([^"]+)"', html)
                thumbnail = thumb_match.group(1).replace(r'\u0026', '&').replace('\\/', '/') if thumb_match else ""
                caption_match = re.search(r'<div class="Caption"[^>]*>(.*?)</div>', html, re.DOTALL)
                caption = re.sub(r'<[^>]+>', '', caption_match.group(1)).strip() if caption_match else "Instagram Reel"
                caption = caption[:80] if caption else "Instagram Reel"

                return {
                    "id": shortcode,
                    "title": caption,
                    "uploader": "Instagram Creator",
                    "thumbnail": thumbnail,
                    "duration": 0,
                    "url": video_url,
                    "formats": [
                        {
                            "format_id": "best",
                            "url": video_url,
                            "ext": "mp4",
                            "vcodec": "h264",
                            "acodec": "aac",
                            "height": 1080,
                        }
                    ],
                    "direct_url": video_url,
                }
    except Exception as e:
        logger.warning(f"Instagram embed extraction fallback error: {e}")
    return None


INVIDIOUS_INSTANCES = [
    "https://invidious.nerdvpn.de",
    "https://inv.nadeko.net",
    "https://invidious.private.coffee",
    "https://invidious.projectsegfau.lt",
    "https://invidious.drgns.space",
    "https://yt.artemislena.eu",
    "https://invidious.flokinet.to",
    "https://inv.bp.projectsegfau.lt",
    "https://invidious.lunar.icu",
]

PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://api.piped.privacydev.net",
    "https://piped-api.lunar.icu",
    "https://pipedapi.projectsegfau.lt",
    "https://api.piped.projectsegfau.lt",
]

COBALT_INSTANCES = [
    "https://api.cobalt.tools",
    "https://cobalt-api.kwiatekm.tokyo",
    "https://cobalt.api.scip.io",
    "https://api.wuk.sh",
]


def extract_cobalt_fallback(url: str, is_audio: bool = False) -> Optional[Dict[str, Any]]:
    """Fetches direct video or audio stream via Cobalt API instances."""
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    }
    payload = {
        "url": url,
        "videoQuality": "1080",
        "downloadMode": "audio" if is_audio else "auto",
        "youtubeVideoCodec": "h264"
    }
    data = json.dumps(payload).encode('utf-8')
    for inst in COBALT_INSTANCES:
        try:
            req = urllib.request.Request(inst, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=6) as resp:
                if resp.status in (200, 201):
                    res = json.loads(resp.read().decode('utf-8'))
                    stream_url = res.get("url")
                    if stream_url:
                        logger.info(f"Cobalt successfully generated stream URL from {inst}")
                        return {
                            "stream_url": stream_url,
                            "filename": res.get("filename", "video.mp4")
                        }
        except Exception:
            continue
    return None


def extract_youtube_fallback(url_or_id: str) -> Optional[Dict[str, Any]]:
    """Extracts YouTube metadata and direct stream URLs via Invidious/Piped/Cobalt public APIs when datacenter IP is blocked."""
    match = re.search(r'(?:v=|youtu\.be\/|shorts\/|embed\/|^)([0-9A-Za-z_-]{11})', url_or_id)
    video_id = match.group(1) if match else url_or_id
    if not video_id or len(video_id) != 11:
        return None

    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'application/json',
    }

    # 1. Try Invidious instances
    for instance in INVIDIOUS_INSTANCES:
        try:
            endpoint = f"{instance}/api/v1/videos/{video_id}"
            req = urllib.request.Request(endpoint, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    title = data.get("title") or f"YouTube_Video_{video_id}"
                    uploader = data.get("author") or "YouTube Creator"
                    duration = data.get("lengthSeconds") or 0

                    thumbnails = data.get("videoThumbnails", [])
                    thumb_url = thumbnails[-1].get("url") if thumbnails else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                    if thumb_url.startswith("/"):
                        thumb_url = f"{instance}{thumb_url}"

                    formats = []
                    # Pre-muxed format streams (720p, 360p)
                    for s in data.get("formatStreams", []):
                        q = s.get("qualityLabel") or s.get("resolution") or "720p"
                        h = 720 if "720" in q else (360 if "360" in q else 480)
                        formats.append({
                            "format_id": f"invidious_{q}",
                            "url": s.get("url"),
                            "ext": s.get("container", "mp4"),
                            "height": h,
                            "vcodec": "h264",
                            "acodec": "aac",
                        })

                    # Adaptive streams (1080p, 4K, audio)
                    for a in data.get("adaptiveFormats", []):
                        q = a.get("qualityLabel") or ""
                        type_str = a.get("type", "")
                        if "video" in type_str:
                            h = 1080 if "1080" in q else (2160 if "2160" in q else (720 if "720" in q else 480))
                            formats.append({
                                "format_id": f"invidious_{q}",
                                "url": a.get("url"),
                                "ext": a.get("container", "mp4"),
                                "height": h,
                            })
                        elif "audio" in type_str:
                            formats.append({
                                "format_id": "invidious_audio",
                                "url": a.get("url"),
                                "ext": "mp3",
                                "acodec": "mp3",
                                "vcodec": "none",
                            })

                    if formats:
                        logger.info(f"Successfully extracted YouTube video {video_id} via Invidious instance {instance}")
                        return {
                            "id": video_id,
                            "title": title,
                            "uploader": uploader,
                            "thumbnail": thumb_url,
                            "duration": duration,
                            "formats": formats,
                            "url": watch_url,
                            "engine": "invidious",
                        }
        except Exception:
            continue

    # 2. Try Piped API instances
    for instance in PIPED_INSTANCES:
        try:
            endpoint = f"{instance}/streams/{video_id}"
            req = urllib.request.Request(endpoint, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode('utf-8'))
                    title = data.get("title") or f"YouTube_Video_{video_id}"
                    uploader = data.get("uploader") or "YouTube Creator"
                    duration = data.get("duration") or 0
                    thumb_url = data.get("thumbnailUrl") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

                    formats = []
                    for v in data.get("videoStreams", []):
                        q = v.get("quality") or "720p"
                        h = 1080 if "1080" in q else (720 if "720" in q else 480)
                        formats.append({
                            "format_id": f"piped_{q}",
                            "url": v.get("url"),
                            "ext": "mp4",
                            "height": h,
                            "vcodec": "h264",
                        })
                    for a in data.get("audioStreams", []):
                        formats.append({
                            "format_id": "piped_audio",
                            "url": a.get("url"),
                            "ext": "mp3",
                            "vcodec": "none",
                        })

                    if formats:
                        logger.info(f"Successfully extracted YouTube video {video_id} via Piped instance {instance}")
                        return {
                            "id": video_id,
                            "title": title,
                            "uploader": uploader,
                            "thumbnail": thumb_url,
                            "duration": duration,
                            "formats": formats,
                            "url": watch_url,
                            "engine": "piped",
                        }
        except Exception:
            continue

    # 3. Try YouTube oEmbed & Noembed fallback for metadata + Cobalt for streams
    meta = None
    for oe_url in [
        f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
        f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={video_id}",
    ]:
        try:
            req = urllib.request.Request(oe_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    meta = json.loads(response.read().decode('utf-8'))
                    if meta.get("title"):
                        break
        except Exception:
            continue

    if meta:
        title = meta.get("title", f"YouTube_Video_{video_id}")
        uploader = meta.get("author_name", "YouTube Creator")
        thumb = meta.get("thumbnail_url", f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")

        cobalt_info = extract_cobalt_fallback(watch_url)
        formats = []
        if cobalt_info and cobalt_info.get("stream_url"):
            formats.append({
                "format_id": "cobalt_best",
                "url": cobalt_info["stream_url"],
                "ext": "mp4",
                "height": 1080,
            })
            formats.append({
                "format_id": "cobalt_audio",
                "url": cobalt_info["stream_url"],
                "ext": "mp3",
                "vcodec": "none",
            })
        else:
            formats.append({
                "format_id": "best",
                "url": watch_url,
                "ext": "mp4",
                "height": 720
            })

        return {
            "id": video_id,
            "title": title,
            "uploader": uploader,
            "thumbnail": thumb,
            "duration": 0,
            "formats": formats,
            "url": watch_url,
            "engine": "oembed_fallback",
        }

    return None


def extract_media_info_direct(url: str) -> Dict[str, Any]:
    """Extracts metadata from any URL using configured yt-dlp with multi-client fallbacks."""
    u_lower = url.lower()

    # 1. YouTube multi-client fallback pipeline
    if "youtube.com" in u_lower or "youtu.be" in u_lower:
        client_attempts = [
            ['mweb', 'web'],
            ['tv_embedded', 'mweb'],
            ['web_creator', 'mweb'],
            ['android', 'ios'],
            None  # Default yt-dlp
        ]
        last_err = None
        for clients in client_attempts:
            opts = get_ydl_opts_for_url(url, download=False)
            opts['extract_flat'] = False
            opts['socket_timeout'] = 8  # Fast 8s timeout per attempt to avoid browser timeout
            opts['retries'] = 2
            if clients:
                opts['extractor_args'] = {
                    'youtube': {
                        'player_client': clients,
                    }
                }
            else:
                opts.pop('extractor_args', None)

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        if 'entries' in info and info['entries']:
                            for entry in info['entries']:
                                if entry:
                                    return entry
                        return info
            except Exception as e:
                last_err = e
                logger.warning(f"YouTube attempt with {clients} failed: {e}. Trying next strategy...")
                continue

        logger.warning(f"All yt-dlp clients failed on cloud IP ({last_err}). Calling Invidious/Piped/Cobalt fallback engine...")
        yt_fb = extract_youtube_fallback(url)
        if yt_fb:
            return yt_fb
        raise last_err or Exception("Could not extract video metadata from this YouTube URL.")

    # 2. Instagram with embed fallback
    if "instagram.com" in u_lower:
        try:
            opts = get_ydl_opts_for_url(url, download=False)
            opts['extract_flat'] = False
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if info:
                    if 'entries' in info and info['entries']:
                        for entry in info['entries']:
                            if entry:
                                return entry
                    return info
        except Exception as ig_err:
            logger.warning(f"yt-dlp Instagram extraction failed: {ig_err}. Attempting embed fallback...")
            fallback_info = extract_instagram_embed_sync(url)
            if fallback_info:
                return fallback_info
            raise ig_err

    # 3. Standard extractor for all other platforms
    opts = get_ydl_opts_for_url(url, download=False)
    opts['extract_flat'] = False
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise Exception("Could not extract video metadata from this URL.")
        if 'entries' in info and info['entries']:
            for entry in info['entries']:
                if entry:
                    return entry
        return info


def download_media_direct(
    url: str,
    output_path: str,
    quality: str = "best",
    format_selector: Optional[str] = None
) -> str:
    """Downloads media and merges video/audio into final mp4/mp3 file with multi-client fallbacks."""
    u_lower = url.lower()

    # 1. YouTube multi-client download
    if "youtube.com" in u_lower or "youtu.be" in u_lower:
        client_attempts = [
            ['mweb', 'web'],
            ['tv_embedded', 'mweb'],
            ['web_creator', 'mweb'],
            ['android', 'ios'],
            None
        ]
        last_err = None
        for clients in client_attempts:
            try:
                opts = get_ydl_opts_for_url(url, quality=quality, download=True, output_path=output_path)
                opts['socket_timeout'] = 30
                opts['retries'] = 3
                if clients:
                    opts['extractor_args'] = {
                        'youtube': {
                            'player_client': clients,
                        }
                    }
                else:
                    opts.pop('extractor_args', None)

                if format_selector:
                    opts['format'] = f"{format_selector}/bestvideo+bestaudio/best[ext=mp4]/best"
                    opts['merge_output_format'] = 'mp4'

                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    downloaded_file = None
                    if 'requested_downloads' in info and info['requested_downloads']:
                        downloaded_file = info['requested_downloads'][0].get('filepath')
                    if not downloaded_file or not os.path.exists(downloaded_file):
                        filename = ydl.prepare_filename(info)
                        if quality in ("audio", "mp3"):
                            filename = os.path.splitext(filename)[0] + ".mp3"
                        elif quality == "m4a":
                            filename = os.path.splitext(filename)[0] + ".m4a"
                        elif opts.get('merge_output_format') == 'mp4':
                            filename = os.path.splitext(filename)[0] + ".mp4"
                        if os.path.exists(filename):
                            downloaded_file = filename
                    if not downloaded_file or not os.path.exists(downloaded_file):
                        prefix = os.path.basename(output_path).split("%")[0]
                        for f in DOWNLOAD_DIR.glob(f"{prefix}*"):
                            if f.exists() and f.is_file() and f.stat().st_size > 0 and not f.name.endswith(".part"):
                                downloaded_file = str(f)
                                break
                    if downloaded_file and os.path.exists(downloaded_file):
                        return str(Path(downloaded_file).resolve())
            except Exception as e:
                last_err = e
                logger.warning(f"YouTube download with {clients} failed: {e}. Trying next...")
                continue

        # Fallback to direct stream download via Cobalt / Invidious / Piped if yt-dlp was blocked by cloud IP
        logger.warning("yt-dlp download failed on cloud IP. Using YouTube stream fallback (Cobalt/Invidious)...")
        is_audio = quality in ("audio", "mp3", "m4a")

        # First try Cobalt stream directly
        cobalt_info = extract_cobalt_fallback(url, is_audio=is_audio)
        if cobalt_info and cobalt_info.get("stream_url"):
            ext = "mp3" if is_audio else "mp4"
            match = re.search(r'(?:v=|youtu\.be\/|shorts\/|embed\/|^)([0-9A-Za-z_-]{11})', url)
            v_id = match.group(1) if match else "video"
            safe_output = output_path.replace("%(title).80B", f"yt_{v_id}").replace("%(ext)s", ext)
            req = urllib.request.Request(cobalt_info["stream_url"], headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            with urllib.request.urlopen(req, timeout=90) as resp, open(safe_output, 'wb') as out_f:
                shutil.copyfileobj(resp, out_f)
            if os.path.exists(safe_output) and os.path.getsize(safe_output) > 0:
                return str(Path(safe_output).resolve())

        # Then try Invidious/Piped fallback formats
        yt_fb = extract_youtube_fallback(url)
        if yt_fb and yt_fb.get("formats"):
            target_stream = None
            for f in yt_fb["formats"]:
                if is_audio and f.get("vcodec") == "none" and f.get("url"):
                    target_stream = f.get("url")
                    break
                elif not is_audio and f.get("url") and f.get("vcodec") != "none":
                    target_stream = f.get("url")
                    if quality.isdigit() and f.get("height") == int(quality):
                        break

            if not target_stream:
                for f in yt_fb["formats"]:
                    if f.get("url"):
                        target_stream = f["url"]
                        break

            if target_stream:
                ext = "mp3" if is_audio else "mp4"
                safe_output = output_path.replace("%(title).80B", f"yt_{yt_fb['id']}").replace("%(ext)s", ext)
                req = urllib.request.Request(target_stream, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                })
                with urllib.request.urlopen(req, timeout=90) as resp, open(safe_output, 'wb') as out_f:
                    shutil.copyfileobj(resp, out_f)
                if os.path.exists(safe_output) and os.path.getsize(safe_output) > 0:
                    return str(Path(safe_output).resolve())

        raise last_err or Exception("Downloaded media file could not be located on disk.")

    # 2. Instagram with embed direct download fallback
    if "instagram.com" in u_lower:
        try:
            opts = get_ydl_opts_for_url(url, quality=quality, download=True, output_path=output_path)
            if format_selector:
                opts['format'] = f"{format_selector}/bestvideo+bestaudio/best[ext=mp4]/best"
                opts['merge_output_format'] = 'mp4'

            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
                downloaded_file = None
                if 'requested_downloads' in info and info['requested_downloads']:
                    downloaded_file = info['requested_downloads'][0].get('filepath')
                if not downloaded_file or not os.path.exists(downloaded_file):
                    filename = ydl.prepare_filename(info)
                    if opts.get('merge_output_format') == 'mp4':
                        filename = os.path.splitext(filename)[0] + ".mp4"
                    if os.path.exists(filename):
                        downloaded_file = filename
                if downloaded_file and os.path.exists(downloaded_file):
                    return str(Path(downloaded_file).resolve())
        except Exception as ig_dl_err:
            logger.warning(f"yt-dlp Instagram download failed ({ig_dl_err}). Using direct embed fallback...")
            fb_info = extract_instagram_embed_sync(url)
            if fb_info and fb_info.get("direct_url"):
                d_url = fb_info["direct_url"]
                safe_output = output_path.replace("%(title).80B", f"ig_{fb_info['id']}").replace("%(ext)s", "mp4")
                req = urllib.request.Request(d_url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36'
                })
                with urllib.request.urlopen(req, timeout=30) as resp, open(safe_output, 'wb') as out_f:
                    shutil.copyfileobj(resp, out_f)
                if os.path.exists(safe_output) and os.path.getsize(safe_output) > 0:
                    return str(Path(safe_output).resolve())
            raise ig_dl_err

    # 3. Standard download for all other platforms
    opts = get_ydl_opts_for_url(url, quality=quality, download=True, output_path=output_path)

    if format_selector:
        opts['format'] = f"{format_selector}/bestvideo+bestaudio/best[ext=mp4]/best"
        opts['merge_output_format'] = 'mp4'

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded_file = None

        if 'requested_downloads' in info and info['requested_downloads']:
            downloaded_file = info['requested_downloads'][0].get('filepath')

        if not downloaded_file or not os.path.exists(downloaded_file):
            filename = ydl.prepare_filename(info)
            if quality in ("audio", "mp3"):
                filename = os.path.splitext(filename)[0] + ".mp3"
            elif quality == "m4a":
                filename = os.path.splitext(filename)[0] + ".m4a"
            elif opts.get('merge_output_format') == 'mp4':
                filename = os.path.splitext(filename)[0] + ".mp4"
            if os.path.exists(filename):
                downloaded_file = filename

        if not downloaded_file or not os.path.exists(downloaded_file):
            prefix = os.path.basename(output_path).split("%")[0]
            for f in DOWNLOAD_DIR.glob(f"{prefix}*"):
                if f.exists() and f.is_file() and f.stat().st_size > 0 and not f.name.endswith(".part"):
                    downloaded_file = str(f)
                    break

        if not downloaded_file or not os.path.exists(downloaded_file):
            raise Exception("Downloaded media file could not be located on disk.")

        return str(Path(downloaded_file).resolve())


def remove_file_safely(file_path: Optional[str]):
    """Safely cleans up temporary files after sending to client."""
    if not file_path:
        return
    try:
        p = Path(file_path)
        if p.exists():
            p.unlink()
            logger.info(f"Cleaned up temporary download: {file_path}")
    except Exception as e:
        logger.error(f"Failed to delete {file_path}: {e}")


@app.post("/api/extract")
async def api_extract(req: ExtractRequest):
    """Universal metadata extraction for all video and audio platforms."""
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    loop = asyncio.get_running_loop()

    # 1. TeraBox Link Handling
    if is_terabox_url(url):
        try:
            tb_data = await scrape_terabox_direct(url)
            title = tb_data.get("title", "TeraBox File")
            size_str = tb_data.get("size") or ""
            tb_stream = tb_data.get("stream_url") or tb_data.get("media_url") or tb_data.get("download_url") or ""
            video_formats = [
                {
                    "id": "best",
                    "label": f"⚡ Original Video Download ({size_str})" if size_str else "⚡ Original Quality Video",
                    "badge": "Original MP4",
                    "type": "video"
                }
            ]
            audio_formats = [
                {
                    "id": "audio",
                    "label": "🎵 High-Quality MP3 (320kbps)",
                    "badge": "320kbps MP3",
                    "type": "audio"
                }
            ]
            return {
                "success": True,
                "url": url,
                "title": title,
                "uploader": "TeraBox Cloud",
                "duration": "N/A",
                "thumbnail": tb_data.get("thumbnail") or "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=600&q=80",
                "platform": "📦 TeraBox",
                "stream_url": tb_stream,
                "embed_url": tb_stream,
                "video_formats": video_formats,
                "audio_formats": audio_formats,
                "qualities": video_formats + audio_formats,
            }
        except Exception as e:
            return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

    # 2. Universal yt-dlp Extraction (YouTube, Instagram, Facebook, Twitter, Reddit, TikTok, etc.)
    try:
        info = await loop.run_in_executor(None, extract_media_info_direct, url)
        title = info.get("title") or "Video"
        uploader = info.get("uploader") or info.get("channel") or info.get("creator") or info.get("uploader_id") or "Media Creator"
        thumbnail = info.get("thumbnail") or "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?auto=format&fit=crop&w=600&q=80"
        duration_str = format_duration(info.get("duration"))
        badge = get_platform_badge(url)

        # Detect live watch / stream URL and embed URL
        stream_url = ""
        embed_url = ""
        u_low = url.lower()
        if "youtube.com" in u_low or "youtu.be" in u_low:
            m_yt = re.search(r'(?:v=|youtu\.be\/|shorts\/|embed\/|^)([0-9A-Za-z_-]{11})', url)
            if m_yt:
                embed_url = f"https://www.youtube-nocookie.com/embed/{m_yt.group(1)}?autoplay=1&rel=0"

        formats = info.get("formats", [])
        for f in formats:
            f_url = f.get("url") or ""
            if f_url and f.get("vcodec") != "none" and not f_url.endswith(".m3u8"):
                stream_url = f_url
                break
        if not stream_url and info.get("direct_url"):
            stream_url = info["direct_url"]
        if not stream_url and info.get("url") and not info["url"].startswith("http://127.0.0.1") and "manifest" not in info["url"]:
            stream_url = info["url"]

        # Detect available video resolutions
        heights = set()
        for f in formats:
            h = f.get("height")
            if h and f.get("vcodec") != "none":
                try:
                    heights.add(int(h))
                except (ValueError, TypeError):
                    pass

        # 🎬 Video Quality Section
        video_formats = [
            {"id": "best", "label": "⚡ Max Original Quality (Highest)", "badge": "Source Best", "type": "video"}
        ]

        if any(h >= 2160 for h in heights):
            video_formats.append({"id": "2160", "label": "🎬 4K Ultra HD (2160p)", "badge": "4K", "type": "video"})
        if any(1440 <= h < 2160 for h in heights):
            video_formats.append({"id": "1440", "label": "🎬 2K Quad HD (1440p)", "badge": "2K", "type": "video"})
        if any(1080 <= h < 1440 for h in heights):
            video_formats.append({"id": "1080", "label": "🎬 1080p Full HD", "badge": "1080p", "type": "video"})
        if any(720 <= h < 1080 for h in heights):
            video_formats.append({"id": "720", "label": "📺 720p HD", "badge": "720p", "type": "video"})
        if any(480 <= h < 720 for h in heights):
            video_formats.append({"id": "480", "label": "📱 480p SD", "badge": "480p", "type": "video"})
        if any(360 <= h < 480 for h in heights):
            video_formats.append({"id": "360", "label": "📱 360p Low", "badge": "360p", "type": "video"})

        # Default fallback options if platform doesn't expose separate heights (Instagram, TikTok, Twitter)
        if len(video_formats) == 1:
            video_formats.append({"id": "720", "label": "🎬 Standard HD (720p)", "badge": "HD", "type": "video"})
            video_formats.append({"id": "480", "label": "📱 Standard SD (480p)", "badge": "SD", "type": "video"})

        # 🎵 Audio Format Section
        audio_formats = [
            {"id": "audio", "label": "🎵 High-Quality MP3 (320kbps)", "badge": "320kbps MP3", "type": "audio"},
            {"id": "m4a", "label": "🎵 High-Quality M4A / AAC", "badge": "M4A Audio", "type": "audio"},
        ]

        return {
            "success": True,
            "url": url,
            "title": title,
            "uploader": uploader,
            "duration": duration_str,
            "thumbnail": thumbnail,
            "platform": badge,
            "stream_url": stream_url or "",
            "embed_url": embed_url or "",
            "video_formats": video_formats,
            "audio_formats": audio_formats,
            "qualities": video_formats + audio_formats,
        }
    except Exception as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.get("/api/download")
async def api_download(
    background_tasks: BackgroundTasks,
    url: str = Query(..., description="Target media URL"),
    quality: str = Query("best", description="Quality preset (best, 2160, 1080, 720, 480, 360, audio, m4a)"),
):
    """Downloads requested media and streams it directly to the user's browser."""
    url = url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="URL parameter is required")

    loop = asyncio.get_running_loop()
    file_id = uuid.uuid4().hex[:8]
    output_tmpl = str(DOWNLOAD_DIR / f"web_{file_id}_%(title).80B.%(ext)s")

    # 1. Handle TeraBox via Scraper
    if is_terabox_url(url):
        try:
            tb_data = await scrape_terabox_direct(url)
            media_url = tb_data.get("media_url") or tb_data.get("stream_url") or tb_data.get("download_url")
            file_path = await loop.run_in_executor(
                None,
                download_media_direct,
                media_url,
                output_tmpl,
                quality,
                None
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"TeraBox download failed: {e}")
    else:
        # 2. Universal yt-dlp (YouTube, Instagram, Facebook, Twitter, Reddit, TikTok, etc.)
        try:
            file_path = await loop.run_in_executor(
                None,
                download_media_direct,
                url,
                output_tmpl,
                quality,
                None
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Download failed: {e}")

    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=500, detail="File could not be found after download.")

    raw_filename = os.path.basename(file_path)
    clean_name = raw_filename.split("_", 2)[-1] if "web_" in raw_filename else raw_filename

    # Safe ASCII fallback for Content-Disposition header
    safe_ascii_name = re.sub(r'[^\w\-. ]', '_', clean_name).strip()
    if quality in ("audio", "mp3") or clean_name.endswith(".mp3"):
        media_type = "audio/mpeg"
        ext = ".mp3"
    elif quality == "m4a" or clean_name.endswith(".m4a"):
        media_type = "audio/mp4"
        ext = ".m4a"
    else:
        media_type = "video/mp4"
        ext = ".mp4"

    if not safe_ascii_name.endswith(ext):
        safe_ascii_name = f"media_{file_id}{ext}"

    background_tasks.add_task(remove_file_safely, file_path)

    encoded_name = urllib.parse.quote(clean_name)
    content_disp = f'attachment; filename="{safe_ascii_name}"; filename*=UTF-8\'\'{encoded_name}'

    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=clean_name,
        headers={
            "Content-Disposition": content_disp,
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
    )


@app.get("/api/health")
async def api_health():
    """Health check endpoint."""
    return {"status": "online", "engine": "yt-dlp Universal + TeraBox", "app": "DownTG-Web", "version": "2.6.0"}


@app.post("/api/admin/login")
async def api_admin_login(req: AdminLoginRequest):
    """Admin login with user: admin, Password: Rohit-admin5064@"""
    if req.username.strip() == ADMIN_USERNAME and req.password.strip() == ADMIN_PASSWORD:
        return {"success": True, "token": ADMIN_TOKEN, "message": "Admin authenticated successfully"}
    return JSONResponse(status_code=401, content={"success": False, "error": "Invalid admin username or password"})


@app.get("/api/admin/config")
async def api_admin_get_config(authorization: Optional[str] = Header(None)):
    """Fetches the current persistent TeraBox API Key and configuration."""
    token = (authorization or "").replace("Bearer ", "").strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")
    cfg = load_admin_config()
    return {"success": True, "config": cfg}


@app.post("/api/admin/config")
async def api_admin_save_config(req: AdminConfigRequest, authorization: Optional[str] = Header(None)):
    """Saves TeraBox API Key and cookies persistently without editing code."""
    token = (authorization or "").replace("Bearer ", "").strip()
    if token != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

    cfg = load_admin_config()
    if req.terabox_api_key is not None:
        cfg["terabox_api_key"] = req.terabox_api_key.strip()
    if req.terabox_endpoint is not None and req.terabox_endpoint.strip():
        cfg["terabox_endpoint"] = req.terabox_endpoint.strip()

    save_admin_config(cfg)

    # Save cookies if provided
    if req.cookies_text and req.cookies_text.strip():
        cookie_file = BASE_DIR / "cookies.txt"
        cookie_file.write_text(req.cookies_text.strip(), encoding="utf-8")
        logger.info(f"Updated cookies.txt from Admin Panel ({len(req.cookies_text)} characters)")

    logger.info(f"Updated TeraBox API configuration from web Admin Panel")
    return {
        "success": True,
        "message": "TeraBox API Key and settings updated successfully!",
        "config": cfg
    }


@app.get("/admin")
async def serve_admin():
    """Serves the Admin Panel view."""
    index_file = BASE_DIR / "index.html"
    if not index_file.exists():
        index_file = WEBSITE_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_file))


@app.get("/")
async def serve_index():
    index_file = BASE_DIR / "index.html"
    if not index_file.exists():
        index_file = WEBSITE_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(str(index_file))


if WEBSITE_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEBSITE_DIR)), name="static")


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print(f"🚀 Starting DownTG-Web Server on http://localhost:{port}")
    uvicorn.run("web_app:app", host="0.0.0.0", port=port, reload=False)
