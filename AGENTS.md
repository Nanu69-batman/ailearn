# AGENTS.md — Malai Pioneer / AI Learning Assistant

Binding operating instructions for any agent working in this repo.
These rules are **always in effect** and override competing defaults.

---

## Rule 1 — Orchestrator-only main agent

The main agent coordinates. It does not implement.

**Delegate everything:** all code edits, refactors, debugging, test authoring,
test runs, and builds go to subagents.

**The main agent retains only:**
- Task decomposition and prioritisation
- Subagent dispatch with self-contained briefs
- Final verification (HTTP probes, `git status`, brief test re-run after a change)
- Status reporting

**Self-check before any action:** *"Is this implementation work?"* If yes → delegate.
A main agent that writes code is operating incorrectly, regardless of how small
the change is.

---

## Rule 2 — Mandatory usage limits on subagents

No subagent is dispatched without these bounds. Include them in the brief itself.

| Bound | Default | Rationale |
|---|---|---|
| Scope | One subsystem, 1–3 files | Fresh-context agents cost per token read |
| Read budget | ~10 files max before writing | Stops exploratory loops |
| Blocker rule | "Unavailable >3 min → STOP and report" | Prevents silent spinning |
| Verification | Exact commands + expected output | Removes ambiguity |
| Return format | Named fields, no prose | Cheapest parse, smallest context cost |
| Dispatch | `background: true` when >2 min | Keeps main loop responsive |

A subagent that ignores the return format is re-prompted **once**, then abandoned.

---

## Rule 3 — Keep main context clean

Subagents get fresh context; they do not inherit the conversation. Consequences:

1. **Every brief is self-contained.** File paths, the bug, constraints, and exact
   verification commands go in the prompt. Never write "as discussed above."
2. **Never paste file contents into a brief.** Give paths.
3. **Only final responses enter main context.** Enforce tight return formats.
4. **Never spawn a status-report subagent.** Three lines do not justify a context.
5. **Never re-dispatch a task that failed for an environmental reason** (see Rule 4).
6. Prefer parallel dispatch of independent tasks in a single message.

---

## Rule 4 — Browser verification stays with the main agent

Subagent browser tools are **disconnected** in this environment. This has already
cost two subagents their entire budget.

- Assign subagents **code fixes verified by commands they can run** (pytest, ruff,
  tsc, HTTP probes).
- The main agent performs the final browser check itself, briefly.
- On `browser.disconnected`: accept it immediately. Do not re-dispatch to another
  subagent; restructure the task around command-line verification.

---

## Rule 5 — Efficiency

- One concern per subagent. Never bundle unrelated fixes into one brief.
- Review diffs, not whole files or whole repos.
- Do not re-run a green suite unless a file changed after it last ran.
- Prefer a single `execute` + `fetch` probe over multi-line shell pipelines.
- Re-scope or stop a subagent that has produced nothing after ~15 minutes.
- Batch independent reads/writes; parallelise independent subagents.

---

## Rule 6 — Project safety invariants

These are product properties, not preferences. Do not weaken them to make a test pass.

1. **Strict timestamp gate.** Retrieval must keep video passages only when
   `end_ms <= T`. Never relax to `start_ms`. An eval harness previously measured a
   48% production-gap rate with a loose gate.
2. **Citations must be validated** against the retrieved set. Uncited or unknown-ID
   answers are refused, not passed through.
3. **Plausible-looking IDs are not real IDs.** A scope of `["res_video", ...]`
   silently produced refusals on every query. When touching scope or resource IDs,
   verify they correspond to actually-indexed chunks.
4. **Secrets.** `apps/api/.env` holds Gemini keys and the launch secret. Never
   commit, never echo, never read it into a brief.
5. **`ALLOW_UNSIGNED_LAUNCH=true` and `LMS_LAUNCH_SECRET=dev-secret` are
   localhost-demo only.** Production requires real HMAC-signed launches.

---

## Rule 7 — Definition of done

Work is done when:

1. A subagent implemented it against a stated acceptance criterion
2. Named verification commands pass (tests, lint, types, build)
3. `git status` shows only intended files changed
4. No secrets or unrelated churn in the diff
5. Final behaviour confirmed by a probe (HTTP or brief browser check)

If a criterion cannot be verified, say so explicitly rather than implying success.

---

## Repo map

```
apps/api/          FastAPI backend — chat, ingestion, retrieval, eval, cache
apps/api/tests/    334 tests collected, must stay green
```

Verification notes: check the suite **collects** (no collection errors), not just
that it passes. Use the interpreter
`C:\Users\Nimish-2\AppData\Local\Programs\Python\Python314\python.exe`
(PATH `python` is 3.13 and lacks pytest/uvicorn).

```text
apps/web/          Next.js 15 + Tailwind v3 frontend
docs/HANDOFF.md    Current state, next task, known issues  ← read first
test-data/         Course files used for ingestion
```

Start every session by reading `docs/HANDOFF.md`.