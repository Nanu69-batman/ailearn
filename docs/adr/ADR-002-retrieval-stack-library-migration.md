# ADR-002: Retrieval Stack — Library Migration to Postgres + pgvector

Status: Proposed
Date: 2026-10-08
Extends: ADR-001 (`docs/adr/ADR-001-lms-integration-scalability.md`) — *extends, does not reopen*
Scope: `apps/api/app/retrieval/**`, ingestion persistence, snapshot format
Nature: **Decision document. No implementation is authorised by this ADR.**

---

## 1. Context

The retrieval stack was hand-built rather than assembled from libraries. Measured
inventory (read 2026-10-08):

| File | Lines | Custom machinery |
|---|---:|---|
| `app/retrieval/store.py` | 359 | in-memory store, manual cosine, hand-rolled `threading.Lock`, snapshot protocol, **plus a `PostgresVectorStore` that already exists** |
| `app/retrieval/hybrid.py` | 164 | RRF fusion, `KeywordIndex`/`Reranker` protocols, `NoOpReranker` |
| `app/retrieval/service.py` | 311 | focus re-rank, `FOCUS_SCORE_FLOOR = 1000.0`, focus rescue query |
| `app/retrieval/filters.py` | 126 | post-retrieval leakage assertion (second gate) |
| **Total** | **960** | |

`apps/api/requirements.txt` has 8 dependencies, none of them a RAG or retrieval
library. It *does* already include `asyncpg>=0.29` and `sqlalchemy>=2`, and
`migrations/0001_initial.sql` already declares `CREATE EXTENSION vector` plus a
`chunks` table with an HNSW index. ADR-001 §6 already committed to
"PostgreSQL + pgvector for the prototype, hidden behind a repository interface".

**So the port was chosen, and partially scaffolded, and then never started.**
This ADR finishes the decision.

### 1.1 Corrections to the framing

Two premises in the task brief do not survive contact with the code. They change
the cost estimate materially, so they are stated up front:

1. **There is no hand-written BM25.** `hybrid.py` defines a `KeywordIndex`
   Protocol (`hybrid.py:44-56`), but grep across `apps/api` finds exactly one
   implementation of `search_text` — a test double at
   `tests/test_retrieval.py:227`. `RetrievalService` defaults `fts=None`
   (`service.py:183`) and every production construction path leaves it `None`.
   Therefore **`reciprocal_rank_fusion` never executes in production today**;
   production retrieval is pure cosine similarity (`hybrid.py:139-140`, the
   `if not fts_hits:` branch). The BM25 decision below is therefore a
   *greenfield* choice costing ~15 lines of SQL, not a ~180-line replacement.
2. **`PostgresVectorStore` is already written and tested-against**
   (`store.py:250-359`), including a pure `build_search_sql()` and the
   `TIMESTAMP_GUARD_CLAUSE` fragment (`store.py:50-53`). Phase 1 of this
   migration is *wiring and data*, not *authoring*.

### 1.2 The cost of staying hand-built, evidenced

From `docs/HANDOFF.md` §2 — every one of these was a real debug cycle, and
**in each case the test suite was fully green while the product was broken**:

- §2.3 silent 3072-vs-1536-dim cosine truncation (`zip` truncating mismatched
  vectors into a plausible, meaningless score);
- §2.2 the store's own `fetch_k` cut starving focused video chunks to ranks 26–40
  of 44 before the focus boost could see them;
- §2.5 `CITATION_RE` unable to parse the marker form the model actually emits;
- §2.7 in-memory sessions producing answers visually identical to real ones.

Green tests were not evidence. That is the architectural tell: a retrieval stack
whose failure modes are invisible to its own test suite has too much untested
mechanism in it.

---

## 2. Decision

### 2.1 Target: Postgres + pgvector, as ADR-001 already decided

Confirmed and unchanged. pgvector is at **0.8.7 (2026-10-01)** per its
`CHANGELOG.md`; HNSW + iterative index scans + `halfvec` are all available. No
new Python dependency is required to use it — `asyncpg` + `sqlalchemy` are
already installed and already imported by `PostgresVectorStore`.

`requirements.txt` gains **nothing**. This is a Postgres extension, not a Python
package.

### 2.2 What dies, what survives

