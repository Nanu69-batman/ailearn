# Architecture

Malai Pioneer has two deployable parts: a Next.js web app and a FastAPI API.
The API makes every decision that matters for correctness: which passages a
learner may see at time `T`, which citations are real, and when to refuse. The
browser plays media, captures a frame when asked, and posts requests.

Related: [API.md](API.md) (routes), [DEPLOYMENT.md](DEPLOYMENT.md)
(how to stand this up).

PNG versions of every diagram on this page are in [diagrams/](diagrams/), and
their Mermaid sources are in [diagrams/src/](diagrams/src/):

| Diagram | PNG |
|---|---|
| System overview | [0-system-overview.png](diagrams/0-system-overview.png) |
| The RAG pipeline at a glance | [9-rag-pipeline.png](diagrams/9-rag-pipeline.png) |
| (a) Hosting topology | [1-hosting-topology.png](diagrams/1-hosting-topology.png) |
| (b) Ingest pipeline | [2-ingest-pipeline.png](diagrams/2-ingest-pipeline.png) |
| Inside the visual lane | [5-visual-lane.png](diagrams/5-visual-lane.png) |
| (c) Question flow, agentic and classic | [3-question-flow.png](diagrams/3-question-flow.png) |
| Inside the agentic tutor | [4-agentic-tutor.png](diagrams/4-agentic-tutor.png) |
| (d) Practice quizzes | [6-practice-quiz.png](diagrams/6-practice-quiz.png) |
| (e) Lesson page: viewers, cited passages, chats | [7-lesson-page.png](diagrams/7-lesson-page.png) |
| (f) Developer view: the live pipeline trace | [8-developer-view.png](diagrams/8-developer-view.png) |

**How to read the diagrams.** Every diagram uses the same colours:

| Colour | Means |
|---|---|
| Blue | The learner or teacher, and what they see in the browser |
| Purple | Our server doing the work |
| Amber | An outside AI service (Groq, Gemini, Nemotron, embeddings) |
| Green cylinder | Where something is stored (search index, files) |
| Red outline | A safety check: a decision that can stop or reject something |
| Green box | A good outcome the learner receives |
| Red box | A refusal or a rejected item |

The boxes describe each step in plain words. The text under each diagram gives
the technical detail.

## System overview

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart LR
  U["Learner<br/>watches a lesson, asks questions,<br/>takes quizzes"]:::person
  W["Website<br/>video player, PDF viewer,<br/>AI tutor chat, quiz"]:::person
  A["Our server<br/>decides what the learner may see,<br/>checks every answer's sources"]:::server
  subgraph AI["Outside AI services"]
    Q["Groq<br/>turns speech into text"]:::ai
    G["Gemini<br/>describes what is on screen,<br/>writes quizzes"]:::ai
    O["Nemotron (OpenRouter)<br/>the AI tutor"]:::ai
  end
  P[("Search index (Pinecone)<br/>lesson pieces, findable by meaning")]:::store
  F[("File storage<br/>videos and PDFs")]:::store
  U --> W
  W -->|"questions, quizzes, uploads"| A
  A --> Q
  A --> G
  A --> O
  A --> P
  A --> F

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

The learner only ever talks to our server, through the website. The server
decides what the learner may see at the moment they paused, calls the AI
services, and checks every answer's sources before showing it. The AI services
never decide what is allowed; they only write text from what the server gives
them.

## The RAG pipeline at a glance

Malai Pioneer is a retrieval-augmented generation (RAG) system. No model is
trained or fine-tuned on the course. The lesson files are indexed once, and for
every question the relevant pieces are retrieved and handed to the model, which
may answer only from them.

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart TB
  subgraph INDEX["PART 1 · INDEXING: done once, when a lesson file is uploaded"]
    direction LR
    F["Lesson files<br/>video, PDF,<br/>slides, image"]:::person
    X["Turn everything into text<br/>speech to text (Groq),<br/>what is on screen (Gemini),<br/>text of each PDF page"]:::ai
    C["Split into small pieces<br/>each stamped with its start<br/>and end time, or its page"]:::server
    E["Embed each piece<br/>numbers that capture<br/>its meaning"]:::ai
    S[("Search index<br/>(Pinecone)<br/>pieces + numbers + times")]:::store
    F --> X --> C --> E --> S
  end
  subgraph QUERY["PART 2 · ANSWERING: every time a learner asks"]
    direction LR
    Q["Learner pauses<br/>at 4:10 and asks<br/>a question"]:::person
    QE["Embed the question<br/>the same way"]:::ai
    R["R · RETRIEVE<br/>find the pieces closest<br/>in meaning, only from<br/>this learner's materials"]:::store
    G1["Spoiler guard<br/>keep a piece only if it<br/>ends before 4:10"]:::check
    A["A · AUGMENT<br/>put the surviving pieces,<br/>with their IDs, into<br/>the AI's instructions"]:::server
    GEN["G · GENERATE<br/>the AI writes an answer<br/>using only those pieces,<br/>citing their IDs"]:::ai
    V{"Every cited ID is<br/>one of the pieces<br/>it was given?"}:::check
    OK["Answer with<br/>clickable sources"]:::good
    NO["Honest refusal:<br/>not covered yet"]:::stop
    Q --> QE --> R --> G1 --> A --> GEN --> V
    V -->|"yes"| OK
    V -->|"no"| NO
  end
  INDEX ==>|"the search index built in Part 1 is what Part 2 searches"| QUERY

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

