"""YouTube ingestion: URL validation, metadata, captions, download fallback.

Pipeline per video: validate URL (accept watch/youtu.be/shorts, reject
playlists) -> metadata (title/author/duration, no download) -> manual then
auto English captions parsed to segments -> if no captions, download the
lowest single-file mp4-with-audio and transcribe with Gemini. Videos longer
than settings.yt_max_minutes are refused. Requires network + yt-dlp.
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.config import settings
from app.ingestion.models import Segment

log = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,}$")


class DurationExceededError(ValueError):
    """Raised when a video exceeds YT_MAX_DURATION_MINUTES (maps to HTTP 422)."""


@dataclass
class VideoMetadata:
    video_id: str
    title: str
    author: str | None = None
    duration_s: float | None = None


@dataclass
class NormalizedMedia:
    video_id: str
    title: str
    segments: list[Segment] = field(default_factory=list)
    author: str | None = None
    duration_s: float | None = None
    media_path: str | None = None


def extract_video_id(url: str) -> str:
    """Validate a YouTube URL and return the video id.

    Accepts watch, youtu.be, and shorts URLs. Rejects playlists and
    non-YouTube URLs with ValueError.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("empty YouTube URL")
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.lower()
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    path = parsed.path or ""
    query = parse_qs(parsed.query)

    if parsed.path == "/playlist" or "list" in query and "v" not in query:
        raise ValueError("playlists are not supported; pass a single video URL")
    if host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        if path == "/watch":
            vid = (query.get("v") or [""])[0]
        elif path.startswith("/shorts/"):
            vid = path.split("/shorts/")[1].split("/")[0]
        elif path.startswith(("/embed/", "/live/")):
            vid = path.split("/")[2] if len(path.split("/")) > 2 else ""
        else:
            raise ValueError(f"unsupported YouTube URL path: {path or '/'}")
    elif host == "youtu.be":
        vid = path.lstrip("/").split("/")[0]
    else:
        raise ValueError("URL is not a YouTube video URL")
    vid = (vid or "").strip()
    if "list" in query and not vid:
        raise ValueError("playlists are not supported; pass a single video URL")
    if not vid or not _VIDEO_ID_RE.match(vid):
        raise ValueError("could not extract a YouTube video id from URL")
    return vid


def check_duration_ok(duration_s: float | None) -> None:
    """Raise DurationExceededError when the video exceeds the configured max."""
    if duration_s is None:
        return
    limit = settings.yt_max_minutes * 60
    if duration_s > limit:
        raise DurationExceededError(
            f"video is {duration_s / 60:.1f} min; limit is "
            f"{settings.yt_max_minutes} min"
        )


def fetch_metadata(url: str) -> VideoMetadata:
    """Fetch title/author/duration without downloading (network required)."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp package not installed") from exc
    video_id = extract_video_id(url)
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[attr-defined]
        info = ydl.extract_info(url, download=False)
    return VideoMetadata(
        video_id=video_id,
        title=info.get("title") or video_id,
        author=info.get("uploader") or info.get("channel"),
        duration_s=float(info["duration"]) if info.get("duration") else None,
    )


def segments_from_caption_text(text: str, video_id: str) -> list[Segment]:
    """Parse VTT/SRT caption text into segments (offline, no network)."""
    from app.ingestion.extractors import parse_caption_file

    with tempfile.NamedTemporaryFile(
        suffix=".vtt", mode="w", encoding="utf-8", delete=False
    ) as tmp:
        tmp.write(text)
        tmp_path = tmp.name
    try:
        return parse_caption_file(tmp_path, source_type="video", video_id=video_id)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _download_caption_file(url: str, video_id: str, automatic: bool) -> str | None:
    """Download one English VTT caption file; return its text or None."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp package not installed") from exc
    with tempfile.TemporaryDirectory(prefix="ytcaps") as tmpdir:
        opts = {
            "skip_download": True,
            "writesubtitles": not automatic,
            "writeautomaticsub": automatic,
            "subtitleslangs": ["en"],
            "subtitlesformat": "vtt",
            "outtmpl": str(Path(tmpdir) / "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[attr-defined]
            ydl.download([url])
        vtts = sorted(Path(tmpdir).glob("*.vtt"))
        if not vtts:
            return None
        return vtts[0].read_text(encoding="utf-8", errors="replace")


def fetch_captions(url: str, video_id: str) -> list[Segment]:
    """Try manual then auto English captions; return segments (maybe empty)."""
    for automatic in (False, True):
        try:
            text = _download_caption_file(url, video_id, automatic=automatic)
        except Exception as exc:  # noqa: BLE001 - caption miss is non-fatal
            log.info(
                "caption fetch failed (automatic=%s): %s", automatic, type(exc).__name__
            )
            continue
        if not text:
            continue
        segments = segments_from_caption_text(text, video_id)
        if segments:
            log.info(
                "using %s captions for %s (%d segments)",
                "auto" if automatic else "manual",
                video_id,
                len(segments),
            )
            return segments
    return []


def download_lowest_mp4(url: str, video_id: str) -> Path:
    """Download the lowest single-file mp4-with-audio (no ffmpeg merge)."""
    try:
        import yt_dlp
    except ImportError as exc:
        raise RuntimeError("yt-dlp package not installed") from exc
    dest_dir = Path(settings.yt_data_dir) / video_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        # Single-file only: no merge/post-processing (no ffmpeg available).
        "format": "worst[ext=mp4]/worst",
        "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:  # type: ignore[attr-defined]
        ydl.download([url])
    files = sorted(p for p in dest_dir.iterdir() if p.is_file())
    if not files:
        raise RuntimeError("download produced no file")
    return files[0]


def ingest_youtube(url: str) -> NormalizedMedia:
    """Full pipeline: metadata -> captions (or download+transcribe)."""
    from app.ingestion.transcribe import transcribe_with_gemini

    meta = fetch_metadata(url)
    check_duration_ok(meta.duration_s)
    segments = fetch_captions(url, meta.video_id)
    media_path: str | None = None
    if not segments:
        downloaded = download_lowest_mp4(url, meta.video_id)
        media_path = str(downloaded)
        segments = transcribe_with_gemini(
            downloaded, meta.title, duration_s=meta.duration_s
        )
    for i, seg in enumerate(segments):
        seg.video_id = meta.video_id
        seg.index = i
    return NormalizedMedia(
        video_id=meta.video_id,
        title=meta.title,
        segments=segments,
        author=meta.author,
        duration_s=meta.duration_s,
        media_path=media_path,
    )
