# Multi-service (Node + Python) image for Railway (Web service)
FROM node:20-slim

# Use bash for all RUN steps (we rely on [[ ... ]] and { ...; } blocks)
SHELL ["/bin/bash", "-lc"]

# --- System deps ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    gcc g++ make \
    libpq-dev \
    curl ca-certificates bash \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Node deps (copy package.json and optionally the lockfile) ---
COPY package.json package-lock.json* ./

# Prefer ci when lock is valid; fall back to install if out-of-sync
RUN if [[ -f package-lock.json ]]; then \
      echo "Using npm ci (lock present)"; \
      npm ci --omit=dev || npm ci --omit=dev --legacy-peer-deps || { \
        echo "Falling back to npm install (removing lock)"; \
        rm -f package-lock.json; \
        npm install --omit=dev --legacy-peer-deps; \
      }; \
    else \
      echo "No lock file; using npm install"; \
      npm install --omit=dev --legacy-peer-deps; \
    fi

# --- Python deps ---
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# --- App code ---
COPY . .

RUN mkdir -p sessions

ENV NODE_ENV=production \
    PYTHONUNBUFFERED=1 \
    FLASK_HOST=0.0.0.0 \
    PORT=8080 \
    PY_ENTRY=main.py \
    NODE_ENTRY=server.js

# Expose for docs
EXPOSE 8080
EXPOSE 3001

# Healthcheck: both Flask ($PORT) and Node (3001) must be up
HEALTHCHECK --interval=30s --timeout=15s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" && curl -fsS "http://127.0.0.1:3001/health" || exit 1

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
