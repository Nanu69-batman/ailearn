"""OCR for scanned / image-only documents.

The honesty contract
--------------------
A scanned PDF has no text layer. Ordinary extraction returns *nothing* for
it, and nothing is indistinguishable from "a document that was read
successfully and happened to be empty" -- so the ingest used to finish
clean, with zero chunks and a cheerful ``status: done``. That is the exact
failure this module exists to prevent.

So the OCR pass produces a **report**, not just some text. The report says
whether OCR was attempted, whether it worked, how many pages it managed
against how many were expected, and -- when it did not work -- why. A
document that could not be read is reported as unread, and the reason
travels all the way onto the job payload instead of dying in a log line.

Two hard rules, and they are the reason this file has the shape it has:

1. **No heavy imports at module level.** ``torch``, ``transformers`` and
   the model weights are imported inside :func:`_unlimited_ocr_multi` and
   nowhere else. The API must import, boot and serve on a machine that has
   none of them -- which is also what lets the whole test suite run with no
   torch, no weights and no GPU.

2. **Never fabricate text.** A backend that returns nothing yields nothing
   *plus a recorded failure*. There is no fallback that invents a plausible
   paragraph, because a plausible paragraph nobody wrote is worse than no
   answer at all: it would be indexed, retrieved and cited.

OCR is **off by default**. An operator opts in with
``OCR_BACKEND=unlimited-ocr``, and the backend's own dependencies have to
be installed too -- opting in is not enough. If the model is not there, the
pass says exactly that instead of pretending the document was empty.
"""

from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

#: The environment variable that selects an OCR backend. Unset (or set to
#: one of :data:`DISABLED_VALUES`) means "no OCR", which is the default.
OCR_BACKEND_ENV = "OCR_BACKEND"

#: Values of :data:`OCR_BACKEND_ENV` that mean "off". OCR is opt-in, so the
#: unset default is off as well.
DISABLED_VALUES = ("", "none", "off", "0", "false", "disabled")

#: The only backend implemented: Unlimited-OCR.
UNLIMITED_OCR = "unlimited-ocr"

#: Known backends, mapped to the packages each one needs.
#:
#: Those packages are deliberately **not** in ``requirements.txt``: the model
#: wants ~6GB of VRAM and nothing else in the system needs it, so it is an
#: operator-installed extra (see the note in that file). They are probed
#: with ``find_spec`` so that asking "is it installed?" never imports a
#: gigabyte of torch into the process.
_BACKENDS: dict[str, tuple[str, ...]] = {
    UNLIMITED_OCR: ("torch", "transformers"),
}

#: A text layer shorter than this is treated as absent, and OCR becomes the
#: fallback. A real text layer is orders of magnitude longer -- a single
#: line of prose is already past it -- while a scanned page yields nothing
#: at all. The comparison is on the *whole* document, not per page: one
#: readable page in a hundred-page scan is not a text layer.
OCR_MIN_TEXT_CHARS = 50


# ---------------------------------------------------------------------------
# The backend gate: what is switched on, and what is actually installed
# ---------------------------------------------------------------------------


def _selected_backend() -> str:
    """The normalised backend name from the environment (``""`` when off)."""
    return (os.environ.get(OCR_BACKEND_ENV) or "").strip().lower()


def _is_importable(package: str) -> bool:
    """Whether ``package`` can be imported, **without importing it**.

    ``find_spec`` only looks for the module's metadata, so the answer costs
    a directory listing instead of a gigabyte of resident memory.
    """
    try:
        return importlib.util.find_spec(package) is not None
    except (ImportError, ValueError):  # a broken install, not a missing one
        return False


def missing_requirements(backend: str | None = None) -> tuple[str, ...]:
    """Packages the selected backend needs that are not installed.

    Returns an empty tuple when everything is present. The name is
    deliberately a module-level function so that a test (or an operator)
    can substitute it: availability is a *fact about the machine*, and the
    machine can change under a running process.
    """
    name = (backend or _selected_backend()).lower()
    return tuple(
        package
        for package in _BACKENDS.get(name, ())
        if not _is_importable(package)
    )


def ocr_enabled() -> bool:
    """Whether an operator has asked for OCR at all.

    Opting in is necessary but not sufficient -- see
    :func:`ocr_available`.
    """
    backend = _selected_backend()
    if not backend:
        return False
    return backend not in DISABLED_VALUES


def ocr_available() -> bool:
    """Whether OCR can actually run: enabled *and* its backend installed."""
    backend = _selected_backend()
    if backend not in _BACKENDS:
        return False
    return not missing_requirements()


