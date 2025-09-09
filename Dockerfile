# Multi-service (Node + Python) image for Railway (Web service)
FROM node:20-slim

# --- System deps ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-dev \
    gcc g++ make \
    libpq-dev \
    curl ca-certificates bash \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Node deps ---
COPY package*.json ./
RUN if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi

# --- Python deps ---
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# --- App code ---
COPY . .

# Ensure sessions dir exists (used by Baileys or your app)
RUN mkdir -p sessions

# --- Environment ---
ENV NODE_ENV=production \
    PYTHONUNBUFFERED=1 \
    FLASK_HOST=0.0.0.0

# Railway injects $PORT at runtime. Default to 8080 for local runs.
ENV PORT=8080

# --- Expose (docs only) ---
EXPOSE 8080
EXPOSE 3001

# --- Healthcheck: require BOTH Flask (on $PORT) and Node (3001) ---
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
  CMD bash -lc 'curl -fsS http://127.0.0.1:${PORT}/health && curl -fsS http://127.0.0.1:3001/health || exit 1'

# --- Entrypoint boots both Node and Python ---
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
