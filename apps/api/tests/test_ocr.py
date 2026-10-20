"""OCR for scanned / image-only documents.

Every test here runs **without torch, transformers, model weights or a GPU**.
The backend is stubbed at its single seam, ``ocr._unlimited_ocr_multi``, which
is the only place the heavy imports happen.

The point of the suite is the honesty contract, not the model: a scanned PDF
must never come back looking like a document that was read successfully and
happened to be empty.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import ClassVar

import pytest

from app.ingestion import documents as documents_mod
from app.ingestion import jobs as jobs_mod
from app.ingestion import ocr as ocr_mod

API_DIR = Path(__file__).resolve().parent.parent
DISABLED_VALUES = ["", "none", "off", "0", "false", "disabled"]


def blank_pdf(tmp_path: Path, pages: int = 1) -> Path:
    """A real PDF with no text layer -- pypdf returns '' for every page."""
    from pypdf import PdfWriter

    target = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with open(target, "wb") as handle:
        writer.write(handle)
    return target


@pytest.fixture(autouse=True)
def _ocr_off_by_default(monkeypatch):
    """No test may inherit OCR from the developer's shell."""
    monkeypatch.delenv(ocr_mod.OCR_BACKEND_ENV, raising=False)


class _Store:
    """Records chunks instead of writing them anywhere."""

    def __init__(self):
        self.chunks: list = []

    def add(self, chunks):
        self.chunks.extend(chunks)
        return len(chunks)


@pytest.fixture
def stub_store(monkeypatch):
    """Point ``jobs._store()`` at a recorder -- nothing touches data/."""
    store = _Store()
    monkeypatch.setattr(jobs_mod, "_store", lambda: store)
    return store


# ---------------------------------------------------------------------------
# Not a hard dependency
# ---------------------------------------------------------------------------


def test_ocr_is_not_imported_at_module_load():
    """Importing the module must not pull torch/transformers into sys.modules.

    Checked in a *fresh interpreter*: the test process itself may already have
    imported them for unrelated reasons, and asserting in-process would then
    prove nothing about this module.
    """
    code = (
        "import sys\n"
        "import app.ingestion.ocr as o\n"
        "print([m for m in ('torch', 'transformers') if m in sys.modules])\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(API_DIR),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]", result.stdout


def test_heavy_ml_packages_are_not_dependencies():
    """torch/transformers/vLLM/SGLang must stay out of requirements.txt."""
    text = (API_DIR / "requirements.txt").read_text(encoding="utf-8").lower()
    required = [
        line.split(">=")[0].split("==")[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for banned in ("torch", "transformers", "vllm", "sglang", "pymupdf", "fitz"):
        assert banned not in required, f"{banned} must not be a dependency"


# ---------------------------------------------------------------------------
# Opt-in, off by default
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", DISABLED_VALUES)
def test_ocr_is_disabled_by_default(monkeypatch, value):
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, value)
    assert ocr_mod.ocr_enabled() is False
    assert ocr_mod.ocr_available() is False
    assert "OCR_BACKEND" in ocr_mod.unavailable_reason()


def test_ocr_disabled_without_the_flag_at_all():
    """No env var set -> off."""
    assert ocr_mod.ocr_enabled() is False
    assert ocr_mod.ocr_available() is False
    assert ocr_mod.run_ocr("missing.pdf").ok is False


def test_selecting_a_backend_without_dependencies_is_unavailable(monkeypatch):
    """Opting in is not enough: the backend must be importable too."""
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, "unlimited-ocr")
    monkeypatch.setattr(ocr_mod, "missing_requirements", lambda: ("torch",))
    assert ocr_mod.ocr_enabled() is True
    assert ocr_mod.ocr_available() is False
    assert "not installed" in ocr_mod.unavailable_reason()
    # A backend that cannot run must not raise out of the entry point.
    assert ocr_mod.ocr_pdf("missing.pdf") is None


# ---------------------------------------------------------------------------
# Trigger condition
# ---------------------------------------------------------------------------


def test_text_layer_pdf_never_triggers_ocr(monkeypatch, tmp_path):
    """A readable PDF is not sent to the model. Not even when OCR is enabled."""
    calls: list[str] = []
    monkeypatch.setattr(
        ocr_mod, "_unlimited_ocr_multi", lambda p: calls.append(str(p))
    )
    monkeypatch.setattr(ocr_mod, "missing_requirements", lambda: ())

    import pypdf

    class _Page:
        def extract_text(self):
            return "Partial derivatives of a multivariate function, page one."

    class _Reader:
        pages: ClassVar[list] = [_Page(), _Page()]

        def __len__(self):
            return 2

    monkeypatch.setattr(pypdf, "PdfReader", lambda *a, **k: _Reader())
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, "unlimited-ocr")

    report = documents_mod.extract_pdf_pass(tmp_path / "readable.pdf")

    assert calls == [], "OCR must not run for a PDF that has a text layer"
    assert len(report.segments) == 2
    assert report.ocr.attempted is False
    assert report.ocr.status == "skipped"
    # Skipped-because-not-needed carries no reason: nothing went wrong.
    assert report.ocr.reason == ""
    assert report.status == "complete"


def test_empty_pdf_triggers_ocr_only_when_enabled(monkeypatch, tmp_path):
    pdf = blank_pdf(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        ocr_mod, "_unlimited_ocr_multi", lambda p: calls.append(str(p))
    )
    monkeypatch.setattr(ocr_mod, "missing_requirements", lambda: ())
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, "unlimited-ocr")

    report = documents_mod.extract_pdf_pass(pdf)
    assert calls == [str(pdf)], "an empty PDF is the one case OCR must handle"
    assert report.ocr.attempted is True


