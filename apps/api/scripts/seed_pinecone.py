"""Copy indexed resources from the local Chroma store into Pinecone.

Vectors are copied as they are, so seeding spends no embedding requests.
Creates the index (serverless, cosine, the local vectors' width) if missing.

    python scripts/seed_pinecone.py res_2e629d682a3d [more resource ids...]

Reads PINECONE_API_KEY / PINECONE_INDEX from the app settings (apps/api/.env).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.retrieval.pinecone_store import PineconeCollection
from app.retrieval.registry import default_chroma_path


def main(resource_ids: list[str]) -> int:
    if not resource_ids:
        print("usage: seed_pinecone.py <resource_id> [...]", file=sys.stderr)
        return 2
    if not settings.pinecone_api_key:
        print("PINECONE_API_KEY is not set in apps/api/.env", file=sys.stderr)
        return 2

    import chromadb
    from pinecone import Pinecone, ServerlessSpec

    col = chromadb.PersistentClient(path=str(default_chroma_path())).get_collection("chunks")
    pc = Pinecone(api_key=settings.pinecone_api_key)
    name = settings.pinecone_index

    for rid in resource_ids:
        got = col.get(
            where={"resource_id": {"$eq": rid}},
            include=["embeddings", "metadatas", "documents"],
        )
        n = len(got["ids"])
        if not n:
            print(f"{rid}: no chunks in the local index, skipped")
            continue
        dim = len(got["embeddings"][0])
        if not pc.has_index(name):
            pc.create_index(
                name=name, dimension=dim, metric="cosine",
                spec=ServerlessSpec(cloud="aws", region="us-east-1"),
            )
            while not pc.describe_index(name).status["ready"]:
                time.sleep(2)
            print(f"created index {name} (dim={dim}, cosine)")
        target = PineconeCollection(pc.Index(name), dimension=dim)
        target.delete(where={"resource_id": {"$eq": rid}})  # exact id: re-seed is clean
        target.upsert(
            ids=got["ids"], documents=got["documents"],
            metadatas=got["metadatas"], embeddings=got["embeddings"],
        )
        print(f"{rid}: {n} chunk(s) copied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
