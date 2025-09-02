#!/usr/bin/env python3
"""
Railway Launcher V2 - Solução definitiva
1. Migra base de dados primeiro
2. Inicia WhatsApp em processo separado com bind correto
3. Inicia Bot Telegram que conecta ao WhatsApp
"""
import os
import sys
import subprocess
import time
import logging
import signal
import threading
import requests
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - Railway-V2 - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('railway_v2')

def migrate_database_first():
    """Migrate database before starting services"""
    try:
        logger.info("🗄️ Running database migration...")
        
        # Import and run migration
        from database_migration import migrate_database
        success = migrate_database()
        
        if success:
            logger.info("✅ Database migration completed")
            return True
        else:
            logger.error("❌ Database migration failed")
            return False
            
    except Exception as e:
        logger.error(f"❌ Migration error: {e}")
        return False

def start_whatsapp_process():
    """Start WhatsApp Baileys as separate process"""
    try:
        logger.info("🚀 Starting WhatsApp Baileys process...")
        
        # Ensure node_modules exist
        node_modules = Path("node_modules")
        if not node_modules.exists():
            logger.warning("📦 Installing Node.js dependencies...")
            subprocess.run(['npm', 'install'], check=True, timeout=120)
        
        # Set environment for WhatsApp
        env = os.environ.copy()
        env['NODE_ENV'] = 'production'
        env['PORT'] = str(os.getenv('PORT', 8080))  # Railway port
        env['WHATSAPP_INTERNAL_PORT'] = '3001'      # Internal WhatsApp port
        
        # Start WhatsApp process
        whatsapp_process = subprocess.Popen(
            ['node', 'whatsapp_baileys_multi.js'],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )
        
        logger.info("✅ WhatsApp process started")
        
        # Monitor WhatsApp output in background
        def monitor_whatsapp():
            try:
                for line in whatsapp_process.stdout:
                    if line.strip():
                        logger.info(f"[WhatsApp] {line.strip()}")
            except:
                pass
        
        threading.Thread(target=monitor_whatsapp, daemon=True).start()
        
        return whatsapp_process
        
    except Exception as e:
        logger.error(f"❌ Failed to start WhatsApp: {e}")
        return None

def wait_for_whatsapp(max_attempts=30):
    """Wait for WhatsApp to be ready"""
    logger.info("⏳ Waiting for WhatsApp to be ready...")
    
    # Try multiple URLs
    urls_to_try = [
        'http://127.0.0.1:3001/health',
        'http://localhost:3001/health',
        f'http://127.0.0.1:{os.getenv("PORT", 8080)}/health'
    ]
    
    for attempt in range(max_attempts):
        for url in urls_to_try:
            try:
                response = requests.get(url, timeout=2)
                if response.status_code == 200:
                    logger.info(f"✅ WhatsApp ready at {url}")
                    # Set the working URL as environment variable
                    os.environ['WHATSAPP_URL'] = url.replace('/health', '')
                    return True
            except:
                pass
        
        logger.info(f"⏳ Attempt {attempt + 1}/{max_attempts}")
        time.sleep(2)
    
    logger.warning("⚠️ WhatsApp not responding, continuing anyway...")
    return False

def start_telegram_bot():
    """Start Telegram bot in current process"""
    try:
        logger.info("🤖 Configuring Telegram bot...")
        
        # Set WhatsApp URL for internal communication
        if not os.getenv('WHATSAPP_URL'):
            os.environ['WHATSAPP_URL'] = 'http://127.0.0.1:3001'
        
        # Configure runtime
        os.environ['PYTHONUNBUFFERED'] = '1'
        
        # Add current directory to Python path
        if '.' not in sys.path:
            sys.path.insert(0, '.')
        
        logger.info("🔗 Starting Telegram bot...")
        
        # Import and run main
        import main
        
        logger.info("✅ Telegram bot started successfully!")
        
    except Exception as e:
        logger.error(f"❌ Telegram bot error: {e}")
        raise

def main():
    """Main launcher"""
    whatsapp_process = None
    
    try:
        logger.info("🌟 Railway Launcher V2 Starting...")
        
        # Step 1: Migrate database
        if not migrate_database_first():
            logger.error("❌ Database migration failed, exiting...")
            return 1
        
        # Step 2: Start WhatsApp
        whatsapp_process = start_whatsapp_process()
        if not whatsapp_process:
            logger.error("❌ WhatsApp failed to start, exiting...")
            return 1
        
        # Step 3: Wait for WhatsApp
        wait_for_whatsapp()
        
        # Step 4: Start Telegram bot
        start_telegram_bot()
        
        logger.info("🎉 All services running successfully!")
        
    except KeyboardInterrupt:
        logger.info("📴 Received keyboard interrupt")
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        return 1
    finally:
        # Cleanup
        if whatsapp_process:
            try:
                whatsapp_process.terminate()
                whatsapp_process.wait(timeout=5)
            except:
                whatsapp_process.kill()
        
        logger.info("🛑 Shutdown complete")
    
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)