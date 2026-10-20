# Hugging Face Spaces image for the API. Staged next to app/ by
# scripts/deploy_space.py; not used for local dev (see ../../Dockerfile).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    HOME=/home/user

# Spaces run the container as uid 1000; give that user the app directory so
# Chroma, uploads and the session/subject mirrors under data/ are writable.
RUN useradd -m -u 1000 user
WORKDIR /home/user/app

COPY --chown=user requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=user app ./app
COPY --chown=user migrations ./migrations
# Demo content (index + media) baked in so the hosted demo works from first
# boot. Empty when the deploy is run without --with-data.
COPY --chown=user data ./data

USER user
EXPOSE 7860

# One process: ingest jobs, the in-flight guard and sessions live in memory.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]
