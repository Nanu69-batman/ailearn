"""Deploy the API to a Hugging Face Space (Docker SDK).

Stages ``app/``, ``migrations/``, ``requirements.txt`` and the Space's
``Dockerfile``/``README.md`` (from ``deploy/space/``) into a temp folder and
uploads it with ``huggingface_hub`` (large files are handled without git-lfs).

    set HF_TOKEN=hf_...            (a write token; never committed)
    python scripts/deploy_space.py --space <user>/<space-name> [--with-data]

``--with-data`` bakes the local demo content into the image: the Chroma index
under ``data/chroma``, ``data/subjects.json`` and the uploaded media of every
resource a subject references. Chat sessions are never shipped.

Secrets (LLM_API_KEY, EMBEDDING_API_KEY, GROQ_API_KEY, LMS_LAUNCH_SECRET, ...)
are set in the Space's settings, never uploaded: ``.env`` is not staged.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

API = Path(__file__).resolve().parent.parent
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".env", "*.log")


def stage(dest: Path, *, with_data: bool) -> list[str]:
    shutil.copytree(API / "app", dest / "app", ignore=IGNORE)
    shutil.copytree(API / "migrations", dest / "migrations", ignore=IGNORE)
    shutil.copy2(API / "requirements.txt", dest / "requirements.txt")
    shutil.copy2(API / "deploy" / "space" / "Dockerfile", dest / "Dockerfile")
    shutil.copy2(API / "deploy" / "space" / "README.md", dest / "README.md")
    data = dest / "data"
    data.mkdir()
    (data / ".keep").write_text("")
    shipped: list[str] = []
    if not with_data:
        return shipped
    src = API / "data"
    shutil.copytree(src / "chroma", data / "chroma")
    shutil.copy2(src / "subjects.json", data / "subjects.json")
    subjects = json.loads((src / "subjects.json").read_text(encoding="utf-8"))
    wanted = {
        rid for s in subjects.get("subjects", {}).values() for rid in s.get("resource_ids", [])
    }
    uploads = data / "assets" / "uploads"
    uploads.mkdir(parents=True)
    for f in sorted((src / "assets" / "uploads").iterdir()):
        if f.is_file() and f.stem in wanted:
            shutil.copy2(f, uploads / f.name)
            shipped.append(f.name)
    return shipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--space", required=True, help="<user>/<space-name>")
    parser.add_argument("--with-data", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="stage only, print the folder")
    args = parser.parse_args()

    token = os.environ.get("HF_TOKEN")
    if not token and not args.dry_run:
        print("HF_TOKEN is not set (needs a write token)", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="space_"))
    shipped = stage(tmp, with_data=args.with_data)
    print(f"staged {tmp} ({len(shipped)} media file(s): {', '.join(shipped) or '-'})")
    if args.dry_run:
        return 0

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(args.space, repo_type="space", space_sdk="docker", exist_ok=True)
    api.upload_folder(
        repo_id=args.space,
        repo_type="space",
        folder_path=str(tmp),
        commit_message="deploy API",
        # Replace the previous deploy entirely so deleted files do not linger.
        delete_patterns=["*", "**/*"],
    )
    print(f"pushed: https://huggingface.co/spaces/{args.space}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
