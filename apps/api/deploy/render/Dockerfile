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
# Demo content: the files of the seeded resources, and the subject that lists
# them. Their chunks are already in Pinecone, so a restart re-indexes nothing.
COPY test-data/Partial-derivatives.mp4 ./data/assets/uploads/res_2e629d682a3d.mp4
COPY test-data/UNIT-1-001.pdf ./data/assets/uploads/res_7c1f4a92d0e5.pdf
COPY apps/api/deploy/seed/subjects.json ./data/subjects.json
# Their chapters, written once against those same indexed chunks, so the
# chapter panel is ready at once and no restart spends LLM calls on them.
COPY apps/api/deploy/seed/chapters/ ./data/chapters/
RUN chown -R appuser:appuser /srv/app

USER appuser
ENV PORT=10000
EXPOSE 10000

# One process: ingest jobs, the in-flight guard and sessions live in memory.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
