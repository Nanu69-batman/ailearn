"""One-time cleanup: dedupe duplicate resource rows in ingest_state.json.

Old rows (pre-idempotency-fix) created multiple resources with the same
(course_id, asset_name). Keep the first (oldest) id per pair, drop the rest
from ``resources``/``resources_by_course``, drop jobs pointing at removed
resource_ids, and purge their chunks from the store snapshot.

Usage:
  python scripts/cleanup_ingest_state.py                  # auto-dedupe (keep oldest)
  python scripts/cleanup_ingest_state.py --drop <id> ...   # drop specific ids

``--drop`` is required when the survivor is NOT the oldest row: a re-ingest
that already produced a corrected/newer resource must win over the stale one,
which auto-dedupe (keep-first) would get backwards.
"""

from __future__ import annotations

import argparse
import json
import pickle
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA = Path("data")
STATE = DATA / "ingest_state.json"
SNAPSHOT = DATA / "store_snapshot.pkl"


def _backup(path: Path) -> Path | None:
    """Copy a data file to ``<name>.bak`` before it is mutated."""
    if not path.is_file():
        return None
    bak = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, bak)
    return bak


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drop",
        action="append",
        default=[],
        metavar="RESOURCE_ID",
        help="drop this exact resource id (repeatable); skips auto-dedupe",
    )
    args = parser.parse_args()

    data = json.loads(STATE.read_text(encoding="utf-8"))

    keep_ids: set[str] = set()
    removed_ids: set[str] = set()

    if args.drop:
        # Explicit mode: honour the caller's choice of survivor.
        removed_ids = {rid for rid in args.drop if rid in data.get("resources", {})}
        keep_ids = set(data.get("resources", {})) - removed_ids
    else:
        seen: dict[tuple[str, str | None], str] = {}
        for rid, res in list(data.get("resources", {}).items()):
            key = (res.get("course_id"), res.get("asset_name") or res.get("title"))
            if key in seen:
                removed_ids.add(rid)
            else:
                seen[key] = rid
                keep_ids.add(rid)

    for rid in removed_ids:
        del data["resources"][rid]

    for course_id, ids in list(data.get("resources_by_course", {}).items()):
        data["resources_by_course"][course_id] = [
            rid for rid in ids if rid in keep_ids
        ]

    removed_jobs = [
        jid
        for jid, job in data.get("jobs", {}).items()
        if job.get("resource_id") in removed_ids
    ]
    for jid in removed_jobs:
        del data["jobs"][jid]

    for path in (STATE, SNAPSHOT):
        bak = _backup(path)
        if bak:
            print(f"backed up {path} -> {bak}")

    STATE.write_text(json.dumps(data), encoding="utf-8")
    print(
        f"ingest_state: removed {len(removed_ids)} duplicate resources "
        f"{sorted(removed_ids)} and {len(removed_jobs)} jobs {removed_jobs}"
    )

    if SNAPSHOT.is_file() and removed_ids:
        with SNAPSHOT.open("rb") as fh:
            chunks = pickle.load(fh)
        kept = [c for c in chunks if getattr(c, "resource_id", None) not in removed_ids]
        dropped = len(chunks) - len(kept)
        if dropped:
            with SNAPSHOT.open("wb") as fh:
                pickle.dump(kept, fh)
        print(f"store_snapshot: dropped {dropped} chunks, kept {len(kept)}")

    print(f"remaining resources: {len(data['resources'])}")


if __name__ == "__main__":
    main()