- **Part 1, indexing**, is section (b) below: speech is transcribed by Groq,
  frames are described by Gemini, PDF text is read, and everything is cut into
  timestamped pieces and embedded (`llama-text-embed-v2` on Pinecone when
  hosted).
- **R, retrieve.** The question is embedded the same way, and a vector search
  finds the closest pieces within the learner's own materials. The **spoiler
  guard** (`end_ms <= T`) is not a separate pass in practice: it is part of the
  search filter itself, and every piece returned is checked again in Python. It
  is drawn as its own step because it is the rule that matters most.
- **A, augment.** The surviving pieces go into the prompt with their IDs and
  times, as the only material the model may use.
- **G, generate.** The model (Nemotron through OpenRouter, or Gemini) writes the
  answer and cites piece IDs. Any ID it was not given, or an answer with no
  citations, means a refusal instead of an answer.
- Section (c) shows the full question flow, including the agentic tutor, which
  runs the same retrieve step through its tools.

## (a) Hosting topology

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart LR
  B["Learner's<br/>browser"]:::person
  subgraph VER["Vercel: hosts the website"]
    direction TB
    W["Website<br/>(Next.js)"]:::person
    L["Sign-in helper<br/>proves the learner came<br/>from the course platform"]:::check
    W --> L
  end
  subgraph RN["Render: hosts our server"]
    direction TB
    A["Our server<br/>(FastAPI)"]:::server
    D[("Files on the server<br/>demo video and PDF<br/>built in")]:::store
    A --> D
  end
  subgraph CLOUD["Cloud services our server calls"]
    direction TB
    Q["Groq<br/>speech to text"]:::ai
    G["Gemini<br/>screen descriptions,<br/>quizzes, backup answers"]:::ai
    O["OpenRouter<br/>AI tutor (Nemotron)"]:::ai
    P[("Pinecone<br/>search index")]:::store
  end
  B -->|"opens a lesson"| VER
  VER -->|"starts a signed session"| RN
  B -->|"questions, quizzes, uploads,<br/>video streaming"| RN
  RN -->|"calls when needed"| CLOUD

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

| Component | Where | Why there |
|---|---|---|
| Web (Next.js 15) | Vercel, root dir `apps/web` | Static and edge-friendly. The only server-side code is the launch-signing route, which keeps `LMS_LAUNCH_SECRET` out of the browser |
| API (FastAPI) | Render web service, Docker, free tier (`render.yaml`, `apps/api/deploy/render/Dockerfile`) | Vercel cannot host it: the function bundle limit is 250 MB, while faster-whisper, PyAV and chromadb are larger, and ingest jobs run in the background for minutes. One process, because jobs and sessions live in memory |
| Vectors | Pinecone serverless (`VECTOR_STORE=pinecone`) | Render's free disk is wiped on restart, so the index lives outside the container. The same `build_where` scope + strict-T filter runs in Pinecone, and every match is re-checked against it in Python. Local dev uses Chroma on disk (`VECTOR_STORE=chroma`) |
| Files | Container disk | A hackathon trade-off. The demo video and the UNIT-1 PDF notes are baked into the image, so they are always there, together with their chapters (`apps/api/deploy/seed/chapters/` → `data/chapters/`) and the demo subject. Files uploaded on the hosted site, and their chapters, last until the next restart. The media route serves them with HTTP Range |
| Tutor agent | OpenRouter, `nvidia/nemotron-3.5-super-vl:free` | Free, reliable tool calling with a 1M context. Optional: with no `OPENROUTER_API_KEY`, the classic pipeline answers every question |
| LLM / vision | Gemini `gemini-3.5-flash-lite` | Quiz writing (about 15 s for a whole quiz), fallback answers and frame descriptions. Blocking SDK calls run on a worker thread, so a slow answer never stalls other requests |
| Embeddings | Pinecone Inference `llama-text-embed-v2` (1024-d) hosted; Gemini `gemini-embedding-2` (3072-d) locally | Pinecone's free plan meters 5M tokens a month, not Gemini's ~1,000 texts a day. The store refuses to start if the index width and the embedder disagree |
| Speech to text | Groq `whisper-large-v3-turbo`, with local faster-whisper as fallback | About 3 s per 2-minute window on Groq, against 14-18 s for local `tiny` (measured, see [EVALUATION.md](EVALUATION.md)). This is what makes a small CPU host workable |

