#!/bin/sh
set -eu

echo "Iniciando serviços (WhatsApp + Telegram)"

# Ambiente
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
: "${TZ:=America/Sao_Paulo}"
export TZ
: "${PORT:=3001}"        # Porta do servidor WhatsApp local
export PORT

# Se não houver URL externa definida, o bot falará com o WhatsApp local
if [ -z "${WHATSAPP_SERVICE_URL:-}" ]; then
  export WHATSAPP_SERVICE_URL="http://127.0.0.1:${PORT}"
fi

# (IMPORTANTE) Não instalar dependências em runtime:
# Removidos: "pip install -r requirements.txt" e "npm install"
# Garanta as dependências no build (Dockerfile/Nixpacks).

# --- Inicia WhatsApp (se presente) ---
WA_PID=""
if [ -f "/app/whatsapp_baileys_multi.js" ]; then
  echo "Subindo WhatsApp na porta ${PORT}"
  node /app/whatsapp_baileys_multi.js &
  WA_PID=$!

  # Aguarda /health
  echo "Aguardando WhatsApp responder em /health"
  i=1
  while [ "$i" -le 30 ]; do
    if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
      echo "WhatsApp pronto"
      break
    fi
    echo "Tentativa $i de 30"
    i=`expr "$i" + 1`
    sleep 2
  done
else
  echo "Aviso: /app/whatsapp_baileys_multi.js não encontrado; WhatsApp será ignorado"
fi

# --- Inicia Telegram bot ---
if [ -f "/app/main.py" ]; then
  echo "Iniciando bot Telegram"
  python /app/main.py &
  TELEGRAM_PID=$!
else
  echo "ERRO: /app/main.py não encontrado"
  # Se quiser manter o WhatsApp vivo mesmo sem o bot, comente o exit 1
  exit 1
fi

# Trap (sem função) para desligar limpo
trap 'echo "Encerrando..."; \
  kill "$TELEGRAM_PID" 2>/dev/null || true; \
  [ -n "${WA_PID}" ] && kill "$WA_PID" 2>/dev/null || true; \
  wait 2>/dev/null || true; \
  exit 0' INT TERM

# Monitor/auto-restart simples
while true; do
  # Reinicia WhatsApp se cair (quando existir)
  if [ -n "${WA_PID}" ] && ! kill -0 "$WA_PID" 2>/dev/null; then
    echo "WhatsApp parou; reiniciando..."
    node /app/whatsapp_baileys_multi.js &
    WA_PID=$!
  fi

  # Reinicia bot se cair
  if ! kill -0 "$TELEGRAM_PID" 2>/dev/null; then
    echo "Bot Telegram parou; reiniciando..."
    python /app/main.py &
    TELEGRAM_PID=$!
  fi

  sleep 10
done
