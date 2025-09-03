#!/bin/bash
set -Eeuo pipefail

export PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=${TZ:-America/Sao_Paulo}

WA_DIR="/app/whatsapp"
WA_JS_NAME="whatsapp_baileys_multi.js"

# Detect ESM file
if [[ -f "${WA_DIR}/${WA_JS_NAME}" ]]; then
  WA_ENTRY="${WA_DIR}/${WA_JS_NAME}"
else
  echo "❌ WhatsApp server entry not found"
  exit 2
fi

# Warn if file still CommonJS
if grep -q "require(" "$WA_ENTRY"; then
  echo "⚠️ $WA_ENTRY usa require(...). Node está em modo ES Module (type: module). Renomeando para .cjs temporariamente."
  mv "$WA_ENTRY" "$WA_ENTRY.cjs"
  WA_ENTRY="$WA_ENTRY.cjs"
fi

# Install deps only if node_modules missing (optional)
if [[ -f "$(dirname "$WA_ENTRY")/package.json" && ! -d "$(dirname "$WA_ENTRY")/node_modules" ]]; then
  echo "📦 Instalando deps Node..."
  npm --prefix "$(dirname "$WA_ENTRY")" install --omit=dev
fi

# Export service URL for Python bot
export WHATSAPP_SERVICE_URL="http://127.0.0.1:3001"

# Start Node
node "$WA_ENTRY" &
WHATSAPP_PID=$!

# Wait for health
for i in {1..30}; do
  if curl -fsS "http://127.0.0.1:3001/health" >/dev/null; then
    echo "✅ WhatsApp service is ready"
    break
  fi
  echo "⏳ Waiting WhatsApp... ($i/30)"
  sleep 1
done

# Start Python
python main.py &
TELEGRAM_PID=$!

# trap
cleanup(){ kill $WHATSAPP_PID $TELEGRAM_PID 2>/dev/null || true; wait || true; }
trap cleanup SIGINT SIGTERM

wait -n $WHATSAPP_PID $TELEGRAM_PID
exit $?