Free-tier protection on the API: `MAX_UPLOAD_MB`, `MAX_VIDEO_MINUTES` (read from
the container header before any model call), `MAX_UPLOADS_PER_HOUR`, a
60 requests/min/IP limit on chat routes, and fail-fast quota handling. A spent
daily quota returns `503 {"detail": "... quota reached ..."}` and does not keep
retrying.

## (b) Ingest pipeline

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart TD
  UP["1. Teacher uploads a video, PDF,<br/>slides or image"]:::person --> CAP{"Within the size, length<br/>and upload limits?"}:::check
  CAP -->|"no"| REJ["Rejected,<br/>nothing is saved"]:::stop
  CAP -->|"yes"| SAVE[("2. Save the file")]:::store
  SAVE --> KIND{"What kind of file?"}:::server
  KIND -->|"PDF or slides"| DOC["Read the text on each page"]:::server
  KIND -->|"image"| IMG["AI describes the picture"]:::ai
  KIND -->|"video"| WIN["3. Cut the video into<br/>2-minute sections"]:::server
  WIN --> T["Turn speech into text<br/>with the time of every sentence<br/>(Groq)"]:::ai
  WIN --> V["Take a picture of the screen<br/>every 4 seconds; AI writes<br/>what it shows (Gemini)"]:::ai
  T --> CH["4. Cut into small pieces,<br/>each stamped with when it<br/>starts and ends in the video"]:::server
  V --> CH
  DOC --> CH
  IMG --> CH
  CH --> EMB["5. Turn each piece into numbers<br/>that capture its meaning<br/>(embeddings)"]:::ai
  EMB --> IDX[("6. Save in the search index<br/>(Pinecone)")]:::store
  IDX --> READY["Ready: learners can ask about<br/>everything processed so far"]:::good
  IDX --> CHAP["7. AI splits the material into<br/>named chapters: time ranges for a video,<br/>page or slide ranges for a document"]:::ai
  CHAP --> CHOK{"Chapter list usable?<br/>(real boundaries, no gaps)"}:::check
  CHOK -->|"yes"| CHS[("Save the chapters<br/>beside the file")]:::store
  CHOK -->|"no"| CHF["Fixed split instead<br/>(by headings or even lengths)"]:::server
  CHF --> CHS
  CHS --> CHR["Chapters tab ready; rebuilt as<br/>stream mode processes more"]:::good
  STREAM["Stream mode: only the first 2 minutes<br/>are processed now; the next sections are<br/>processed while the learner watches"]:::person -.-> WIN

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

How it works:

- **Windows.** Video is processed in 120 s windows. Each window has two
  independently committed stages. Stage T (transcribe, chunk, embed, store) runs
  first, so a window becomes askable quickly. Stage V (frame descriptions) runs
  in a second lane in parallel, one window behind.
- **Modes.** `full` processes every window in one job. `stream` processes the
  first window (2 minutes) at upload. After that the player reports its
  position (`POST /courses/{id}/resources/{rid}/position`), and the API queues up
  to two transcript windows ahead of the playhead plus a visual catch-up job.
- **Frontiers live in the store, not in memory.** Chunks carry
  `processed_through_ms` and `visual_through_ms`, so the next window can be
  derived after a restart. A window with nothing to index still advances the
  frontier.
- **Timestamps never come from a language model.** Transcript times come from
  the ASR segments, shifted by the window offset. Frame times come from the
  sampler's index. A frame index in the model's reply that the sampler never
  produced is dropped and recorded; it is never clamped or reassigned.
- **Degradation is reported.** Each job reports `visual_status`
  (complete / partial / failed / skipped), `visual_coverage_pct` and warnings. A
  scanned PDF that could not be read reports `skipped` or `failed` with the reason.
  It never looks like an empty document that was read successfully.

### Chapters, after ingestion

When an ingest job succeeds, a background task writes chapters for the
resource (`apps/api/app/ingestion/chapters.py`), served at
`GET /resources/{id}/chapters` and shown in the lesson panel's Chapters tab.

