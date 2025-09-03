#!/bin/bash
set -euo pipefail

echo "🚀 Iniciando serviços…"

# Python deps (já devem estar instaladas no build; se insistir em instalar aqui, mantenha silencioso)
if [ -f requirements.txt ]; then
  pip install -q -r requirements.txt || true
fi

# Node deps (já devem estar instaladas no build; fallback leve)
if [ -f /app/whatsapp/package.json ]; then
  pushd /app/whatsapp >/dev/null
  if [ -f package-lock.json ]; then
    npm ci --omit=dev || npm i --omit=dev
  else
    npm i --omit=dev
  fi
  popd >/dev/null
fi

# Inicia WhatsApp (ESM .mjs)
echo "📱 Iniciando WhatsApp Baileys (ESM)…"
cd /app/whatsapp
node whatsapp_baileys_multi.mjs &
WHATSAPP_PID=$!
cd /app

# Espera ficar pronto
for i in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:3001/health" >/dev/null 2>&1; then
    echo "✅ WhatsApp service OK"
    break
  fi
  echo "⏳ Waiting WhatsApp… ($i/30)"
  sleep 2
done

# Inicia o bot
echo "🤖 Iniciando Telegram bot…"
python main.py &
BOT_PID=$!

cleanup() {
  echo "🛑 Finalizando…"
  kill ${BOT_PID} ${WHATSAPP_PID} 2>/dev/null || true
  wait || true
}
trap cleanup INT TERM

wait ${BOT_PID} ${WHATSAPP_PID}
