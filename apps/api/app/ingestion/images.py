"""Image ingest: one vision description, indexed as ``source_type == "image"``.

Why an image is *not* time-gated
--------------------------------
``app.retrieval.store.TIME_GATED_SOURCE_TYPES`` gates ``video``,
``video_transcript``, ``visual`` and ``visual_frame`` because those are
moments on a timeline a learner may not have reached. An image has no
timeline: a diagram a learner uploaded is exactly as visible as a PDF page
they uploaded, at every T including T=0. So ``image`` is deliberately NOT in
that set, and an image chunk carries **no** ``start_ms``/``end_ms`` at all --
the same null bounds pdf/pptx chunks carry. Inventing a timestamp here would
be a fabricated citation, not merely an unused one.

What is reused, and what is not invented
----------------------------------------
The description comes from the *same* vision capability video frames use:
``app.ingestion.visual._gemini_describe`` -- one Gemini client, one prompt,
one description grammar. There is no second vision client in this repo and
none is added here. What is NOT reused is the timestamp half of that module:
``build_visual_segments`` derives ``start_ms``/``end_ms`` from frame-index
arithmetic against a duration, and an image has neither a frame index nor a
duration, so that path is never invoked.

Nothing is fabricated. If the image cannot be decoded, or the model returns
no description, or it returns one of the emptiness markers
(``visual._EMPTY_DESCRIPTIONS``), this module raises
:class:`ImageDescriptionError` rather than returning placeholder text. The
ingest job then reports ``status: "failed"`` and the resource indexes zero
chunks -- an honest "not indexed" instead of an invented description that is
indistinguishable from a real one once it is in the vector store.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.ingestion import visual as visual_mod

log = logging.getLogger(__name__)

#: File suffixes accepted as an image upload. Matched case-insensitively.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})

#: Image mime types accepted on upload. ``image/jpg`` is non-standard but is
#: what some browsers send; both map onto the same JPEG decoder.
IMAGE_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/jpg", "image/webp"})

#: Canonical staging suffix per mime, used only when the uploaded filename has
#: no usable suffix. Derived from the declared mime type, never guessed.
MIME_SUFFIX = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
}

#: 1-based position of this image in its resource. A standalone upload is
#: always image 1; the field exists so the citation label is a stable identity
#: rather than an accident of the file being the only one.
DEFAULT_IMAGE_NO = 1


class ImageDescriptionError(ValueError):
    """The image could not be honestly described. Never a fallback text."""


@dataclass
class ImageDescription:
    """What the vision model actually said about one image."""

    image_no: int
    text: str
    #: Empty when the description came back clean. Populated with the
    #: discarded-stray-index note when the model mislabelled the frame.
    warnings: list[str] = field(default_factory=list)


def read_jpeg(path: str | Path) -> bytes:
    """Decode ``path`` to JPEG bytes for the vision model.

    Pillow is already a hard dependency of the frame sampler
    (``av``'s ``frame.to_image()`` returns a Pillow image), so this adds no
    dependency. Everything is converted to RGB: a PNG with an alpha channel
    or a palette would otherwise reach the model in a form the video path
    never produces, and the description would not be comparable with the
    descriptions of sampled frames from the same course.
    """
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as img:
            buf = io.BytesIO()
            img.convert("RGB").save(
                buf, format="JPEG", quality=visual_mod.FRAME_JPEG_QUALITY
            )
            return buf.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageDescriptionError(
            f"cannot decode image {Path(path).name}: {type(exc).__name__}: {exc}"
        ) from exc


def _is_empty(text: str) -> bool:
    """Emptiness check, delegated to the video path's own rule.

    Reuses ``visual_mod.VisualSegment.is_empty`` rather than restating the
    marker list, so an image and a video frame with the same words are judged
    identically. The constructed segment's timestamps are placeholders: they
    are never stored, they exist only to reach the shared predicate.
    """
    probe = visual_mod.VisualSegment(frame_index=0, text=text, start_ms=0, end_ms=1)
    return probe.is_empty


def describe_image(
    path: str | Path, *, image_no: int = DEFAULT_IMAGE_NO
) -> ImageDescription:
    """Describe one image with the video visual pass's Gemini call.

    The model is handed a single image labelled with its index, exactly as
    ``extract_visual_pass`` labels a batch of frames, and the reply goes
    through ``visual._validated_descriptions`` -- so a reply naming an index
    we did not send is discarded and reported, not attached to the image.

    Raises :class:`ImageDescriptionError` when the image cannot be decoded or
    no usable description came back. The caller must not substitute text.
    """
    jpeg = read_jpeg(path)
    result = visual_mod._gemini_describe([(0, jpeg)])
    kept, notes = visual_mod._validated_descriptions(dict(result), {0})
    warnings = list(notes)
    text = (kept.get(0) or "").strip()
    if not text or _is_empty(text):
        # An empty description is a real answer -- "no visible content" -- but
        # indexing it would put a citation with nothing behind it into the
        # store. Refusing is the honest outcome.
        raise ImageDescriptionError(
            f"vision model returned no usable description for image "
            f"{image_no} of {Path(path).name}"
        )
    if warnings:
        log.warning("image ingest notes for %s: %s", Path(path).name, "; ".join(warnings))
    return ImageDescription(image_no=image_no, text=text, warnings=warnings)
