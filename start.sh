#!/bin/bash
set -euo pipefail

echo "🚀 Iniciando Container..."

# Configurações de ambiente
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export TZ="${TZ:-America/Sao_Paulo}"

# Iniciar servidor WhatsApp em background
echo "📱 Iniciando WhatsApp Service..."
cd /app/whatsapp
node whatsapp_baileys_multi.js &
WA_PID=$!
cd /app

# Esperar o servidor WhatsApp ficar online
echo "⏳ Aguardando WhatsApp Service..."
for i in $(seq 1 30); do
    if curl -s http://localhost:3001/health > /dev/null; then
        echo "✅ WhatsApp Service está pronto"
        break
    fi
    echo "⏳ Tentativa $i/30..."
    sleep 2
done

# Iniciar o bot principal
echo "🤖 Iniciando Telegram Bot..."
python3 main.py &
BOT_PID=$!

# Capturar sinais para encerrar com segurança
cleanup() {
    echo "🛑 Encerrando serviços..."
    kill $BOT_PID $WA_PID 2>/dev/null || true
    wait
    echo "✅ Serviços finalizados"
    exit 0
}
trap cleanup SIGTERM SIGINT

wait $BOT_PID $WA_PID