def unavailable_reason() -> str:
    """Why OCR cannot run, in one sentence. ``""`` when it can.

    Every path returns something an operator can act on: which variable to
    set, or which package to install. A silent "no" is what made the
    original bug invisible.
    """
    backend = _selected_backend()
    if not backend or backend in DISABLED_VALUES:
        return (
            f"{OCR_BACKEND_ENV} is not set; OCR is off by default "
            f"(set {OCR_BACKEND_ENV}={UNLIMITED_OCR} to enable it)"
        )
    if backend not in _BACKENDS:
        known = ", ".join(sorted(_BACKENDS))
        return (
            f"{OCR_BACKEND_ENV}={backend!r} is not a known OCR backend "
            f"(known backends: {known})"
        )
    missing = missing_requirements()
    if missing:
        return (
            f"{OCR_BACKEND_ENV}={backend!r} is selected but its dependencies "
            f"are not installed: {', '.join(missing)}"
        )
    return ""


# ---------------------------------------------------------------------------
# What one OCR run produced
# ---------------------------------------------------------------------------


@dataclass
class OcrResult:
    """The output of a single OCR run.

    ``ok`` is false for a run that produced no text -- an empty result is a
    *failure*, never a successfully-read blank page. The two must not look
    alike, so the error is recorded rather than implied.
    """

    #: The whole document as one string, when the backend returned it that way.
    text: str = ""
    #: Per-page text, in page order. Entry *i* is page *i + 1* of the
    #: document; blank pages are kept so the page numbers stay aligned.
    pages: list[str] = field(default_factory=list)
    #: Whether usable text came back.
    ok: bool = False
    #: Why not, when ``ok`` is false.
    error: str = ""


@dataclass
class OcrPassReport:
    """What the OCR pass actually managed.

    The same honesty contract as :class:`app.ingestion.visual.VisualPassReport`
    and :class:`app.ingestion.documents.DocumentPassReport`: a pass that did
    nothing and a pass that read a short document must be tellable apart.
    """

    #: Whether the backend was actually invoked. False when OCR was off or
    #: unavailable -- "skipped" is not "failed", and it is not "complete".
    attempted: bool = False
    #: The backend's own result, when one was obtained.
    result: OcrResult | None = None
    #: Why the pass did not produce text. Empty on success.
    reason: str = ""
    #: Pages the document has, as far as the caller could tell.
    pages_expected: int = 0
    #: Pages the pass actually read.
    pages_read: int = 0

    @property
    def status(self) -> str:
        """``complete`` | ``partial`` | ``failed`` | ``skipped``.

        ``complete`` means the document was read in full. Anything short of
        that is named, never rounded up.
        """
        if not self.attempted:
            return "skipped"
        if self.result is None or not self.result.ok:
            return "failed"
        if self.pages_read < self.pages_expected:
            return "partial"
        return "complete"

    @property
    def warnings(self) -> list[str]:
        """The one line a caller must show. Empty when nothing went wrong."""
        return [self.reason] if self.reason else []


# ---------------------------------------------------------------------------
# Running the backend
# ---------------------------------------------------------------------------


def _run_backend(path: str | Path) -> OcrResult | None:
    """Dispatch to the selected backend.

    The only caller of :func:`_unlimited_ocr_multi`, and therefore the only
    way into the heavy stack.
    """
    backend = _selected_backend()
    if backend == UNLIMITED_OCR:
        return _unlimited_ocr_multi(str(path))
    raise RuntimeError(f"unknown OCR backend {backend!r}")


def _normalise(raw: object) -> OcrResult:
    """Turn whatever the backend returned into an :class:`OcrResult`.

    The verdict is computed here, from the content, rather than trusted
    from the backend: a backend that returns text it did not actually read
    is still a backend that returned text, and the caller has to be able to
    tell "empty" from "blank page".
    """
    if raw is None:
        return OcrResult(ok=False, error="OCR backend returned no result")
    text = str(getattr(raw, "text", "") or "").strip()
    # Blank pages are kept in place: the list index is the page number, and
    # dropping an entry would shift every page after it.
    pages = [str(page or "").strip() for page in (getattr(raw, "pages", None) or [])]
    error = str(getattr(raw, "error", "") or "").strip()
    if not text and not any(pages):
        return OcrResult(
            text=text,
            pages=pages,
            ok=False,
            error=error or "OCR backend returned no text",
        )
    return OcrResult(text=text, pages=pages, ok=True, error="")


def run_ocr(path: str | Path) -> OcrResult:
    """Run the selected backend over a document. **Never raises.**

    A failure is a result with ``ok`` false and an error, never an
    exception: the caller is a document ingest, and an unreadable scan must
    not take down the job that carried it.
    """
    if not ocr_available():
        return OcrResult(ok=False, error=unavailable_reason() or "OCR is unavailable")
    try:
        raw = _run_backend(path)
    except Exception as exc:  # noqa: BLE001 - classified, never propagated
        log.error("OCR failed for %s: %s: %s", path, type(exc).__name__, exc)
        return OcrResult(ok=False, error=f"{type(exc).__name__}: {exc}")
    return _normalise(raw)


