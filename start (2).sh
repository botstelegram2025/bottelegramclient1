#!/bin/bash

# Script de inicialização para Railway
# Inicia ambos os serviços: Telegram Bot e WhatsApp Server

echo "🚀 Iniciando serviços no Railway..."

# Verificar se as dependências estão instaladas
echo "📦 Verificando dependências Python..."
pip install -q -r requirements.txt

echo "📦 Verificando dependências Node.js..."
npm install --silent

# Iniciar o servidor WhatsApp em background
echo "📱 Iniciando servidor WhatsApp Baileys..."
node whatsapp_baileys_multi.js &
WHATSAPP_PID=$!

# Aguardar alguns segundos para o servidor WhatsApp inicializar
sleep 5

# Iniciar o bot Telegram
echo "🤖 Iniciando bot Telegram..."
python main.py &
TELEGRAM_PID=$!

# Função para cleanup em caso de encerramento
cleanup() {
    echo "🛑 Encerrando serviços..."
    kill $WHATSAPP_PID 2>/dev/null
    kill $TELEGRAM_PID 2>/dev/null
    exit 0
}

# Capturar sinais de encerramento
trap cleanup SIGTERM SIGINT

echo "✅ Ambos os serviços estão rodando"
echo "WhatsApp Server PID: $WHATSAPP_PID"
echo "Telegram Bot PID: $TELEGRAM_PID"

# Manter o script ativo e monitorar os processos
while true; do
    # Verificar se os processos ainda estão rodando
    if ! kill -0 $WHATSAPP_PID 2>/dev/null; then
        echo "⚠️ Servidor WhatsApp parou, reiniciando..."
        node whatsapp_baileys_multi.js &
        WHATSAPP_PID=$!
    fi
    
    if ! kill -0 $TELEGRAM_PID 2>/dev/null; then
        echo "⚠️ Bot Telegram parou, reiniciando..."
        python main.py &
        TELEGRAM_PID=$!
    fi
    
    sleep 30
done