| Custom code | Disposition | Replacement |
|---|---|---|
| `_cosine()` `store.py:93-118` | **Dies** (26 lines) | `<=>` cosine-distance operator |
| `EmbeddingDimensionMismatch` + `_warned_dims` `store.py:57-67` | **Dies** (11 lines) | typed column rejects the write (§3) |
| `_in_scope()` `store.py:121-142` | **Dies as Python**, survives as the SQL fragment it already mirrors | `TIMESTAMP_GUARD_CLAUSE` |
| `InMemoryVectorStore` `store.py:145-247` | **Dies from production** (103 lines); retained **test-only** | `PostgresVectorStore` |
| `threading.Lock` + `snapshot_chunks()` `store.py:159-171` | **Dies** (~30 lines incl. the reasoning comments) | MVCC + `asyncpg` pool |
| `data/store_snapshot.pkl` | **Dies** | table rows; `COPY` for bulk load |
| `reciprocal_rank_fusion()` `hybrid.py:33-41` | **Survives** — 9 lines, and it is the canonical implementation (see §2.4) | — |
| `KeywordIndex` Protocol `hybrid.py:44-56` | **Replaced** by a `tsvector` query | `websearch_to_tsquery` + `ts_rank_cd` |
| `NoOpReranker` `hybrid.py:68-74` | **Survives** | — |
| `FOCUS_SCORE_FLOOR` `service.py:41` | **Dies** (replaced by a declared fusion weight, §2.5) | weighted RRF term |
| focus rescue query `service.py:250-281` | **Dies** (absorbed as a third fused rank list, §2.6) | weighted RRF |
| `assert_no_leakage()` `filters.py:54-126` | **Survives unchanged** — strengthened, see §5 | — |
| `RetrievedPassage`, `to_passage()` `service.py:62-173` | **Survives** | — |
| `RetrievalService.retrieve()` orchestration | **Survives** as a thin adapter | — |

Net: **~200 lines of production mechanism deleted, ~15 lines of FTS added**, and
the pickle file retired. The `VectorStore` Protocol (`store.py:78-91`) is the
seam and stays.

### 2.3 BM25: Postgres `tsvector` + `ts_rank`, not `pg_search`, not `rank-bm25`

Chosen: a `tsvector` column with a GIN index, scored with `ts_rank_cd`, queried
via `websearch_to_tsquery`. Roughly 15 lines of SQL plus a generated column.

Rejected:
- **ParadeDB `pg_search`** — genuinely the most capable option; its docs confirm
  BM25 and, as of `0.25.0`, native vector search inside its own indexes with a
  unified hybrid ranking. Rejected for the MVP because it replaces the pgvector
  HNSW index with its own index, which directly contradicts ADR-001 §6, and
  because it pins a Postgres build/version. Revisit at scale.
- **`rank-bm25` (Python)** — re-implements BM25 in Python, keeping the failure
  mode ADR-001 and this ADR exist to remove. It would also reintroduce a second
  copy of the corpus that can drift from the table.
- **Keeping `None`** — the current state is not "hybrid retrieval", it is cosine
  with a dormant extension point. Fine for a demo; not worth claiming.

**Caution, stated plainly:** Postgres `ts_rank` is *not* BM25. It is a
different ranking function with different length-normalisation behaviour. Any
document claiming `tsvector` gives us BM25 is wrong. What `tsvector` gives us is
*indexed keyword retrieval with no new dependency*, which is what the MVP needs.

### 2.4 RRF fusion: stays in Python, as a reference implementation

pgvector has **no** built-in RRF. Its "Hybrid Search" documentation section
explicitly points readers to a *Python example* (`rrf.py` in `pgvector-python`)
and a cross-encoder example for combining results. There is no SQL-level RRF
primitive to migrate to.

Our `reciprocal_rank_fusion()` is 9 lines and is already that algorithm. It
stays, with one change: it becomes **weighted** (§2.5). Outsourcing this would
mean importing a library to compute `sum(1/(k+rank))`.

**Risk flagged by this ADR:** RRF has never run in production. Activating it
changes the score distribution that `docs/eval/golden.jsonl` was built against.
See R2 in §7.

### 2.5 `FOCUS_SCORE_FLOOR = 1000.0` → a declared fusion weight

This is the most important design change in the document.

The floor is a **magnitude hack**, and it is only safe by coincidence. Per its
own docstring (`service.py:36-40`), the guarantee — focused beats unfocused
outright — holds *because* cosine similarity is bounded in `[-1, 1]`, so `1000.0`
clears the maximum by three orders of magnitude. The docstring concedes the
fragility: it only holds if the score stays "trivially finite".

