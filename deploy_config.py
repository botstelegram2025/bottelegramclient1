"""
Configuração específica para deploy no Railway
"""
import os

# Railway Environment Configuration
RAILWAY_ENV = os.getenv('RAILWAY_ENVIRONMENT_NAME') is not None

# Database Configuration
if RAILWAY_ENV:
    DATABASE_URL = os.getenv('DATABASE_URL', '')
    if DATABASE_URL.startswith('postgres://'):
        DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)
else:
    DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://user:password@localhost:5432/telegram_bot')

# WhatsApp Service Configuration
WHATSAPP_PORT = int(os.getenv('WHATSAPP_PORT', 3001))
# No Railway usamos 127.0.0.1 para comunicação interna
WHATSAPP_URL = os.getenv('WHATSAPP_URL', f"http://127.0.0.1:{WHATSAPP_PORT}")

# Telegram Bot Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN', '')
TELEGRAM_PORT = int(os.getenv('PORT', 8080))

# Mercado Pago Configuration
MERCADO_PAGO_ACCESS_TOKEN = os.getenv('MERCADO_PAGO_ACCESS_TOKEN', '')

# Production Settings
DEBUG = not RAILWAY_ENV
LOG_LEVEL = 'INFO' if RAILWAY_ENV else 'DEBUG'

print(f"🔧 Railway Environment: {RAILWAY_ENV}")
print(f"🌐 WhatsApp URL: {WHATSAPP_URL}")
print(f"📊 Database configured: {'✅' if DATABASE_URL else '❌'}")
print(f"🤖 Bot Token configured: {'✅' if BOT_TOKEN else '❌'}")