- **What a chapter is.** A short topic name over a contiguous span: a time range
  for a video, a page range for a PDF, a slide range for a deck.
- **How.** One model call over a compact, ordered digest of the indexed chunks
  (read by exact `resource_id`). Its JSON is validated: boundaries are snapped
  to real timestamps or pages, sorted, gaps and overlaps repaired, junk titles
  dropped. Unusable output, a failed call or the offline stub falls back to a
  deterministic split, which is still a ready result. A chapter failure never
  fails the ingest.
- **Storage and staleness.** One JSON file per resource in `data/chapters/`
  (`CHAPTERS_DIR` overrides it). A stream-mode video records the frontier it
  was built from and is rebuilt once processing has moved on by more than
  2 minutes, or the video is complete. A missing or stale file is rebuilt in
  the background the next time it is requested; the route never waits for the
  model. Deleting a resource deletes its chapters.
- **Catalogue only.** Chapters never feed retrieval, `build_where`, the strict
  `end_ms <= T` gate or citation validation. A chapter list that names the end
  of a lecture is navigation the learner chose to open, not evidence for an
  answer.
- **Demo seed.** The demo video (3 chapters) and PDF (8 chapters) have their
  chapter files baked into the API image, generated against the same Pinecone
  chunks, so they are ready after every restart with no model calls.

### Inside the visual lane

Frames are not transcribed. Gemini's vision model *describes* each one in
text, and that text is indexed like the transcript
(`apps/api/app/ingestion/visual.py`).

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart TD
  W["A 2-minute section of video"]:::server --> S["Take a picture of the screen<br/>every 4 seconds"]:::server
  S --> D{"Looks the same as<br/>the last picture kept?"}:::check
  D -->|"yes"| X["Skip it: no AI call needed.<br/>The previous picture now<br/>covers this time too"]:::server
  D -->|"no"| R["Shrink the picture<br/>to save data"]:::server
  R --> B["Send 8 pictures at a time"]:::server
  B --> P["Gemini writes what each one shows:<br/>equations, labels, slide titles,<br/>diagrams, colours"]:::ai
  P --> CHK{"Did the AI describe a picture<br/>we actually sent?"}:::check
  CHK -->|"no"| DROP["Ignored and logged.<br/>Never guessed"]:::stop
  CHK -->|"yes"| TS["Time comes from when we took<br/>the picture, never from the AI"]:::good
  P -->|"AI busy"| RT["Wait and retry"]:::server
  RT -->|"still failing"| GAP["Recorded as<br/>partly described"]:::stop
  X -.-> TS
  TS --> M["Group 3 pictures into<br/>one piece (12 seconds)"]:::server
  M --> E[("Added to the search index<br/>next to the spoken text")]:::store

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

- **Cost:** a 655 s lecture samples 164 frames. That's 21 requests before
  duplicates are skipped. Calls start at least 4 s apart, with up to 3 in
  flight, to stay under the free tier's ~15 requests a minute.
- **Strict timestamps:** a frame's time comes from its index, so a description
  is gated exactly like a transcript passage. A merged chunk ends at its latest
  frame, so it becomes answerable only after every frame in it has played.
- **Live frames:** when a learner asks "what's on screen?", the player sends
  the paused frame itself. It's described on the spot as a passage at exactly
  T and never indexed.

## (c) Question flow

Every question passes the same session and scope checks. Then the **agentic
tutor** answers first. The **classic pipeline** answers when the agent is not
configured, or when its reply fails validation. Both paths read the lesson only
through the strict-T gate.

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart TD
  Q["1. Learner pauses at, say, 4:10<br/>and asks a question"]:::person --> AUTH{"A real, signed-in session?"}:::check
  AUTH -->|"no"| NO["Rejected"]:::stop
  AUTH -->|"yes"| GEN{"A Malai chat in no subject,<br/>not asking about their materials?"}:::check
  GEN -->|"yes"| GK["General study answer from<br/>Gemini's own knowledge,<br/>no sources claimed"]:::ai
  GEN -->|"no"| SC["2. Work out what they may read:<br/>this video up to 4:10, plus any<br/>material they ticked as done;<br/>a subject chat reads only that subject"]:::server
  SC --> HAS{"AI tutor available?"}:::server

  subgraph AGENT["AI tutor path (Nemotron)"]
    PF["3a. Fetch matching lesson pieces,<br/>only from before 4:10"]:::server
    PF --> AG["AI tutor writes an answer<br/>and names its sources"]:::ai
    AG --> AV{"Was every source really<br/>given to it just now?"}:::check
  end

  subgraph CLASSIC["Standard path (Gemini)"]
    SR["3b. Search the index for pieces<br/>that match the question"]:::store
    SR --> GATE["Spoiler guard: drop any piece<br/>that ends after 4:10"]:::check
    GATE --> LLM["Gemini answers using<br/>only those pieces"]:::ai
    LLM --> CIT{"Has sources, and is every<br/>source one of those pieces?"}:::check
  end

  HAS -->|"yes"| PF
  HAS -->|"no"| SR
  AV -->|"yes"| OK["4. Answer with clickable sources:<br/>the exact video moment or PDF page"]:::good
  AV -->|"no, or the tutor is busy"| SR
  CIT -->|"yes"| OK
  CIT -->|"no"| RF["Says it cannot answer that yet,<br/>instead of guessing"]:::stop
  LLM -.->|"while it writes"| LIVE["Answer appears word by word;<br/>the checked answer then replaces it"]:::person
  LIVE -.-> OK

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