The moment a real cross-encoder reranker is wired into the existing `Reranker`
hook (`hybrid.py:59-65`) — whose logits are unbounded — a single `1000.0` stops
meaning anything, and focus silently reverts to a weak preference. This is a
landmine that is already armed. It would fail exactly as §2.3 failed: plausibly,
silently.

The replacement is **weighted RRF**, where boost is a dimensionless coefficient
rather than a score-magnitude offset:

```
fused(d) = Σ_i  w_i / (rrf_k + rank_i(d))
```

with `w = {vector: 1.0, keyword: 1.0, focus: FOCUS_WEIGHT}`. Weights are ratios
against a common scale, so `FOCUS_WEIGHT = 1.0` means "the focused resource's
rank list counts as much as another modality", and it stays correct no matter
what units the underlying scores carry. Intra-group ordering is still preserved
(additive term, stable sort), so the property `service.py:88-90` relies on is
unchanged.

Per-source boost is then a **query-time filter made structural**: the focused
resource is retrieved as its own scoped, T-gated rank list and fused as a third
input to `fused()`. It is no longer a constant that must out-race every other
score; it is a term whose weight is the number we actually meant.

Recommended `FOCUS_WEIGHT`: start at **2.0** and tune against the golden set.
Do not tune by feel — record the sweep (the existing
`docs/eval/threshold-note.md` sweep is the precedent, and `DEFAULT_THRESHOLD`
being untuned is called out at `harness.py:15-23` as an existing debt).

### 2.6 The rescue query disappears — for the right reason

`service.py:250-281` issues a second full retrieval scoped to the focused
resource, then unions it in. Its stated cause (`service.py:251-255`) is that
"the store truncates its own `fetch_k` window before returning".

In SQL, that truncation is not a property of the store, it is a property of the
query — and it is fixable *inside* one statement. But the correct fix is not a
second round-trip at all: the focused resource's rank list becomes a third input
to the weighted fusion of §2.5, computed in the same round-trip as a
`UNION ALL` of two scoped sub-selects.

This deletes the hack, but **only after** the starvation bug it papers over is
fixed at the source. That source is §2.2, and it has a Postgres twin that we
would otherwise walk straight into — see §4.2.

`_resolve_focus_resource_ids()` (`service.py:123-157`) is subtler and
**survives in simplified form**: it exists because `ChatRequest.video_id` is an
*external* id while chunks are indexed under an internal `resource_id`. In SQL
that mapping is a `JOIN` on `resources.video_id`, not a Python scan over the
whole chunk list. The scope-intersection safety property is preserved
structurally: the join is constrained to `resources.id IN (:allowed_resource_ids)`.

---

## 3. Embedding dimension mismatch: from detectable to impossible

The bug (`HANDOFF.md` §2.3) was that `_cosine` used `zip`, which truncates a
1536-dim query vector against a 3072-dim chunk vector into a *plausible float
with no meaning*. Today this class of bug is **detected** — `EmbeddingDimensionMismatch`
is raised at `store.py:109-112`, then caught and the chunk skipped
(`store.py:229-244`).

In Postgres this class becomes **impossible rather than merely detectable**:

1. `migrations/0001_initial.sql:35` declares `embedding vector(3072)` — a
   *typed, fixed-width* column. Inserting a 1536-element literal is a **type
   error at write time**, inside the ingestion transaction. It cannot reach a
   table row, therefore it cannot reach a query, therefore it cannot corrupt a
   score. There is no "plausible but meaningless score" state to exist.
2. The failure surfaces at the correct moment — **ingestion**, where the cost is
   a failed job and a log line — rather than at query time, where the cost is
   silently arbitrary ranking.
3. Belt-and-braces, and the belt is a real documented pattern: pgvector's FAQ
   recommends a dimension check constraint,
   `ALTER TABLE chunks ADD CHECK (vector_dims(embedding::vector) = 3072)`.
4. Defence in depth across the embedder-swap boundary: the schema also needs a
   `CHECK (embed_model = <pinned model>)` (or a partial index per model), so a
   future model swap cannot leave old-dimension rows behind — the *actual* root
   cause in §2.3 was a mid-store embedder swap, not a bad vector.