def ocr_pdf(path: str | Path) -> str | None:
    """The text of a document, or ``None`` when there is none.

    Convenience wrapper for callers that only want text. ``None`` means
    "no text could be read", which is different from an empty string.
    """
    result = run_ocr(path)
    if not result.ok:
        return None
    if result.text:
        return result.text
    if any(result.pages):
        return "\n\n".join(page for page in result.pages if page.strip())
    return None


def _pages_read(result: OcrResult, pages_expected: int) -> int:
    """How many pages the pass actually read."""
    usable = sum(1 for page in (result.pages or []) if page.strip())
    if usable:
        return usable
    if result.text.strip():
        # One merged document: the backend read the whole file but could not
        # say which page a line came from. The count is the file's, not a
        # guess -- and the segment that carries this text keeps
        # ``page_no=None`` rather than attributing it to a page.
        return pages_expected
    return 0


def _no_text_reason(path: Path, detail: str) -> str:
    """The reason a document could not be read.

    One sentence that names the file, the gap and the cause. It reaches the
    job payload, so it has to stand on its own without the log around it.
    """
    return (
        f"{path.name} yielded zero text for indexing (its text layer holds "
        f"fewer than {OCR_MIN_TEXT_CHARS} characters) and OCR produced none: "
        f"{detail}"
    )


def extract_ocr_pass(
    path: str | Path, pages_expected: int = 0
) -> OcrPassReport:
    """OCR a document, with an honest account of what came back.

    Called only when ordinary extraction found less than
    :data:`OCR_MIN_TEXT_CHARS` characters -- OCR is the fallback for a
    document with no text layer, never a replacement for one that has it.

    ``pages_expected`` is what the caller believes the document has; the
    report says how many pages were actually read against it.
    """
    path = Path(path)
    if not ocr_available():
        reason = _no_text_reason(path, unavailable_reason() or "OCR is unavailable")
        log.error("OCR not run for %s: %s", path.name, reason)
        return OcrPassReport(
            attempted=False,
            result=None,
            reason=reason,
            pages_expected=pages_expected,
            pages_read=0,
        )

    result = run_ocr(path)
    if not result.ok:
        reason = _no_text_reason(path, result.error or "OCR backend returned no text")
        log.error("OCR failed for %s: %s", path.name, reason)
        return OcrPassReport(
            attempted=True,
            result=result,
            reason=reason,
            pages_expected=pages_expected,
            pages_read=0,
        )

    pages_read = _pages_read(result, pages_expected)
    reason = ""
    if pages_read < pages_expected:
        reason = (
            f"OCR read {pages_read} of {pages_expected} page(s) of "
            f"{path.name}; the rest yielded no text"
        )
        log.error("OCR partially read %s: %s", path.name, reason)
    return OcrPassReport(
        attempted=True,
        result=result,
        reason=reason,
        pages_expected=pages_expected,
        pages_read=pages_read,
    )


# ---------------------------------------------------------------------------
# The heavy seam
# ---------------------------------------------------------------------------


def _unlimited_ocr_multi(path: str | Path) -> OcrResult:
    """Run Unlimited-OCR over a document. **The only heavy-import seam.**

    Everything expensive -- ``torch``, ``transformers``, the ~3B parameters
    of model weights, the PDF rasteriser -- is imported *here* and nowhere
    else in the codebase. That is what lets ``app.ingestion.ocr`` be
    imported by the API on a machine with no GPU, and what lets the test
    suite stub this single function instead of installing a model.

    The installed package is expected to expose:

    * ``UnlimitedOCR(device=...)`` -- an engine;
    * ``UnlimitedOCR.render_pages(pdf_path)`` -- an iterable of page images;
    * ``engine.ocr(image)`` -- the text found on that page.

    Anything else raises, loudly, rather than returning an empty result:
    an empty result means "this page is blank", and that must not be what
    an API mismatch looks like.

    The extra is operator-installed (it is not in ``requirements.txt``) and
    is deliberately absent here: it is ~6GB of VRAM and nothing else in
    the system needs it.
    """
    # --- the heavy imports: here, and only here -------------------------
    import torch
    from unlimited_ocr import UnlimitedOCR

    for required in ("render_pages",):
        if not hasattr(UnlimitedOCR, required):
            raise RuntimeError(
                f"the installed 'unlimited_ocr' package does not expose "
                f"{required!r}; the OCR extra needs a compatible version"
            )

    device = "cuda" if torch.cuda.is_available() else "cpu"
    engine = UnlimitedOCR(device=device)
    if not hasattr(engine, "ocr"):
        raise RuntimeError(
            "the installed 'unlimited_ocr' package does not expose an "
            "'ocr' method on its engine; the OCR extra needs a compatible "
            "version"
        )

    pages: list[str] = []
    for image in UnlimitedOCR.render_pages(str(path)):
        pages.append(str(engine.ocr(image) or ""))
    text = "\n\n".join(page for page in pages if page.strip())
    return OcrResult(
        text=text,
        pages=pages,
        ok=bool(text.strip() or any(page.strip() for page in pages)),
    )
