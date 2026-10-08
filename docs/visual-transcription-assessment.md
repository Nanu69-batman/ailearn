# Visual Transcription Assessment

**Verdict: the ingestion pipeline is AUDIO-ONLY. No frame sampling, no image
description, no OCR, no slide extraction from video anywhere in `apps/api`.**

## Current state

What is captured per source:

| Source | Captured | Path |
|---|---|---|
| Local mp4 | Gemini Files upload + **audio-only** transcript prompt. Model sees the container but is asked for `<spoken words>` only. | `ingestion/transcribe.py:189-212` |
| YouTube + captions | English VTT/SRT text (manual, then auto), parsed to timed segments. Video never downloaded. | `ingestion/youtube.py:215`, `ingestion/extractors.py:20` |
| YouTube no captions | `worst[ext=mp4]/worst` download → Gemini audio transcript. | `ingestion/youtube.py:218-222`, `ingestion/youtube.py:185-206` |

The prompt is explicit about scope — `transcribe.py:41` requests
`"text": "<spoken words>"`, and `transcribe.py:46` forces the literal string
`"[non-speech]"` for anything that is not speech. A written equation held on
screen for 20 seconds while the instructor says "this is the derivative of sin"
is therefore indexed as `"[non-speech]"` and nothing else.

## Evidence (file:line)

- `ingestion/transcribe.py:37-47` — `TRANSCRIBE_PROMPT` asks for spoken words only.
- `ingestion/transcribe.py:204-210` — single `generate_content` call, one prompt,
  one file part. No image/frame parts ever constructed.
- `ingestion/youtube.py:215` — caption path is the preferred path; when captions
  exist the video bytes are never fetched, so frames are impossible by construction.
- `ingestion/extractors.py:168-177` — `extract()` dispatch: video → caption parse,
  pdf → `get_text()`, pptx → **text frames only** (`extractors.py:132-136`:
  `shape.has_text_frame`). Slide images, charts, and SmartArt are skipped.
- `ingestion/extractors.py:103-122` — PDF text layer only; no page rasterisation.
- `ingestion/jobs.py:26-34` — videos go through `chunk_transcript`, which only
  ever receives `Segment.text`.
- Repo-wide search for `ocr|ffmpeg|opencv|cv2|image_url|dense_caption|
  video_metadata|extract_frame|thumbnail` across `apps/api`: **2 hits, both are
  comments in `youtube.py:186,194` stating ffmpeg is unavailable.** Zero code.
- `requirements.txt:8-9` — only `google-genai>=2.0` and `yt-dlp>=2024.0`.
  No `opencv`, `pillow`, or OCR engine.

## Impact

Structurally unanswerable today, no matter how good retrieval is:

- **Equations written on screen.** "Why does he write d/dy sin(y) = cos(y)?"
  The correct evidence (`cos(y)`, the notation) exists only as pixels.
- **Board/screen references.** "What does the third step on the whiteboard say?"
  / "what is the variable he defined at the top of the board?"
- **Diagrams and graphs.** "Explain the graph he drew", "what are the axes",
  "what does the arrow from A to B mean". A lecture where the *only* description
  is verbal will produce a correct-but-useless answer from the transcript alone.
- **Slide titles / agenda structure.** "What was on slide 4?" A captioned YouTube
  talk has no notion of slides at all.

This matches the reported failure exactly: a question about a formula that was
*written* got a weak answer, because the written content was never in the index.
The retrieval stack is not at fault — there is nothing to retrieve.

## Feasibility with current stack

**Yes, with zero new packages.**

`google-genai` is already a dependency and already used for Gemini calls at
`ingestion/transcribe.py:176` and `ingestion/embedder.py:103`. Gemini models in
that SDK accept image parts directly (`types.Part.from_bytes(data, mime_type)`
or `types.Part.from_uri`) with the same client object, and Gemini Flash-class
models are natively multimodal. Two implementation routes exist:

- **Route A — ask for frames during the existing transcription call.** The model
  already receives the video file; a prompt variant requesting a parallel array
  of `{start_s, end_s, on_screen_text}` alongside speech would need **no new
  network path and no local decoding.** Cheapest option.
- **Route B — client-side frame extraction.** Requires ffmpeg or opencv, which
  are absent. Not recommended.

Embedding side needs no change: descriptions are text, so they go through the
existing `GeminiEmbedder.embed_batch` (`ingestion/embedder.py:111`) unchanged.

Cost note (one line): free-tier Flash limits apply per *additional* inference
per segment window, so visual passes on long videos will hit RPM/token caps
fastest — keep the pass bounded and cache it the same way transcripts are cached
(`transcribe.py:182`).

## Recommended approach (phased, minimal first)

**Phase 0 — nothing.** Ship nothing until the gap is confirmed on real content.
Cheapest validation: sample a few known lecture timestamps and confirm the
answer is currently absent from the transcript segments.

**Phase 1 — on-screen text only, inside the existing Gemini call (Route A).**
Extend `TRANSCRIBE_PROMPT` with a second JSON array for written/board/slide
content. New segments get `source_type="video"`, non-null `start_ms`/`end_ms`
from the interval the writing is visible on screen. No new dependencies, no
local video decoding, reuses the existing transcript cache key path.

**Phase 2 — diagram/figure narration.** Only if Phase 1 evals show a gap.

**Phase 3 — retroactive backfill.** Re-ingest existing video resources on
demand only, since Phase 1/2 output changes the text of already-indexed chunks.

### Non-negotiable constraints for any phase

1. **Visual segments MUST carry `start_ms` and `end_ms`.** They must flow through
   the same strict `end_ms <= T` gate as speech (`retrieval/store.py:51`,
   `retrieval/hybrid.py:115`, `chat/service.py:44`). A visual chunk with NULL
   `end_ms` is excluded by the defensive filter (`retrieval/store.py:129`) —
   that is correct behaviour and must not be "fixed" by relaxing the gate.
2. **Never relax the gate to `start_ms`.** It is a product safety property.
3. **Visual chunks are NOT unconditionally in scope.** They must not be treated
   like PDF documents. A slide the instructor is *currently pointing at* is a
   spoiler; the timestamp gate is the only thing preventing it. Any path that
   would put `start_ms <= T < end_ms` content in scope is a regression.
4. **Evals must be re-run.** `app/eval/harness.py:134-139` already scores
   timestamp-gate compliance; adding visual chunks changes the candidate set and
   production-gap-rate baseline.

## Open questions

- Does `gemini-3.5-flash-lite` (`app/config.py:9`) accept video-native input plus
  structured JSON, or does it need frames as separate image parts? One live probe.
- What free-tier RPM ceiling applies to the visual pass on a 60-minute video?
  Determines whether Phase 1 must be batched or opt-in per resource.
- Should visual segments share `source_type="video"` or get a distinct type?
  Affects the `chat/service.py:44` scope rule and the citation UI.
- De-duplication: instructor *reads aloud* what is written → same text twice
  distorts embeddings and top-k.
- Is a "[no visual content]" sentinel needed, so a Phase-0 resource is
  distinguishable from a Phase-1-checked one?
- Does adding visual text shift eval-harness refusal baselines enough to require
  re-baselining before release?