**Consequence to state explicitly:** this protection is inherited from the
`vector(n)` column type. The moment someone "fixes" a dimension error by
loosening the column to untyped `vector` — which pgvector's FAQ documents as a
legitimate pattern for mixed-dimension corpora — the protection is gone. That
loosening is therefore **forbidden** in this schema; note it in the migration
comment.

---

## 4. Two bugs this migration would introduce if done naively

This section exists because the shipped migration file already contains one of
them. Both are silent-recall-loss bugs of exactly the class §2.2 was.

### 4.1 The existing HNSW index cannot be created at 3072 dimensions — **already broken**

`migrations/0001_initial.sql:41`:

```sql
CREATE INDEX idx_chunks_embedding ON chunks USING hnsw (embedding vector_cosine_ops);
```

pgvector's documentation is explicit: for HNSW, `vector` is supported
**"up to 2,000 dimensions"**; `halfvec` is supported up to 4,000; `bit` up to
64,000. `vector(3072)` exceeds the HNSW `vector` limit. The migration as
written is expected to fail at `CREATE INDEX`. **This has never surfaced
because the migration has never been run** — the demo runs
`InMemoryVectorStore` + pickle.

Two fixes, both documented:

```sql
-- preferred: half-precision index + full-precision re-rank
CREATE INDEX idx_chunks_embedding ON chunks
  USING hnsw ((embedding::halfvec(3072)) halfvec_cosine_ops);
```

(`halfvec` HNSW supports 4,000 dims, so 3072 fits. Search against the
`halfvec` expression, then re-rank the shortlist by full `vector` cosine for
recall — both steps are in the pgvector docs.)

Alternative: dimensionality reduction, or `binary_quantize` with re-ranking.
**Recommended: `halfvec(3072)` expression index + full-precision re-rank.**

*VERIFY:* that `halfvec_cosine_ops` accepts a 3072-dim halfvec on the target
Postgres version, and confirm the resulting index is actually used by
`EXPLAIN (ANALYZE, BUFFERS)` rather than silently falling back to a seq scan.

### 4.2 The strict-T filter will starve HNSW results — the §2.2 twin

This is the one that matters most, and it is the reason this ADR must not be
skimmed.

With an approximate index, **filtering is applied after the index scan**. From
pgvector's docs: *"If a condition matches 10% of rows, with HNSW and the default
`hnsw.ef_search` of 40, only 4 rows will match on average."*

The strict-T gate is precisely such a filter, and it is **most selective exactly
when it matters most**: at low `T` in a long video, a large fraction of chunks
have `end_ms > T`. A learner 30 seconds into a 40-minute lecture has almost no
visible transcript chunks — and default `ef_search = 40` would return a
near-empty result set.

That is *the same starvation bug as `HANDOFF.md` §2.2*, wearing a different hat:
the store truncates its candidate window and the interesting candidates are
already gone. Shipping the port without fixing it would reintroduce a bug we
already paid a full debug cycle for, and it would be worst in the demo's
signature scenario (early-`T` video Q&A).

The fix is documented and available since pgvector 0.8.0 — **iterative index
scans**, which keep scanning the index until enough rows survive the filter:

```sql
BEGIN;
SET LOCAL hnsw.iterative_scan = strict_order;
SET LOCAL hnsw.ef_search = 100;
-- scoped SELECT ... WHERE course_id = :course_id AND ... AND <strict-T>
COMMIT;
```

`strict_order` (not `relaxed_order`) because our citation timing depends on
rank ordering; `relaxed_order` + a materialised CTE is the documented way to get
strict ordering back with better recall — relevant only if `strict_order` proves
too slow.

**Required supporting index** — also named in `HANDOFF.md` §3 as missing. Today
only single-column indexes exist (`0001_initial.sql:39-40`). Add:

```sql
CREATE INDEX idx_chunks_course_resource_t ON chunks(course_id, resource_id, end_ms);
```

A multicolumn index on the filter columns lets the planner do exact (fast)
nearest-neighbour search within the scope when it matches a low percentage of
rows — the case a course+learner scope usually is.

*VERIFY:* measured `production_gap_rate` **and** recall at low `T` with and
without `iterative_scan`. Do not ship §4.2 unmeasured; a plausible-looking
result set at low `T` is exactly the failure the whole ADR-001 lineage is about.

---

## 5. What we deliberately keep hand-built

Outsourcing is not the goal. Three things stay, and each has a reason that a
library does not address.

### 5.1 The strict-`T` timestamp gate — keep it, and make it unbypassable

