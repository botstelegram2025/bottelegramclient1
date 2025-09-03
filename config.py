import os
from datetime import timedelta

class Config:
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/telegram_bot")
    
    # Telegram Bot
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "your_telegram_bot_token")
    
    # Bayleys WhatsApp API
    BAYLEYS_API_URL = os.getenv("BAYLEYS_API_URL", "https://api.bayleys.com")
    BAYLEYS_API_KEY = os.getenv("BAYLEYS_API_KEY", "your_bayleys_api_key")
    BAYLEYS_INSTANCE_ID = os.getenv("BAYLEYS_INSTANCE_ID", "your_instance_id")
    
    # Mercado Pago
    MERCADO_PAGO_ACCESS_TOKEN = os.getenv("MERCADO_PAGO_ACCESS_TOKEN", "your_mp_access_token")
    MERCADO_PAGO_PUBLIC_KEY = os.getenv("MERCADO_PAGO_PUBLIC_KEY", "your_mp_public_key")
    
    # Subscription Settings
    TRIAL_PERIOD_DAYS = 7
    MONTHLY_SUBSCRIPTION_PRICE = 20.00
    
    # Reminder Settings
    REMINDER_DAYS = [-2, -1, 0, 1]  # Days relative to due date
    
    # Timezone
    TIMEZONE = "America/Sao_Paulo"
    
    # Logging
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
