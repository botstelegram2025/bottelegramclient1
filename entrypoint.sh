#!/usr/bin/env bash
set -euo pipefail

# Defaults (overridable via env)
: "${PORT:=8080}"
: "${FLASK_HOST:=0.0.0.0}"
: "${PY_ENTRY:=main.py}"                       # Python entry (your Flask+Bot file). Use main.py or main_webhook_hardened_fixed.py
: "${NODE_ENTRY:=server.js}"                   # Node entry (your Baileys server)

echo "🚀 Booting services... PORT=${PORT}, PY_ENTRY=${PY_ENTRY}, NODE_ENTRY=${NODE_ENTRY}"

# Helpful env echo for debugging
echo "ENV summary:"
echo "  - NODE_ENV=${NODE_ENV:-unset}"
echo "  - FLASK_HOST=${FLASK_HOST}"
echo "  - PYTHONUNBUFFERED=${PYTHONUNBUFFERED:-unset}"

# Start Node (if present)
if [ -f "/app/${NODE_ENTRY}" ]; then
  echo "▶️  Starting Node: ${NODE_ENTRY} (port 3001)"
  node "/app/${NODE_ENTRY}" &
  NODE_PID=$!
  echo "Node PID=${NODE_PID}"
else
  echo "⚠️  Node entry '/app/${NODE_ENTRY}' not found; skipping Baileys server"
fi

# Start Python (stays in foreground as PID 1)
if [ -f "/app/${PY_ENTRY}" ]; then
  echo "🐍 Starting Python: ${PY_ENTRY} (Flask should bind 0.0.0.0:${PORT})"
  exec python3 -u "/app/${PY_ENTRY}"
else
  echo "❌ Python entry '/app/${PY_ENTRY}' not found"; exit 1
fi
