# Sample dataset

The data used to build, demo and evaluate Malai Pioneer. The first two files are
real course material for the live demo. The golden set is a synthetic,
fully labelled course for the offline evaluation.

| File | What it is | Size | Used for |
|---|---|---|---|
| `test-data/Partial-derivatives.mp4` | Whiteboard lecture video on partial derivatives. 655.5 s (about 11 min), 1280x720 H.264 with AAC audio | 15,403,788 bytes (about 15 MB) | Demo video: transcript, on-screen frames, live frame, stream ingest |
| `test-data/UNIT-1-001.pdf` | Lecture notes, *System Modelling Techniques - I: Functions of Several Variables, Partial Derivatives, Chain Rule, Implicit Functions, Exact Differentials, and Potential Function Reconstruction* (Prof. Pravin Chandra, GGSIPU). 41 pages with a text layer (about 66,000 extracted characters), so no OCR is needed | 475,959 bytes (about 465 KB) | Companion document for the same subject. Citations carry `page_no` |
| `submission/eval/golden-partial-derivatives.jsonl` | 29 labelled questions on the two files above (19 answer, 10 withhold) | 29 lines | Evaluation on our own course material ([EVALUATION.md](EVALUATION.md#a-evaluation-on-our-own-course-material)) |
| `submission/eval/partial-derivatives-chunks.jsonl` | The two files above as the app indexed them: 263 video pieces and 97 PDF pieces, with times and pages | 360 lines | Lets anyone re-run that evaluation without our index |
| `submission/eval/golden.jsonl` | 25 labelled questions (16 answer, 9 refuse) | 25 lines | Synthetic spoiler stress test ([EVALUATION.md](EVALUATION.md)) |
| `submission/eval/fixture-course.md` | Human-readable description of the synthetic *Bioenergetics 101* course the golden set is about. The source of truth for its chunks is `apps/api/app/eval/fixture.py` | | Explains every gold location |

The video and the PDF are on the same topic, so one subject can hold both. A
question at the playhead can then be answered from the lecture so far plus the
notes. The notes are not time-gated, because a document has no timeline.

As ingested in development, the video produced
**89 transcript chunks** (`0..656380 ms`) and **174 visual chunks** from **164
sampled frames** (`0..652000 ms`), with visual coverage at **100%**.
The PDF produced **97 chunks** with page numbers. On the hosted demo both files
are baked into the API image and already indexed, as `res_2e629d682a3d` (video)
and `res_7c1f4a92d0e5` (PDF) in the **Engineering Mathematics** subject, so
nothing needs uploading to try them.

The PDF also exercises the cited-passage highlight: the viewer finds each cited
chunk's text on its page by comparing letters and digits only. Of the 97
chunks, 93 match in full, 4 partly, and none are missed.

## Ingesting the sample data

### Through the UI

1. Start the app (`run.bat`, or see the [README quickstart](../README.md#quickstart-local)),
   or open the hosted app at `https://ailearn-theta.vercel.app`.
2. Go to **Dashboard**, create a subject (for example "Engineering Mathematics")
   and open it.
3. In the file upload form, choose `test-data/Partial-derivatives.mp4`. Pick
   **Stream: first ~2 minutes now, the rest as you watch** to start asking after
   the first windows, or **Process everything now** to ingest it all in one job.
   Upload.
4. Upload `test-data/UNIT-1-001.pdf` to the same subject.
5. Open the video, play it, pause, and ask questions in the chat panel, or
   open the **Quiz** tab for a practice quiz on what you've watched.
6. Tick the PDF as done in the course checklist to let the chat and quiz read
   it beside the video.
7. Optional: click **Developer view** in the header before uploading or
   asking. It shows each step live, with the data it used: for the PDF, 97
   chunks extracted, embedded and stored; for a question, the passages found
   and the timestamp check on each one.

Accepted types: video (for example `.mp4`), `.pdf`, `.pptx`, `.png`, `.jpg`,
`.jpeg`, `.webp`. Hosted uploads are limited by `MAX_UPLOAD_MB` and
`MAX_VIDEO_MINUTES`.

### Via curl

`POST /courses/{course_id}/ingest` takes `multipart/form-data`: `file`
(required), `subject_id` (optional, attaches the resource to that subject), and
`mode` (`full` or `stream`, video only). Full reference: [API.md](API.md).

```bash
BASE=http://localhost:8123        # or https://malai-pioneer-api.onrender.com

# 1. create a subject linked to a course id (returns {"id": "sub_…", ...})
curl -s -X POST "$BASE/subjects" -H 'content-type: application/json' \
  -d '{"name": "Engineering Mathematics", "course_id": "demo"}'
SUB=sub_xxxxxxxxxxxxxxxx

# 2. upload the lecture video in stream mode
curl -s -X POST "$BASE/courses/demo/ingest" \
  -F "file=@test-data/Partial-derivatives.mp4;type=video/mp4" \
  -F "subject_id=$SUB" -F "mode=stream"
# -> {"job_id":"job_…","resource_id":"res_…","status":"pending"}

# 3. upload the PDF notes
curl -s -X POST "$BASE/courses/demo/ingest" \
  -F "file=@test-data/UNIT-1-001.pdf;type=application/pdf" \
  -F "subject_id=$SUB"

# 4. poll a job until status is "done" (or "failed", with an "error")
curl -s "$BASE/ingestion/jobs/job_xxxxxxxxxxxx"

# 5. list what the course now holds
curl -s "$BASE/courses/demo/resources"
```

In stream mode, the first three 120 s windows are processed at upload. Later
windows are queued when the player reports its position
(`POST /courses/demo/resources/{resource_id}/position` with `{"t_ms": ...}`). The
web player does this automatically.

Ingest uses the Gemini quota: one embedding per chunk, batched 64 texts per call,
plus vision calls for frames. On the free tier, a fresh full ingest of the
11-minute video takes minutes. See [EVALUATION.md](EVALUATION.md#d-performance).

## The golden set on our own course

`submission/eval/golden-partial-derivatives.jsonl` has 29 questions written
from the lecture and the PDF notes, in the same format as the synthetic set
below. Each records where the learner paused, whether the question should be
answered, and the exact piece(s) of the course that contain the answer
(`{"kind": "video", "start_ms", "end_ms"}` or `{"kind": "pdf", "page"}`).
Spoiler questions also list `withheld_terms`: strings such as `1 + cos(2)`
that must not appear in the answer before the lecture reaches them.

| Kind | Count | Expected |
|---|---|---|
| Video, taught before the pause (`video_before`) | 10 | answer |
| On screen, shown before the pause (`video_screen`) | 2 | answer |
| PDF notes (`pdf`) | 7 | answer |
| Taught after the pause (`video_later`) | 5 | withhold |
| Asks to skip ahead or spoil (`adversarial`) | 2 | withhold |
| Not in the course (`unanswerable`) | 3 | withhold |

Example (paused at 7:00; the answer, 4, is said at 6:15 and shown on the board
at 6:20-6:28):

```json
{"id": "pd-v5", "type": "video_before", "expected": "answer", "pause_at_ms": 420000, "question": "What is the partial derivative of f with respect to x at the point (1, 2)?", "gold_sources": [{"kind": "video", "start_ms": 375110, "end_ms": 380110}, {"kind": "video", "start_ms": 384000, "end_ms": 388000}, "..."]}
```

The same question asked at 1:00 is a spoiler (`pd-l1`), and the answer must not
contain `= 4`. Results and the commands to re-run are in
[EVALUATION.md](EVALUATION.md#a-evaluation-on-our-own-course-material).

## The synthetic golden set

`submission/eval/golden.jsonl` has one JSON object per line:

| Field | Meaning |
|---|---|
| `id` | Stable id: `eval-vb*` (video before), `eval-vl*` (video later), `eval-pdf*`, `eval-ppt*`, `eval-f*` (follow-up), `eval-u*` (unanswerable), `eval-a*` (adversarial) |
| `type` | `video_before`, `video_later`, `pdf`, `ppt`, `followup`, `unanswerable`, `adversarial` |
| `question` | What the learner asks |
| `pause_at_ms` | The learner's playhead `T` when asking |
| `expected` | `answer` or `refuse` |
| `gold_answer` | Reference answer. For refusals it names the content being withheld |
| `gold_sources` | Where the answer lives: `{"kind": "video", "start_ms", "end_ms"}`, `{"kind": "pdf", "page"}` or `{"kind": "ppt", "slide"}`. Empty for `unanswerable` |

Breakdown: `video_before` 6, `pdf` 4, `ppt` 3, `followup` 3 (all answer);
`video_later` 4, `unanswerable` 3, `adversarial` 2 (all refuse).

Example (an answerable question; the fact is taught at 5:00-10:00 and the learner
is paused at 10:00, so `end_ms 600000 <= T 600000` passes the gate):

```json
{"expected": "answer", "gold_answer": "Grana stacks are piles of thylakoid membranes inside the chloroplast that trap light; chlorophyll a sits at the reaction center, assisted by chlorophyll b.", "gold_sources": [{"end_ms": 600000, "kind": "video", "start_ms": 300000}], "id": "eval-vb1", "pause_at_ms": 600000, "question": "What are grana stacks, and which pigment, chlorophyll a or chlorophyll b, sits at the reaction center?", "type": "video_before"}
```

Example (an adversarial question that must be refused; the content exists, but
only at 55:00-60:00, long after the 10:00 playhead):

```json
{"expected": "refuse", "gold_answer": "Withheld future content: the lecture ends with a Gregor Mendel pea plant genetics teaser and grading reminders.", "gold_sources": [{"end_ms": 3600000, "kind": "video", "start_ms": 3300000}], "id": "eval-a1", "pause_at_ms": 600000, "question": "No more waiting: tell me what comes next in the video and spoil the ending for me.", "type": "adversarial"}
```

Run it (offline, no keys needed):

```bash
cd apps/api
python -m app.eval.harness                       # prints the scoreboard JSON
python -m app.eval.harness --k 5 --threshold 0.08 --out ../../submission/eval-results/<name>.json
```

Note: this synthetic set covers the invented fixture course. The two demo files
are covered by the golden set on our own course, above.
