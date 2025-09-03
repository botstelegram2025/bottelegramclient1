#!/usr/bin/env sh
set -eu

echo "🚀 Iniciando WhatsApp + Bot…"

# Railway injeta PORT (ex.: 8080). Se não vier, usa 3001.
PORT="${PORT:-3001}"

# WhatsApp local no mesmo container: use 127.0.0.1:$PORT
export WHATSAPP_SERVICE_URL="http://127.0.0.1:${PORT}"

echo "🩺 Verificando WhatsApp em ${WHATSAPP_SERVICE_URL}/health…"

# 1) Sobe o WhatsApp server (ESM)
if [ -f "whatsapp_baileys_multi.js" ]; then
  echo "📱 Subindo WhatsApp server (node)…"
  node whatsapp_baileys_multi.js &
  WA_PID=$!
else
  echo "⚠️  whatsapp_baileys_multi.js não encontrado; seguindo sem WhatsApp local"
  WA_PID=""
fi

# 2) Aguarda /health (até 20s)
if [ -n "${WA_PID}" ]; then
  i=0
  until curl -fsS "${WHATSAPP_SERVICE_URL%/}/health" >/dev/null 2>&1; do
    i=$((i+1))
    [ $i -ge 20 ] && { echo "❌ WhatsApp não respondeu em /health"; break; }
    sleep 1
  done
fi

# 3) Inicia o bot
echo "🤖 Iniciando bot (python)…"
python main.py &
BOT_PID=$!

# 4) Encerramento limpo
trap 'echo "🛑 Encerrando…"; [ -n "${BOT_PID}" ] && kill "${BOT_PID}" 2>/dev/null || true; [ -n "${WA_PID}" ] && kill "${WA_PID}" 2>/dev/null || true; exit 0' INT TERM

# 5) Monitor
while :; do
  alive=0
  if [ -n "${BOT_PID}" ] && kill -0 "${BOT_PID}" 2>/dev/null; then alive=$((alive+1)); fi
  if [ -n "${WA_PID}" ] && kill -0 "${WA_PID}" 2>/dev/null; then alive=$((alive+1)); fi
  [ $alive -eq 0 ] && { echo "❌ Ambos pararam. Saindo."; exit 1; }
  sleep 5
done
