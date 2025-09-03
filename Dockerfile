# ---------- Stage 1: build deps do WhatsApp (Node) ----------
FROM node:20-alpine AS whatsapp-deps
WORKDIR /app

# Dependências de sistema p/ pacotes que usam repositórios git
RUN apk add --no-cache git

# Copia manifestos Node (esperado na raiz do repo)
# Se o seu package.json estiver em uma subpasta (ex.: whatsapp/), mude as linhas abaixo.
COPY package*.json ./

# Instala com lock, senão instala normal (sem dev)
RUN if [ -f package.json ]; then \
      if [ -f package-lock.json ]; then \
        npm ci --omit=dev; \
      else \
        npm install --omit=dev; \
      fi; \
    else \
      echo "⚠️  Nenhum package.json encontrado; pulando instalação do WhatsApp"; \
    fi

# ---------- Stage 2: app Python principal ----------
FROM python:3.11-slim AS main
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Sao_Paulo

# deps de sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc g++ libpq-dev curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Instala Node no container final (vamos rodar o server WhatsApp aqui)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs \
 && node -v && npm -v

# Diretório da app
WORKDIR /app

# Copia requisitos Python e instala (sem -e .)
COPY requirements.txt* ./ 
RUN if [ -f requirements.txt ]; then \
      pip install --no-cache-dir -r requirements.txt; \
    else \
      echo "⚠️  Nenhum requirements.txt encontrado; pulando pip install"; \
    fi

# Copia código da aplicação (bot + whatsapp_baileys_multi.js)
COPY . .

# Copia node_modules do stage anterior para /app/node_modules
# (Node resolve módulos subindo diretórios, então funciona com o script na raiz ou em subpastas)
COPY --from=whatsapp-deps /app/node_modules /app/node_modules

# Ajusta permissões e converte EOL do start.sh (evita erro de sintaxe)
RUN sed -i 's/\r$//' /app/start.sh && chmod +x /app/start.sh

# Porta do WhatsApp server (in-process)
ENV PORT=3001
EXPOSE 3001

# Healthcheck do WhatsApp local (não falha o container se não subir; só para observabilidade)
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD curl -fsS http://127.0.0.1:3001/health || exit 1

# Entry
CMD ["/app/start.sh"]
