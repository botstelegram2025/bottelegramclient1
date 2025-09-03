#!/bin/bash
set -euo pipefail

echo "🚀 Iniciando serviços…"

# Variáveis úteis
export TZ="${TZ:-America/Sao_Paulo}"
export WHATSAPP_SERVICE_URL="${WHATSAPP_SERVICE_URL:-http://127.0.0.1:3001}"

# 1) (Opcional) deps Python – idealmente já instaladas no build
if [ -f requirements.txt ]; then
  pip install -q -r requirements.txt || true
fi

# 2) deps Node do WhatsApp (fallback leve; no build é melhor)
if [ -f /app/whatsapp/package.json ]; then
  pushd /app/whatsapp >/dev/null
  if [ -f package-lock.json ]; then
    npm ci --omit=dev || npm i --omit=dev
  else
    npm i --omit=dev
  fi
  popd >/dev/null
fi

# 3) Sobe WhatsApp ESM .mjs
echo "📱 Iniciando WhatsApp Baileys (ESM)…"
cd /app/whatsapp
node whatsapp_baileys_multi.mjs &
WHATSAPP_PID=$!
cd /app

# 4) Espera ficar pronto
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:3001/health" >/dev/null 2>&1; then
    echo "✅ WhatsApp service OK"
    break
  fi
  echo "⏳ Waiting WhatsApp… ($i/30)"
  sleep 2
done

# 5) Sobe o bot
echo "🤖 Iniciando Telegram bot…"
python main.py &
BOT_PID=$!

# 6) Encerramento limpo
cleanup() {
  echo "🛑 Finalizando…"
  kill ${BOT_PID} ${WHATSAPP_PID} 2>/dev/null || true
  wait || true
}
trap cleanup INT TERM

wait ${BOT_PID} ${WHATSAPP_PID}
