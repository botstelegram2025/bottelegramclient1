# Multi-service (Node + Python) image for Railway (Web service)
FROM node:20-slim

# Usar bash nos RUN (permite blocos e [[ ... ]])
SHELL ["/bin/bash", "-lc"]

# --- System deps ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    gcc g++ make \
    libpq-dev \
    curl ca-certificates bash \
    git openssh-client \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# --- Node deps (copia package.json e lock se existir) ---
COPY package.json package-lock.json* ./

# Evitar prompts de git/ssh em deps via Git
RUN git config --global url."https://github.com/".insteadOf git@github.com: || true \
 && git config --global --add safe.directory /app || true \
 && export GIT_ASKPASS=/bin/true

# Preferir ci quando lock está válido; fallback para install se fora de sincronia
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

# --- Python venv para evitar PEP 668 (externally-managed-environment) ---
# --- Python venv para evitar PEP 668 (externally-managed) ---
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/python -m ensurepip --upgrade || true \
 && /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# --- Python deps ---
COPY requirements.txt ./
RUN /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# --- Código da aplicação ---
COPY . .

# Diretório usado por Baileys/sessões, se aplicável
RUN mkdir -p sessions

# --- Environment ---
ENV NODE_ENV=production \
    PYTHONUNBUFFERED=1 \
    FLASK_HOST=0.0.0.0 \
    PORT=8080 \
    PY_ENTRY=main.py \
    NODE_ENTRY=server.js

# Expose (documentação)
EXPOSE 8080
EXPOSE 3001

# Healthcheck: Flask ($PORT)/health e Node (3001)/health devem responder
HEALTHCHECK --interval=30s --timeout=15s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/health" && curl -fsS "http://127.0.0.1:3001/health" || exit 1

# Entrypoint que sobe Node (em background) + Python (foreground)
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

CMD ["/app/entrypoint.sh"]