### Inside the agentic tutor

The agent is `app/chat/agent.py`, and its tool loop works like this:

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart TD
  Q["Question at the paused moment"]:::person --> PRE{"Just chatting?<br/>(hi, who are you)"}:::server
  PRE -->|"yes"| CONV["Friendly short reply,<br/>no sources needed"]:::good
  PRE -->|"no"| PF["The server fetches the lesson first:<br/>matching pieces, or everything watched<br/>so far for a summary"]:::server
  PF --> TOOLS["Every lookup only sees what has<br/>already played, and only the learner's<br/>own materials"]:::check
  TOOLS --> AG["AI tutor (Nemotron) answers<br/>in one go, naming its sources"]:::ai
  AG --> VAL{"Check the answer"}:::check
  VAL -->|"every source was really given to it"| GR["Answer with sources"]:::good
  VAL -->|"forgot its sources"| REP["Ask it once more<br/>to add them"]:::server
  REP --> VAL
  VAL -->|"clearly labelled<br/>'Not from the video'"| SUP["General-knowledge answer,<br/>shown with that label"]:::good
  VAL -->|"made-up source, or AI busy"| FB["Hand over to the<br/>standard path"]:::stop

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

- **The model chooses tools; code chooses what it sees.** Both tools run through
  the same scope and strict-T gate as the classic path. A request for
  `get_lesson_content(0, 99999)` returns only what has played.
- **Repairs before falling back.** An answer that cites nothing gets one
  cite-repair turn. A citation the model shortened (`c3_83fd4ed4` for
  `res_…_c3_83fd4ed4`) is restored only when exactly one passage returned this
  turn ends with it; anything else is an unknown id and falls back. Only after
  that does the classic pipeline take over.
- **Prefetch, then one call.** The server runs the read the model would ask
  for first (`search_lesson` for a question, `get_lesson_content(0, T)` for a
  summary or quiz) through the same gates, and hands it over as that tool's
  result. The model then answers in one call, which on the free endpoint is
  the difference between one slow round trip and four.
- **Budgets.** At most 2 model calls (the answer, plus one cite-repair turn),
  and no time limit of our own: a slow free-tier reply is waited for
  (`AGENT_TIMEOUT_S` / `AGENT_TOTAL_TIMEOUT_S` bring one back). A reply cut off at the token limit has any unfinished
  `[chunk_id=...` removed and asks the learner to say "continue".
- **Without the agent**, a regex intent router (`app/chat/intent.py`) keeps
  the classic path from going wrong:
  - Small talk gets a short conversational reply with no retrieval.
  - "What questions can the prof ask?" becomes quiz mode.
  - "What have we covered?" becomes summarize mode, over a coverage query.

"Ask about this frame" (`POST /chat/live-frame`) adds one step before the query.
The captured JPEG is described by Gemini, and the description joins the pool as a
passage with `start_ms == end_ms == T`, so it passes the same gate. `T` is first
checked against the duration the server read from the file. An out-of-range `T`
gets `422`; it is never clamped.

