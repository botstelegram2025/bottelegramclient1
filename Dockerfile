# Dockerfile – Bot Telegram + Servidor WhatsApp no mesmo container
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=America/Sao_Paulo

# Dependências de sistema + Node 20 + GIT (necessário p/ deps via git)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates gnupg git gcc g++ libpq-dev && \
    rm -rf /var/lib/apt/lists/* && \
    update-ca-certificates

# Instala Node 20 (NodeSource)
RUN bash -lc 'set -e; \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get update && apt-get install -y --no-install-recommends nodejs && \
    node -v && npm -v && \
    rm -rf /var/lib/apt/lists/*'

# Usuário não-root
RUN groupadd --gid 1001 app && useradd --uid 1001 --gid app --shell /bin/bash --create-home app
WORKDIR /app

# ---------- camada de dependências (melhor cache) ----------
# Node deps (servidor WhatsApp)
COPY package*.json ./
RUN if [ -f package.json ]; then \
      if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi; \
    else \
      echo "⚠️  package.json não encontrado; ignorando deps Node (serviço WhatsApp não iniciará)"; \
    fi

# Python deps (use apenas requirements.txt para evitar -e . do pyproject)
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ---------- código da aplicação ----------
# Copia tudo (bot + whatsapp_baileys_multi.js)
COPY . .

# Pastas úteis
RUN mkdir -p /app/logs /app/sessions /app/backups && chown -R app:app /app

# Script de start que sobe WhatsApp (:3001), espera /health e inicia o bot
RUN printf '#!/bin/bash\n\
set -euo pipefail\n\
echo \"🚀 Iniciando serviços (WhatsApp + Bot)\"\n\
export PYTHONUNBUFFERED=1\n\
export PYTHONDONTWRITEBYTECODE=1\n\
export TZ=\"${TZ:-America/Sao_Paulo}\"\n\
# Força o cliente Python a usar 127.0.0.1:3001 (fallback Railway)\n\
export RAILWAY_ENVIRONMENT_NAME=local\n\
export PORT=${PORT:-3001}\n\
# Sobe WhatsApp\n\
echo \"📱 Subindo WhatsApp em :${PORT}…\"\n\
node /app/whatsapp_baileys_multi.js &\n\
WA_PID=$!\n\
# Espera health\n\
echo \"⏳ Aguardando /health do WhatsApp…\"\n\
for i in {1..30}; do\n\
  if curl -fsS http://127.0.0.1:${PORT}/health >/dev/null 2>&1; then echo \"✅ WhatsApp pronto\"; break; fi\n\
  echo \"… tentativa $i/30\"; sleep 2;\n\
done\n\
# Inicia o bot\n\
echo \"🤖 Iniciando Bot Telegram…\"\n\
python /app/main.py &\n\
BOT_PID=$!\n\
# Trap para encerramento limpo\n\
cleanup(){ echo \"🛑 Encerrando…\"; kill $BOT_PID $WA_PID 2>/dev/null || true; wait || true; }\n\
trap cleanup SIGTERM SIGINT\n\
# Acompanha processos\n\
wait -n $BOT_PID $WA_PID\n\
EXIT_CODE=$?\n\
cleanup\n\
exit ${EXIT_CODE}\n' > /app/start.sh \
 && chmod +x /app/start.sh \
 && chown app:app /app/start.sh

# Porta do WhatsApp (interno) e, se quiser, a do bot (ex.: 5000 se usar webhook)
EXPOSE 3001 5000

# Healthcheck do WhatsApp
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=5 \
  CMD curl -fsS http://127.0.0.1:3001/health || exit 1

USER app
CMD ["/app/start.sh"]
