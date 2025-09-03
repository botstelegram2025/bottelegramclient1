#!/bin/sh
set -eu

echo "Iniciando servicos: WhatsApp + Telegram"

# Ambiente basico
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
: "${TZ:=America/Sao_Paulo}"
export TZ
: "${PORT:=3001}"
export PORT

# Sobe o servidor WhatsApp (Baileys) em background
echo "Subindo WhatsApp na porta ${PORT}..."
node /app/whatsapp_baileys_multi.js &
WA_PID=$!

# Aguarda /health responder
echo "Aguardando WhatsApp /health..."
i=1
while [ "$i" -le 30 ]; do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "WhatsApp pronto"
    break
  fi
  echo "Tentativa ${i}/30"
  i=$((i+1))
  sleep 2
done

# Inicia o bot Telegram
echo "Iniciando bot Telegram..."
python /app/main.py &
BOT_PID=$!

# Encerramento limpo
cleanup() {
  echo "Encerrando processos..."
  kill "$BOT_PID" "$WA_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM

# Mantem o container vivo e monitora os dois processos
while :; do
  if ! kill -0 "$BOT_PID" 2>/dev/null; then
    echo "Bot finalizou; encerrando WhatsApp..."
    kill "$WA_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    exit 0
  fi
  if ! kill -0 "$WA_PID" 2>/dev/null; then
    echo "WhatsApp finalizou; encerrando bot..."
    kill "$BOT_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    exit 0
  fi
  sleep 5
done
