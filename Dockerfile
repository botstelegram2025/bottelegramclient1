# Multi-stage build for Node.js and Python app (corrigido)
FROM node:20-slim AS node-base

ENV DEBIAN_FRONTEND=noninteractive
ENV NODE_ENV=production

# Instala pacotes de sistema necessários (inclui python3-venv)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    openssh-client \
    python3 \
    python3-pip \
    python3-venv \
    python3-dev \
    gcc \
    g++ \
    make \
  && rm -rf /var/lib/apt/lists/*

# Garante que qualquer URL git:// seja tratada como https://
RUN git config --global url."https://github.com/".insteadOf git://github.com/

WORKDIR /app

# Copia apenas os arquivos de dependências Node primeiro (melhora cache)
COPY package*.json ./

# Instala dependências Node (npm ci quando há lockfile)
RUN if [ -f package-lock.json ]; then \
      npm ci --omit=dev; \
    else \
      npm install --omit=dev; \
    fi

# Copia requirements Python
COPY requirements.txt ./

# Cria virtualenv, atualiza pip e instala dependências Python no venv
RUN if [ -f requirements.txt ]; then \
      python3 -m venv /opt/venv && \
      /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
      /opt/venv/bin/pip install --no-cache-dir -r requirements.txt ; \
    fi

# Copia o restante da aplicação
COPY . .

# Ajusta PATH para usar binários do venv por padrão
ENV PATH="/opt/venv/bin:${PATH}"
ENV PYTHONUNBUFFERED=1
ENV PORT=5000

# Garante diretório de sessões (se usado pelo app)
RUN mkdir -p sessions

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD curl -fsS http://localhost:5000/health || exit 1

# Use o python do PATH (será o do venv)
CMD ["python3", "start_railway.py"]