def test_empty_pdf_does_not_touch_the_backend_when_disabled(tmp_path, monkeypatch):
    pdf = blank_pdf(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(
        ocr_mod, "_unlimited_ocr_multi", lambda p: calls.append(str(p))
    )

    documents_mod.extract_pdf_pass(pdf)
    assert calls == []


# ---------------------------------------------------------------------------
# Honest degradation
# ---------------------------------------------------------------------------


def test_empty_pdf_without_ocr_reports_an_explicit_reason(tmp_path):
    """The whole point: no text + no OCR must not look like a clean empty doc."""
    pdf = blank_pdf(tmp_path, pages=3)

    report = documents_mod.extract_pdf_pass(pdf)

    assert report.segments == []
    assert report.text_chars == 0
    # Not "complete", not "skipped-and-fine": the extraction failed.
    assert report.status == "failed"
    assert report.ocr.status == "skipped"
    assert report.ocr.reason, "an empty document must carry a reason"
    assert "OCR" in report.ocr.reason
    assert "OCR_BACKEND" in report.ocr.reason
    assert "zero text" in report.ocr.reason
    assert report.warnings == [report.ocr.reason]
    assert report.ocr.pages_expected == 3


def test_empty_pdf_reports_reason_on_the_job_payload(tmp_path, stub_store):
    pdf = blank_pdf(tmp_path)

    payload = jobs_mod.ingest_document(
        pdf, course_id="c1", resource_id="res_scan", source_type="pdf"
    )

    assert payload["document_chunks"] == 0
    assert payload["document_status"] == "failed"
    assert payload["text_chars"] == 0
    assert payload["ocr_status"] == "skipped"
    assert payload["ocr_reason"]
    assert payload["warnings"], "the gap must reach the job record, not a log line"
    assert stub_store.chunks == []


def test_ocr_failure_is_reported_not_hidden(monkeypatch, tmp_path):
    pdf = blank_pdf(tmp_path)

    def _boom(_path):
        raise RuntimeError("CUDA out of memory (requested 2.00 GiB)")

    monkeypatch.setattr(ocr_mod, "_unlimited_ocr_multi", _boom)
    monkeypatch.setattr(ocr_mod, "missing_requirements", lambda: ())
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, "unlimited-ocr")

    report = documents_mod.extract_pdf_pass(pdf)

    assert report.segments == []
    assert report.ocr.attempted is True
    assert report.ocr.status == "failed"
    assert "CUDA out of memory" in report.ocr.reason
    assert report.ocr.result is not None
    assert report.ocr.result.ok is False


def test_never_fabricates_text(monkeypatch, tmp_path):
    """A backend that returns nothing yields nothing -- plus a recorded failure."""
    pdf = blank_pdf(tmp_path, pages=2)

    monkeypatch.setattr(ocr_mod, "_unlimited_ocr_multi", lambda _p: ocr_mod.OcrResult())
    monkeypatch.setattr(ocr_mod, "missing_requirements", lambda: ())
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, "unlimited-ocr")

    result = ocr_mod.run_ocr(pdf)
    assert result.text == ""
    assert result.pages == []
    assert result.ok is False
    assert result.error, "an empty result is a failure, not a silent blank"
    assert ocr_mod.ocr_pdf(pdf) is None

    report = documents_mod.extract_pdf_pass(pdf)
    assert report.segments == []
    assert report.status == "failed"
    assert report.ocr.pages_read == 0
    assert report.warnings and report.ocr.reason


def test_whitespace_only_backend_output_is_not_text(monkeypatch, tmp_path):
    """`"   "` is nothing. Indexing it would dilute results with a blank."""
    pdf = blank_pdf(tmp_path)
    monkeypatch.setattr(
        ocr_mod,
        "_unlimited_ocr_multi",
        lambda _p: ocr_mod.OcrResult(text="   \n\t "),
    )
    monkeypatch.setattr(ocr_mod, "missing_requirements", lambda: ())
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, "unlimited-ocr")

    assert ocr_mod.run_ocr(pdf).ok is False
    assert documents_mod.extract_pdf_pass(pdf).segments == []


# ---------------------------------------------------------------------------
# Happy path, still without the model
# ---------------------------------------------------------------------------


def test_ocr_text_is_indexed_with_page_numbers(monkeypatch, tmp_path):
    pdf = blank_pdf(tmp_path, pages=2)
    monkeypatch.setattr(
        ocr_mod,
        "_unlimited_ocr_multi",
        lambda _p: ocr_mod.OcrResult(
            text="Page one of the handout\n\nPage two of the handout",
            pages=["Page one of the handout", "Page two of the handout"],
        ),
    )
    monkeypatch.setattr(ocr_mod, "missing_requirements", lambda: ())
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, "unlimited-ocr")

    report = documents_mod.extract_pdf_pass(pdf)

    assert [s.page_no for s in report.segments] == [1, 2]
    assert report.ocr.status == "complete"
    assert report.status == "complete"
    assert report.ocr.reason == ""


def test_merged_ocr_output_is_stored_without_guessing_a_page(
    monkeypatch, tmp_path
):
    """One merged document is stored whole -- never attributed to a page."""
    pdf = blank_pdf(tmp_path, pages=4)
    monkeypatch.setattr(
        ocr_mod,
        "_unlimited_ocr_multi",
        lambda _p: ocr_mod.OcrResult(text="Handout body text"),
    )
    monkeypatch.setattr(ocr_mod, "missing_requirements", lambda: ())
    monkeypatch.setenv(ocr_mod.OCR_BACKEND_ENV, "unlimited-ocr")

    report = documents_mod.extract_pdf_pass(pdf)

    assert len(report.segments) == 1
    assert report.segments[0].page_no is None
    assert report.segments[0].start_ms is None and report.segments[0].end_ms is None