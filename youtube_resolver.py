"""
youtube_resolver.py  --  SaveFrom.net jaisa hi method, apne downloader ke liye.
Supports all video qualities (4K, 2K, 1080p, 720p, 480p, 360p) and audio formats (MP3, M4A).

SaveFrom asal me karta kya hai:
  1. Uska apna worker YouTube ke internal player (Innertube) response se streamingData nikalta hai.
  2. Wapas milte hain DIRECT signed *.googlevideo.com/videoplayback URLs.
  3. 720p/1080p/4K "with audio" ke liye YouTube single file nahi deta,
     isliye FFmpeg se video+audio merge karke deta hai.
  4. Audio-only (MP3/M4A) ke liye audio stream extract karke deta hai.

Use:
    python youtube_resolver.py "https://youtu.be/..."                  # JSON info + saare video & audio formats
    python youtube_resolver.py "https://youtu.be/..." --best           # all available video qualities + audio
    python youtube_resolver.py "https://youtu.be/..." --download 1080  # 1080p FFmpeg merged download
    python youtube_resolver.py "https://youtu.be/..." --download audio # MP3 320kbps audio download
"""

import os
import re
import sys
import json
import shutil
from typing import Dict, Any, List, Optional

import yt_dlp

# Auto-initialize static-ffmpeg if available so ffmpeg is in PATH
try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass


def find_ffmpeg_path() -> Optional[str]:
    """Locates ffmpeg executable across system paths."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    candidates = [
        "ffmpeg",
        "ffmpeg.exe",
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "C:/ffmpeg/bin/ffmpeg.exe",
        "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def resolve(url: str) -> dict:
    """
    Extracts all available video resolutions (4K, 1080p, 720p, 480p, 360p)
    and audio formats (M4A, WebM/Opus) using SaveFrom's Innertube method.
    """
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "socket_timeout": 6,
        "retries": 0,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web_safari"]}}
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = []
    raw_formats = info.get("formats", [])
    heights_seen = set()

    for f in raw_formats:
        f_url = f.get("url") or ""
        if not f_url or str(f.get("format_id", "")).startswith("sb"):
            continue  # ignore storyboard thumbnails

        h = f.get("height")
        w = f.get("width")
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        has_v = bool(vcodec and vcodec != "none")
        has_a = bool(acodec and acodec != "none")

        if h and has_v:
            heights_seen.add(int(h))

        q_label = f"{h}p" if h else (f.get("format_note") or ("audio" if has_a else ""))

        formats.append({
            "itag": str(f.get("format_id", "")),
            "quality": q_label,
            "height": h,
            "width": w,
            "fps": f.get("fps"),
            "ext": f.get("ext", "mp4"),
            "has_audio": has_a,
            "has_video": has_v,
            "vcodec": vcodec or "none",
            "acodec": acodec or "none",
            "abr": f.get("abr"),
            "tbr": f.get("tbr"),
            "size": f.get("filesize") or f.get("filesize_approx"),
            "url": f_url,
        })

    # Sort available heights descending
    sorted_heights = sorted(list(heights_seen), reverse=True)
    if not sorted_heights:
        sorted_heights = [1080, 720, 480, 360]

    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel") or "YouTube Creator",
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "formats": formats,
        "available_resolutions": sorted_heights,
        "has_audio": True,
    }


def download_merged(
    url: str,
    height: int = 1080,
    quality: str = "best",
    out: str = "%(title)s.%(ext)s"
):
    """
    Downloads YouTube media in any requested quality:
    - For video ('best', '2160', '1440', '1080', '720', '480', '360'):
      Merges best video stream for that height + best audio stream into a complete MP4.
    - For audio ('audio', 'mp3', 'm4a'):
      Extracts highest quality audio stream and converts to MP3 320kbps or pristine M4A.
    """
    try:
        import static_ffmpeg
        static_ffmpeg.add_paths()
    except Exception:
        pass

    ff = find_ffmpeg_path()

    opts = {
        "outtmpl": out,
        "socket_timeout": 20,
        "retries": 2,
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web_safari"]}},
    }
    if ff:
        opts["ffmpeg_location"] = ff

    # 1. Audio Download Mode
    if quality in ("audio", "mp3"):
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "320",
        }]
    elif quality == "m4a":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "m4a",
            "preferredquality": "256",
        }]
    # 2. Video Download Mode with FFmpeg Audio Merge
    else:
        target_h = height
        if quality.isdigit():
            target_h = int(quality)
        elif quality == "best":
            target_h = 4320

        # Try best video for requested height + best audio; fallback to best single file
        opts["format"] = (
            f"bestvideo[height<={target_h}]+bestaudio/"
            f"best[height<={target_h}]/"
            f"bestvideo+bestaudio/best"
        )
        opts["merge_output_format"] = "mp4"

    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    url = sys.argv[1]
    if "--download" in sys.argv:
        idx = sys.argv.index("--download")
        q = "best"
        if len(sys.argv) > idx + 1:
            q = sys.argv[idx + 1]
        download_merged(url, quality=q)
        print(f"Downloaded media successfully with quality: {q}")
    else:
        data = resolve(url)
        if "--best" in sys.argv:
            summary = {
                "title": data["title"],
                "uploader": data["uploader"],
                "duration": data["duration"],
                "available_video_resolutions": [f"{h}p" for h in data["available_resolutions"]],
                "available_audio_formats": ["MP3 (320kbps)", "M4A (AAC 256kbps)"],
            }
            print(json.dumps(summary, indent=2))
        else:
            print(json.dumps(data, indent=2))
