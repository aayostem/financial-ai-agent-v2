# ── Stage 1: builder ────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

# If this ever fails with "Hash Sum mismatch" against a hostname that isn't
# deb.debian.org / security.debian.org, that's a network-level captive-portal
# or ISP redirect corrupting HTTP traffic -- fix the network, not this file.
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        build-essential \
        libpq-dev \
        && apt-get clean && \
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

COPY pyproject.toml README.md ./

RUN python -m venv /app/venv && \
    /app/venv/bin/pip install --upgrade pip setuptools wheel && \
    /app/venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu --no-cache-dir && \
    /app/venv/bin/pip install -e "." --no-cache-dir --timeout 1000

COPY src/ ./src/

RUN /app/venv/bin/pip install --no-deps -e "."

# ── Pre-fetch the embedding model, saved to the EXACT flat path the app ────
# ── looks for -- not Hugging Face's internal hub/snapshots cache layout ───
#
# ROOT CAUSE of every "model not found" failure so far: your app's code
# builds a literal path -- {HF_HOME}/models--sentence-transformers--<name> --
# and loads directly from it. But SentenceTransformer('all-MiniLM-L6-v2')
# downloads into HF's *nested* hub cache (hub/models--.../snapshots/<hash>/),
# never into that flat path. The files existed on disk in every previous
# attempt; they were just never where the app was looking.
#
# Fix: download once, then explicitly .save() to the flat path the app
# actually reads from. This also means the app never depends on HF's cache
# internals at runtime, which can change between library versions.
ENV HF_HOME=/app/.cache/huggingface
RUN mkdir -p ${HF_HOME} && \
    /app/venv/bin/python -c "\
from sentence_transformers import SentenceTransformer; \
m = SentenceTransformer('all-MiniLM-L6-v2'); \
m.save('${HF_HOME}/models--sentence-transformers--all-MiniLM-L6-v2')"

# Fail the BUILD, not a container three minutes into production, if the
# save didn't produce a loadable model directory.
RUN test -f ${HF_HOME}/models--sentence-transformers--all-MiniLM-L6-v2/config.json && \
    echo "Model cache verified at build time." || \
    (echo "FATAL: model save did not produce config.json -- build aborted." && exit 1)


# ── Stage 2: production ─────────────────────────────────────────────────────
FROM python:3.11-slim-bookworm AS production

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ca-certificates \
        && apt-get clean && \
        rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

RUN groupadd -r finai && useradd -r -g finai finai && \
    mkdir -p /app/data /app/.cache/huggingface /app/.cache/torch && \
    chown -R finai:finai /app

COPY --from=builder --chown=finai:finai /app/venv /app/venv
COPY --from=builder --chown=finai:finai /app/src /app/src
COPY --from=builder --chown=finai:finai /app/pyproject.toml /app/pyproject.toml
COPY --from=builder --chown=finai:finai /app/.cache /app/.cache

ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="/app/src" \
    TZ="UTC" \
    HF_HOME="/app/.cache/huggingface" \
    TORCH_HOME="/app/.cache/torch" \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    OMP_NUM_THREADS=1 \
    TOKENIZERS_PARALLELISM=false

# Verify again in the final image -- catches any COPY/chown mistake between
# stages before it ever reaches a running container.
RUN test -f /app/.cache/huggingface/models--sentence-transformers--all-MiniLM-L6-v2/config.json && \
    echo "Production image model cache verified." || \
    (echo "FATAL: model directory missing in production stage -- build aborted." && exit 1)

RUN find /app -type f -name "*.pyc" -delete && \
    find /app -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

USER finai

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import requests; requests.get('http://localhost:8000/health', timeout=5)" || exit 1

EXPOSE 8000

CMD ["uvicorn", "financial_ai.api.server:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--loop", "asyncio", \
     "--access-log"]