`TIMESTAMP_GUARD_CLAUSE` (`store.py:50-53`) and `assert_no_leakage()`
(`filters.py:54-126`) are **product policy, not retrieval mechanism**. They
encode a measured decision: a loose gate produced a **48% spoiler rate**
(`AGENTS.md` Rule 6.1). No vector database will encode "this learner is at
00:01:52 and must not be told what happens at 00:02:00". That is ours.

The problem is not the gate — it is that `build_search_sql()` is a *string
builder*, and a string builder can be called incorrectly by a future caller who
omits the clause. Three defences, strongest first:

1. **A SQL `VIEW` owns visibility.** Create `visible_chunks(course_id,
   resource_ids, T)` containing the course + scope + permitted + strict-T
   predicate, and make it the *only* supported way to read chunks for
   retrieval. Every retrieval path — vector, FTS, fusion — selects from the
   view. A bypass is then a visible schema violation rather than a missing
   `AND` clause in a diff. The view is the single auditable statement of "what
   this learner may see at this instant".
2. **Keep `assert_no_leakage()` as the second gate.** Store-level filtering plus
   post-retrieval assertion plus a view is three layers for one invariant, and
   the redundancy is correct: `filters.py:1-6` states the intent, and each layer
   has failed independently at some point in this project's history.
3. **A test that asserts no other module selects `FROM chunks`** outside the
   view's definition. Cheap, and it makes the invariant enforced rather than
   documented.

`NULL end_ms` handling deserves its own note: it is **EXCLUDE**, deliberately,
and the SQL makes that intent explicit rather than relying on `UNKNOWN`
three-valued logic (`store.py:46-49`). Keep the explicit
`end_ms IS NOT NULL AND end_ms <= :current_time_ms`. A future "simplification"
that drops the `IS NOT NULL` is a behaviour change disguised as a cleanup.

### 5.2 Citation validation — keep it entirely

`MARKER_RE` / citation validation lives in `app/chat/service.py` and is
untouched by this ADR. It is the defence against hallucinated chunk IDs, and it
was hardened once already (`HANDOFF.md` §2.5) after a regex failed to parse the
marker form Gemini actually emits. It also enforces `AGENTS.md` Rule 6.2: uncited
or unknown-ID answers are **refused**, not passed through.

Nothing in Postgres, pgvector, or any retrieval library does this. Passing
plausible-looking `resource_id`s into the scope filter is precisely the
`HANDOFF.md` §2.1 failure (placeholder scope IDs that match no indexed chunk,
refusing every query). Validation stays.

### 5.3 The session / HMAC launch boundary — keep it entirely

ADR-001 §2 fixes the trust boundary: identity established at session start,
carried server-side or signed, with `LMS_LAUNCH_SECRET` and
`ALLOW_UNSIGNED_LAUNCH` as **localhost-demo-only** (`AGENTS.md` Rule 6.5). The
migration makes this *stronger* — moving sessions to Postgres (ADR-001 §3)
turns the in-memory session loss of `HANDOFF.md` §2.7 into a non-event.

Unchanged by this ADR. Not in scope, not negotiable, not a library's business.

---

## 6. Migration path

Every phase is independently shippable and independently revertible. **Phase 0
and Phase 1 touch no retrieval code at all**, so the running demo cannot break
before the risky part is reached.

| Phase | Change | Ship gate | Rollback |
|---|---|---|---|
| **0. Infrastructure** | Provision Postgres + pgvector ≥ 0.8.0. Fix `0001_initial.sql`: `halfvec(3072)` expression index (§4.1), `idx_chunks_course_resource_t`, `vector_dims` CHECK, `tsvector` column + GIN, `visible_chunks` view (§5.1). | `migrations/0001_initial.sql` applies cleanly on a virgin DB | Drop DB. Zero code risk. |
| **1. Backfill, read-path untouched** | Load `test-data` corpus into `chunks` via `COPY`. `PostgresVectorStore` sits behind the existing `VectorStore` Protocol, unused. | Row counts and `embed_model` match the pickle. `git status` shows no retrieval change. | Drop table. Demo unaffected. |
| **2. Parity, then cut over reads** | Add `pg_enabled` flag → `PostgresVectorStore`. Run **both** stores on one query set; diff retrieved IDs. Cut over, keep `InMemoryVectorStore` as the test double. | ID-set parity ≥ 99% on the golden questions; **lowest-`T` cases verified specifically** (§4.2); `production_gap_rate` still 0. | Flip flag. Minutes, no redeploy. |
| **3. Kill the magnitude hack** | Weighted RRF (§2.5); delete `FOCUS_SCORE_FLOOR` and the rescue query (§2.6). | Re-run golden eval; re-baseline `golden.jsonl` if scores shifted (expected — R2). | `git revert` the single commit. |
| **4. Enable keyword retrieval** | `tsvector` path + first *real* `KeywordIndex` implementation; RRF activates in production for the first time. | `rrf_k` + weights swept and recorded; eval re-run. | `fts=None` restores pure cosine. One-line revert. |
| **5. Retire the pickle** | Delete `data/store_snapshot.pkl`, `InMemoryVectorStore`'s production wiring, `scripts/reindex_snapshot.py`. | Demo green with no pickle on disk. | `git revert`. |