**Streaming.** `POST /chat/stream` runs exactly the same pipeline as `/chat`
and sends Server-Sent Events while it works (`app/chat/streaming.py`). A sink
bound for that one request receives short status lines ("Searching the
lesson") and the provider's text as it is generated: Gemini streams token by
token, while the agent path shows status lines until its answer is ready.
Citation markers are stripped from the preview, and a marker that has not
closed yet is held back. The preview is written from the already time-gated
passages, so it cannot show anything past `T`. Citation validation still runs
on the finished reply, and the final `done` event carries the validated
answer, which replaces the preview (a refusal replaces it entirely). Outside
this route the sink is unset and every streaming hook does nothing.

**Malai chats: scoped or general.** The `/malai` page holds chats that are not
tied to one open material.

- A chat filed under a subject sends that subject's resources as
  `scope_resource_ids`. They are intersected with the session grant, so they
  can only remove materials. A session with course id `"*"` drops the course
  filter and reads across subjects, still only inside its `resource_scope`.
  With no playhead, such a chat reads documents and the videos marked done.
  The strict `end_ms <= T` gate is unchanged.
- A chat in no subject sends `general: true`. It is answered from general
  knowledge with no retrieval and zero citations (`mode: "general"`), unless
  the message explicitly points at the learner's materials ("my notes", "the
  lecture", "the uploaded pdf"), which takes the ordinary grounded pipeline
  (`tests/test_general_chat.py`).

**Answer style.** Every prompt (grounded, agent and general) carries a format
rule asking for Markdown structure (headings for long answers, bold key terms,
numbered steps, display equations on their own lines), and the tutor answers
in the learner's language and matches their tone. The web app renders it with
remark-gfm and KaTeX.

## (d) Practice quizzes

The **Quiz** tab beside the chat writes a real quiz: multiple choice,
numerical and written questions. It reads the lesson through the same gates as
chat. The whole quiz costs **one** model call. Multiple-choice and numerical
answers are checked in the browser with no model call; only written answers
go back to the model for grading.

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart TD
  ASK["Learner types &quot;quiz me on...&quot;<br/>or /quiz in any chat"]:::person -.->|"opens the form"| L
  L["1. Quiz form: 1 to 20 questions,<br/>which kinds (multiple choice, number<br/>answer, written answer), optional<br/>instructions such as a topic"]:::person --> AUTH{"A real, signed-in session?"}:::check
  AUTH -->|"no"| NO["Rejected"]:::stop
  AUTH -->|"yes"| WHERE{"Which chat is it from?"}:::server
  WHERE -->|"lesson or subject chat"| READ["2. Gather what they may read:<br/>watched only up to the paused moment,<br/>materials ticked as done; a subject<br/>chat reads only that subject"]:::server
  WHERE -->|"Malai chat in no subject"| GQ["General-knowledge quiz on<br/>the topic of the chat, no sources"]:::ai
  READ --> BAL["Mix: mostly what the teacher said,<br/>some of what was on screen;<br/>a new mix for every new quiz"]:::server
  BAL --> GEN["3. One AI call writes the whole quiz<br/>(Gemini, Nemotron as backup)"]:::ai
  GEN --> VAL{"Does each question point to a<br/>real piece of the lesson?"}:::check
  VAL -->|"no"| DROP["That question is thrown away"]:::stop
  VAL -->|"yes"| UI["4. Quiz appears;<br/>answers stay hidden"]:::person
  GQ --> UI
  UI --> T{"Learner answers"}:::person
  T -->|"multiple choice or number"| LOCAL["Checked instantly in the browser,<br/>no AI call"]:::good
  T -->|"written"| GR["AI marks it from 0 to 1 against<br/>the key points (keyword check<br/>if the AI is unavailable)"]:::ai
  LOCAL --> REV["5. Shows the right answer, why,<br/>and, for a lesson quiz, a link to the<br/>exact video moment or PDF page"]:::good
  GR --> REV
  UI -.-> NOTE[("Quiz started and final score<br/>written into the chat by the server")]:::store
  REV -.-> NOTE

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

- **Same gates as chat.** The quiz never sees past the playhead, never reads a
  material outside the grant, and a material counts as readable in full only
  when the learner marked it done.
- **Every question is cited.** A question whose `chunk_id` was not among the
  passages sent is dropped, so the source link on each answer is always real.
- **Why the in-order read.** A similarity search for "key concepts, formulas"
  matched mostly on-screen frame descriptions, so quizzes ignored what the
  lecturer said. Reading the watched range in order and balancing speech
  against screen keeps theory questions in the mix.
- **Quizzes from any chat.** The Quiz tab is a form: 1 to 20 questions, the
  question types and optional instructions (a topic, a difficulty). A
  plain-language request in a chat ("quiz me on…") or the `/quiz` slash command
  opens it too. A Malai chat in a subject gets a quiz on that subject's
  materials (`scope_resource_ids`, intersected with the grant); a chat in no
  subject gets a general-knowledge quiz with no citations. The quiz start and
  the final score are written into the chat by the server (`POST /quiz/note`),
  from structured fields rather than client text.

## (e) Lesson page: viewers, cited passages, chats

The lesson page shows the open material beside the tutor. Clicking a citation
shows exactly what it cites.

It is two dockable windows (`components/LessonView.tsx`): the lesson (the
player plus a Chapters / Transcript / Resources panel) and Malai. Malai docks to
any edge by dragging its grip, or with the side-by-side/stacked and swap
buttons on a floating toolbar in the bottom-left corner. The divider resizes,
the panel collapses, and "Hide video" gives Malai the whole space. The layout is
remembered per browser. The Transcript tab is UI only for now.

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart TD
  subgraph LESSON["Lesson window"]
    PL["Video player or PDF viewer"]:::person
    CHP["Chapters tab<br/>named parts of the material"]:::person
    RES["Resources tab<br/>tick a material as done"]:::person
  end

  subgraph MALAI["Malai window"]
    CH["Chat<br/>several separate chats;<br/>type / for commands"]:::person
    QZ["Quiz<br/>opened from the switch,<br/>/quiz or &quot;quiz me&quot;"]:::person
  end

  TB["Floating toolbar: show or hide<br/>each window, side by side or<br/>stacked, swap; or drag Malai<br/>to any edge"]:::person -.-> LESSON
  TB -.-> MALAI

  CHP -->|"click a chapter"| PL
  RES -->|"done materials can be<br/>read in full"| API["Our server<br/>never goes beyond what<br/>this learner was given"]:::server
  CH --> API
  QZ --> API
  API --> CIT["Answer or quiz question<br/>with its sources"]:::good
  CIT -->|"click a video source"| SEEK["Video jumps to that moment;<br/>the quoted span is marked<br/>on the timeline"]:::person
  CIT -->|"click a PDF source"| HL["PDF opens on that page with<br/>the quoted sentence highlighted"]:::person

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

- **Several chats per session.** Each chat (`thread_id`) has its own history;
  the original one is `main`. The chat service itself is unaware of chats: the
  router sets the current chat per request and the message store keys history by
  it.
- **Open material plus what is marked done.** A chat reads the open material up
  to the playhead, plus every material ticked done in the course checklist (read
  in full), and nothing else from the course. The done list is intersected with
  the session grant, so it can never reach a material the session was not given.
- **Cited passages.** Each citation carries the passage text. The PDF viewer
  finds it on the cited page by comparing letters and digits only, so line
  breaks, hyphenation and ligatures do not stop the match (93 of the UNIT-1
  PDF's 97 chunks match in full, the rest partly, none missed).
- **Slash commands.** Every chat (lesson and Malai) has `/explain`,
  `/analogy`, `/summarize`, `/compare` and `/quiz`. Typing `/` opens a menu, and
  each command has its options. They are worded for, and scoped to, what that
  chat can read: a video, a document, a subject, or nothing.

### The rest of the web app

- **Dashboard** (`/dashboard`): a year of study activity (streak, study days,
  time spent), a Malai entry card, continue learning, the next practice quiz
  and the most recent subjects.
- **My subjects** (`/ai-tutor/subjects`): search, Ongoing / Completed /
  Archived filters, sorting and a grid/list toggle. A resource opened from here
  shows without the chat, with an "Open with Malai" button.
- **Malai** (`/malai`): a standalone tutor chat with its own sidebar: each
  subject with its chats, then recent chats in no subject. Chats can be
  searched, renamed, moved into or out of a subject, and deleted; the sidebar
  collapses to a rail. A subject's page (`/malai?s=<id>`) lists its chats and
  resources and starts a chat filed under it. A resource opens beside Malai at
  `/malai/lesson/<subject>/<resource>` in the same dockable layout.
- Activity, progress and archiving are recorded in the browser
  (`lib/activity.ts`), not on the server.

## (f) Developer view: the live pipeline trace

A floating **Developer view** button (in the top bar on small screens) opens `/dev` in its own window.
It shows, live and animated, each step the server takes for an upload, a
question or a quiz: where the step ran, how long it took, and the data it
carried.

```mermaid
%%{init: {"theme": "base", "fontFamily": "Arial, Helvetica, sans-serif", "themeVariables": {"fontSize": "16px", "fontFamily": "Arial, Helvetica, sans-serif", "lineColor": "#64748b", "primaryTextColor": "#0f172a"}, "flowchart": {"curve": "basis", "padding": 14, "wrappingWidth": 340}}}%%
flowchart LR
  subgraph APP["While the app is used"]
    U["Teacher uploads, or learner<br/>asks a question or a quiz"]:::person
    S["Each step on the server notes<br/>what it did, where it ran,<br/>how long it took, and the data"]:::server
  end
  M[("Recent steps kept in<br/>the server's memory")]:::store
  subgraph DEV["Developer view (separate window)"]
    P["Checks for new steps<br/>a few times a second"]:::person
    DIA["Animated diagram: a dot travels<br/>between steps, each step lights up,<br/>then gets a tick and its time"]:::good
    INS["Inspector: the real data, e.g. the<br/>pieces found, the spoiler check,<br/>and which sources were confirmed"]:::good
  end
  U --> S --> M --> P
  P --> DIA
  P --> INS

  classDef person fill:#e0f2fe,stroke:#0284c7,color:#0c4a6e,stroke-width:1.5px
  classDef server fill:#ede9fe,stroke:#7c3aed,color:#2e1065,stroke-width:1.5px
  classDef ai fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1.5px
  classDef store fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1.5px
  classDef check fill:#ffffff,stroke:#dc2626,color:#7f1d1d,stroke-width:2px
  classDef good fill:#d1fae5,stroke:#059669,color:#064e3b,stroke-width:2px
  classDef stop fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-width:1.5px
```

- **What is recorded.** `app/devtrace.py` records one event per step: the
  ingest stages (read, transcribe, describe frames, chunk, embed, store), and
  for a question the session check, the agent-or-classic choice, the query, the
  embedding, the vector search, the strict-T gate (with every passage kept and
  its time range), the model call, citation validation and the reply. Events
  carry ids, times, counts and short text previews, never keys or full prompts.
- **How it gets to the page.** Events sit in a bounded in-memory ring buffer
  (4,000 events) on the API. `GET /dev/trace?after=<seq>` returns the new ones;
  the page polls it every 0.8 s and plays them in order, so a fast answer is
  still readable step by step. **Replay** re-animates any past run.
- **The diagram.** Steps sit in rows by where they run (browser, server, AI
  models, vector database). Lines leave and enter boxes at their sides and are
  routed around every other box. Steps can be dragged within their row, and the
  layout is saved per browser. There are dark and light themes.
- **Recording never changes behaviour.** No trace call can raise into the
  pipeline it observes, and the gate it reports is the same gate that ran
  (`tests/test_devtrace.py`).
- **Limits.** Anyone who can reach the API can read the trace, and a restart
  clears it. `DEV_TRACE=false` turns recording off. See
  [FUTURE_SCOPE.md](FUTURE_SCOPE.md).

## Safety invariants

These are product properties, and the tests pin them. They are not tuning
knobs.

| # | Invariant | Why |
|---|---|---|
| 1 | **Strict timestamp gate: keep a video passage only when `end_ms <= T`.** Never `start_ms <= T`. A missing `end_ms` excludes the passage. The rule is enforced inside the vector query and re-checked in Python. | A passage that *starts* before `T` can still *end* after it, and its tail is a spoiler. The evaluation on our own course measures 0 spoilers in 29 questions. |
| 2 | **Citations are validated against the retrieved set.** Uncited answers and unknown ids are refused. | A model can cite a plausible-looking id that was never retrieved. Passing that through presents an ungrounded answer as grounded. |
| 3 | **Scope is intersected, never replaced.** The focused video narrows the granted `resource_scope`; it cannot widen it. Materials marked done are intersected with the grant the same way, and a quiz question citing anything outside the passages sent is dropped. | An earlier version replaced the scope with the focused id, which let a client read outside its grant. |
| 4 | **Plausible-looking ids are not real ids.** Subjects refuse to attach a resource id that the index cannot vouch for. | A scope such as `["res_video"]` once matched no indexed chunk and silently refused every question. |
| 5 | **Timestamps come from signals, not from the model.** ASR segment times and frame-index arithmetic set the times; a live frame sits at exactly `T`. | A model that labels a frame from 650 s as "index 5" would otherwise store it at 20 s, which is a spoiler leak. |
| 6 | **Signed launches in production.** `LMS_LAUNCH_SECRET=dev-secret` with `ALLOW_UNSIGNED_LAUNCH=true` is for a localhost demo only. Hosted, the Vercel server route signs launches and the API runs with `ALLOW_UNSIGNED_LAUNCH=false`. | The launch carries the learner, the course and the allowed resources. Unsigned, anyone could grant themselves any scope. |
| 7 | **Secrets stay out of the repo and the browser.** `apps/api/.env` is git-ignored. The web app's launch secret is server-only (no `NEXT_PUBLIC_` prefix). | |
| 8 | **Model HTML stays inert.** No `rehype-raw`, and KaTeX runs with `trust: false`. `npm run contract-check` fails if either is broken. | Answers are model output rendered in the learner's browser. |
