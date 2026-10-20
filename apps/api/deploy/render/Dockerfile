# Render image for the API. Build context is the REPO ROOT (see render.yaml)
# so the demo video tracked in test-data/ can be baked in: Render's free disk
# is wiped on every restart, and the index itself lives in Pinecone.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN useradd -m -u 10001 appuser
WORKDIR /srv/app

COPY apps/api/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY apps/api/app ./app
COPY apps/api/migrations ./migrations
# Demo content: the media file of the one seeded resource, and the subject that
# lists it. Its chunks are in Pinecone (scripts/seed_pinecone.py).
COPY test-data/Partial-derivatives.mp4 ./data/assets/uploads/res_2e629d682a3d.mp4
COPY apps/api/deploy/seed/subjects.json ./data/subjects.json
RUN chown -R appuser:appuser /srv/app

USER appuser
ENV PORT=10000
EXPOSE 10000

# One process: ingest jobs, the in-flight guard and sessions live in memory.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
