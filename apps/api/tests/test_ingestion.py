import asyncio
from pathlib import Path

import pytest

from app.ingestion.chunkers import chunk_transcript
from app.ingestion.embedder import HashingEmbedder, OpenAIEmbedder
from app.ingestion.extractors import (
    extract,
    extract_pdf,
    extract_pptx,
    parse_caption_file,
)
from app.ingestion.jobs import run_ingestion
from app.ingestion.models import Segment

VTT = """WEBVTT

00:00:00.000 --> 00:00:04.000
Welcome to the course. Today we cover vectors.

00:00:04.000 --> 00:00:09.500
A vector is an array of numbers. It represents meaning.

00:00:09.500 --> 00:00:15.000
Embeddings map text into this space. Similar text is nearby.

00:00:35.000 --> 00:00:40.000
Let us look at an example now.

00:00:40.000 --> 00:00:46.000
We embed the query and compare with stored vectors.

00:00:46.000 --> 00:00:52.000
Cosine similarity measures the angle between vectors.

01:10:00.000 --> 01:10:05.000
Now switching topics to databases.

01:10:05.000 --> 01:10:11.000
We store vectors in Postgres with pgvector.
"""


@pytest.fixture
def vtt_file(tmp_path: Path) -> Path:
    p = tmp_path / "lecture.vtt"
    p.write_text(VTT, encoding="utf-8")
    return p


def test_vtt_parsing_segments_ms(vtt_file: Path):
    segs = parse_caption_file(vtt_file, video_id="vid1")
    assert len(segs) == 8
    assert segs[0].start_ms == 0
    assert segs[0].end_ms == 4000
    assert segs[2].start_ms == 9500
    assert segs[2].end_ms == 15000
    assert segs[-1].start_ms == (70 * 60 + 5) * 1000
    assert all(s.video_id == "vid1" for s in segs)


def _seg(start_s, end_s, text):
    return Segment(
        text=text, source_type="video", video_id="v",
        start_ms=int(start_s * 1000), end_ms=int(end_s * 1000),
    )


def test_chunker_window_sizes_and_no_mid_sentence_split():
    segs = []
    t = 0.0
    for i in range(40):
        segs.append(_seg(t, t + 2.0, f"Sentence number {i} ends here."))
        t += 2.0
    chunks = chunk_transcript(segs, "res1", "course1", embed_model="hashing-v1")
    assert len(chunks) >= 2
    for c in chunks[:-1]:
        dur = (c.end_ms - c.start_ms) / 1000.0
        assert dur >= 30.0
        assert c.text.rstrip().endswith(".")
    # no sentence is split across chunks: every chunk ends cleanly
    for c in chunks:
        assert "Sentence number" in c.text


def test_chunker_overlap():
    segs = []
    t = 0.0
    for i in range(60):
        segs.append(_seg(t, t + 2.0, f"Point {i} is made here."))
        t += 2.0
    chunks = chunk_transcript(
        segs, "res1", "course1", min_window_s=30.0, max_window_s=60.0, overlap_s=12.0
    )
    assert len(chunks) >= 2
    import itertools

    for prev, cur in itertools.pairwise(chunks):
        assert cur.start_ms is not None and prev.end_ms is not None
        assert cur.start_ms < prev.end_ms  # overlap exists


def test_chunker_force_closes_unpunctuated_transcript():
    # ASR output with no sentence-ending punctuation must still yield
    # bounded chunks (regression: 211 unpunctuated segments -> 1 giant chunk
    # that the end_ms gate then excluded for every T before the video end).
    segs = [_seg(i * 10.0, (i + 1) * 10.0, f"fragment number {i} without stop") for i in range(40)]
    chunks = chunk_transcript(segs, "res1", "course1", embed_model="hashing-v1")
    assert len(chunks) >= 2
    for c in chunks:
        assert (c.end_ms - c.start_ms) / 1000.0 <= 60.0 * 1.5 + 10.0


def test_chunk_ids_deterministic():
    segs = [_seg(i * 3, i * 3 + 2.9, f"Fact {i} is true.") for i in range(40)]
    a = chunk_transcript(segs, "res1", "course1")
    b = chunk_transcript(segs, "res1", "course1")
    assert [c.id for c in a] == [c.id for c in b]
    assert len({c.id for c in a}) == len(a)


def test_hashing_embedder_deterministic_and_dim():
    e = HashingEmbedder()
    v1 = e.embed("hello world vectors")
    v2 = e.embed("hello world vectors")
    assert v1 == v2
    assert len(v1) == 1536
    assert e.name == "hashing-v1"


def test_openai_embedder_requires_key(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "embedding_api_key", "")
    emb = OpenAIEmbedder(api_key="")
    with pytest.raises(RuntimeError):
        emb.embed("hi")


def test_run_ingestion_dry_run(vtt_file: Path):
    row = {"id": "res1", "course_id": "c1", "type": "video", "video_id": "vid1"}
    chunks = asyncio.run(
        run_ingestion(row, vtt_file, embedder=HashingEmbedder(), database_url="")
    )
    assert chunks
    assert all(c.embed_model == "hashing-v1" for c in chunks)
    assert all(c.embedding is not None and len(c.embedding) == 1536 for c in chunks)
    assert all(c.resource_id == "res1" and c.course_id == "c1" for c in chunks)


def test_pdf_extractor(tmp_path: Path):
    fitz = pytest.importorskip("fitz")
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "First page of the PDF.")
    page2 = doc.new_page()
    page2.insert_text((72, 72), "Second page content here.")
    p = tmp_path / "doc.pdf"
    doc.save(str(p))
    segs = extract_pdf(p)
    assert len(segs) == 2
    assert segs[0].page_no == 1
    assert segs[1].page_no == 2


def test_pptx_extractor(tmp_path: Path):
    pytest.importorskip("pptx")
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    slide.shapes.title.text = "Slide One Title"
    slide2 = prs.slides.add_slide(prs.slide_layouts[5])
    tx = slide2.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1))
    tx.text_frame.text = "Slide two body text."
    p = tmp_path / "deck.pptx"
    prs.save(str(p))
    segs = extract_pptx(p)
    assert len(segs) == 2
    assert segs[0].slide_no == 1
    assert segs[1].slide_no == 2


def test_extract_text(tmp_path: Path):
    p = tmp_path / "notes.md"
    p.write_text("Para one.\n\nPara two with more text.", encoding="utf-8")
    segs = extract(p, "note")
    assert len(segs) == 2
    assert segs[0].source_type == "note"
