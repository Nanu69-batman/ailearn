"""YouTube ingestion via ``youtube-transcript-api`` (real caption timestamps).

Captions are preferred and are the whole fast path: they are already
timestamped by the uploader against the real video clock, so a transcript
chunk's ``end_ms`` is observed rather than inferred. Re-downloading the video
to run ASR over content that already has captions would be slower and produce
*worse* timestamps.

Fallback (captions absent/private) is the media path: download with ``yt-dlp``
and run the same audio/visual pipeline as a file upload. That path re-derives
duration from the container, so strict-T still has a real upper bound.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.ingestion.chunking import TextSegment

log = logging.getLogger(__name__)

_YT_RE = re.compile(
    r"(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/)([A-Za-z0-9_-]{11})"
)


def parse_youtube_id(url: str) -> str | None:
    match = _YT_RE.search(url or "")
    return match.group(1) if match else None


def fetch_captions(video_id: str) -> list[TextSegment]:
    """Fetch timestamped captions. Returns ``[]`` when none are available."""
    from youtube_transcript_api import YouTubeTranscriptApi

    try:
        # 1.x moved the fetcher behind a class; support both shapes.
        try:
            raw = YouTubeTranscriptApi().fetch(video_id)
        except AttributeError:  # pragma: no cover - older 0.x API
            raw = YouTubeTranscriptApi.get_transcript(video_id)
    except Exception as exc:  # noqa: BLE001 - absence is an expected outcome
        log.info("no captions for %s: %s", video_id, exc)
        return []

    segments: list[TextSegment] = []
    for cue in raw:
        text = (getattr(cue, "text", "") or "").strip()
        if not text:
            continue
        start = float(getattr(cue, "start", 0.0) or 0.0)
        dur = float(getattr(cue, "duration", 0.0) or 0.0)
        segments.append(
            TextSegment(
                text=text,
                start_ms=round(start * 1000),
                # A cue with zero duration still observed its start; give it
                # the conventional 1ms extent rather than an end it never had.
                end_ms=round((start + max(dur, 0.001)) * 1000),
                kind="caption",
            )
        )
    log.info("captions for %s: %d cue(s)", video_id, len(segments))
    return segments


def download_media(
    url: str, *, data_dir: str | Path, max_minutes: int
) -> Path | None:
    """Download video+audio for the ASR fallback. ``None`` on failure."""
    import yt_dlp

    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    video_id = parse_youtube_id(url) or "unknown"
    target = data_dir / f"{video_id}.mp4"
    if target.exists():
        log.info("reusing downloaded media %s", target.name)
        return target
    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": str(data_dir / f"{video_id}.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "max_filesize": max_minutes * 60 * 1_000_000,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except Exception as exc:  # noqa: BLE001 - caller reports ingest failure
        log.error("yt-dlp download failed for %s: %s", url, exc)
        return None
    return target if target.exists() else None