Phases 0–2 are **additive and reversible**; only Phase 3 is a semantic change to
ranking, and it lands behind its own commit. Phases 4–5 are optional
improvements that a demo-day time crunch can simply not start.

---

## 7. Cost, risks

**Effort: ~3.5 engineering days** (Phase 0: 0.5d, Phase 1: 0.5d, Phase 2: 1d,
Phase 3: 1d, Phase 4: 0.5d). Phase 5 is 0.25d if reached. Day 1-2 is
infrastructure with no code risk; the demo is not at risk from this ADR before
day 2.

### Top risks

- **R1 — Low-`T` recall silently collapses (HNSW post-filter starvation, §4.2).**
  *This is the same failure mode as `HANDOFF.md` §2.2 and would be worst in the
  demo's best moment.* Mitigation: mandatory `SET LOCAL hnsw.iterative_scan =
  strict_order`; composite `(course_id, resource_id, end_ms)` index; a dedicated
  golden case at very low `T`; measured A/B on recall before cutover. **Do not
  ship Phase 2 without this A/B.**
- **R2 — Activating RRF for the first time moves every score.** `golden.jsonl`
  was tuned against pure cosine (`harness.py:15-23` already flags untuned
  thresholds as debt). Enabling `tsvector` + RRF changes score magnitudes, which
  can flip `should_refuse` decisions and the documented `FOCUS_SCORE_FLOOR`
  assumption. Mitigation: record the pre-Phase-4 scoreboard, re-run after, and
  diff `refusal_correctness` / `recall` per item rather than accepting a single
  aggregate. Phase 4 is separately revertible.
- **R3 — No Postgres in the demo environment (or a Neon/network hiccup on
  stage).** Mitigation: `PostgresVectorStore` already lazily creates its engine
  (`store.py:259-270`) — importing the module never needs a DB. Keep
  `InMemoryVectorStore` in the tree *permanently* as the test double and as the
  degraded demo mode behind one flag; never delete it in Phase 5. See §8.

### The metric that must not go stale

`production_gap_rate` (`harness.py:288-348`) is currently computed against
`InMemoryVectorStore` + `HashingEmbedder` (`harness.py:282-284`). After the
migration it would still report **0** — while measuring nothing whatsoever about
the real path. A green `production_gap_rate` would become a **false assurance**,
which is worse than no metric, and precisely the "green tests, broken product"
pattern `HANDOFF.md` §1 warns about.

To remain meaningful it must be backed by the real store: run it against
`PostgresVectorStore` when a database is reachable, and make the harness say so
explicitly in its output (`gap_mode=postgres` vs `gap_mode=inmemory`) so nobody
can mistake a fixture-backed 0 for a production-backed 0.

---

## 8. Fallback: minimum-change library path when Postgres is unavailable

### Decision rule

> **If a Postgres instance reachable from the demo environment cannot be
> provisioned and smoke-tested ≥48h before the demo, choose the in-process
> library path and record it here as a temporary state.**

In-process options, in preference order:

1. **Chroma (in-process, `PersistentClient`)** — one dependency, embedded, no
   server. Metadata filtering via `where=` maps *well* onto our scope+T
   predicate, which is the deciding factor: it is the only thing we actually
   need from a vector DB. *VERIFY* the current `where`-filter grammar and
   whether multi-clause `AND` on metadata is supported as required.
2. **Qdrant** — better filter DSL and better hybrid/multivector story, but it is
   a server (or an embedded mode with its own caveats), which reintroduces the
   "second always-on service" that ADR-001 explicitly rejected for the
   prototype. Only if Chroma's filter grammar proves insufficient.

**Regardless of which is chosen, the same three invariants hold**, because they
live in `filters.py` and the `VectorStore` Protocol and not in the backend:
`TIMESTAMP_GUARD_CLAUSE` becomes the backend's `where`/filter expression;
`assert_no_leakage()` remains the second gate; citation validation and the
HMAC boundary are untouched.

**`rank-bm25` is explicitly not part of any fallback.** It keeps the corpus in
Python, keeps it in danger of drifting from the vector store, and buys nothing
that `tsvector`-in-Postgres or a Chroma keyword filter does not.

Note the fallback is a **partial** answer: it fixes §3 (dimension typing — though
Chroma is less strict than `vector(3072)`; VERIFY its behaviour on mismatched
dimensions, and if it is permissive, keep the explicit `vector_dims` CHECK
equivalent in our own code) and it retires the pickle. It does **not** deliver
ADR-001 §3 stateless sessions or §4 background jobs. Those remain open under the
fallback and should be re-stated as known gaps rather than quietly dropped.

---

## 9. VERIFY — confirm before implementing

Do not assert these from memory; check them at implementation time.

**Schema / index**
1. That `halfvec(3072)` is accepted by `halfvec_cosine_ops` on the target
   Postgres version, and that the expression index is used
   (`EXPLAIN (ANALYZE, BUFFERS)`) rather than silently seq-scanned.
2. That `migrations/0001_initial.sql` as written *actually fails* at
   `CREATE INDEX ... vector_cosine_ops` on `vector(3072)` — confirm the failure
   mode before "fixing" it, so the fix is targeted rather than speculative.
3. Exact `halfvec` half-precision recall impact on our 3072-dim Gemini vectors,
   and the full-precision re-rank query shape that restores it.

**pgvector behaviour**
4. `hnsw.iterative_scan = strict_order` semantics and the
   `hnsw.max_scan_tuples` / `hnsw.scan_mem_multiplier` defaults for our table
   size.
5. That `vector(n)` rejects a wrong-width insert with a hard error (not a
   silent truncation) — this is the backbone of §3.
6. `vector_dims()` CHECK-constraint form for `vector(3072)` columns.

**Full-text**
7. `ts_rank_cd` vs `ts_rank` behaviour for our chunk lengths, and whether
   `websearch_to_tsquery` handles our question forms (natural-language learner
   questions) better or worse than `plainto_tsquery`.
8. Whether `tsvector` should be a stored column, a generated column, or a
   expression index — and how re-ingestion updates it.
9. Confirm pgvector still ships **no** native BM25 and **no** native RRF as of
   the version deployed (0.8.7 at time of writing).

**Fusion**
10. `FOCUS_WEIGHT` tuning range and the sweep method, reusing the
    `docs/eval/threshold-note.md` precedent.

**Operational**
11. Whether `asyncpg` is configured for the target instance's minimum/maximum
    pool, and whether PgBouncer in transaction mode is needed (it is not, at
    prototype scale — ADR-001 §46 already scoped that out).
12. Neon/free-tier behaviour: cold-start latency, and whether the demo can
    tolerate a cold connection on first request.

---

## 10. Alternatives considered

- **Keep the in-memory store + pickle for the MVP.** Tempting on a hackathon
  clock, and defensible if the demo environment has no Postgres. Rejected as the
  *default* only because it cannot satisfy ADR-001 §3 (stateless API layer) and
  §4 (background ingestion) at all, and because the entire §1.2 failure history
  is failure *of this component*. Retained as the documented degraded mode
  (§8, R3).
- **A dedicated vector DB now (Qdrant/Weaviate/Chroma).** Rejected for the same
  reason ADR-001 §28 rejected it: a second always-on service without a measured
  need. `asyncpg` + `sqlalchemy` are already installed and the schema is already
  written, so the marginal cost of the intended path is lower than the
  alternative's.
- **Outsource RRF to a library.** Rejected: pgvector has no RRF primitive, and
  the algorithm is 9 lines we already have correct.
- **Outsource the timestamp gate into the store's filter DSL.** Rejected as
  *replacement*, accepted as *implementation*: the DSL expression is the gate,
  but the gate is defined, documented, unit-tested and enforced by our view and
  `assert_no_leakage` — not by whatever the vendor's filter grammar happens to
  mean this quarter.