#!/usr/bin/env python3
"""
🚀 Railway Launch DEFINITIVO - Portas unificadas
Resolve conflito de portas entre WhatsApp e Telegram bot
"""
import os
import sys
import subprocess
import time
import logging
import threading
import requests
import signal
from pathlib import Path

# Logging configurado para Railway
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - RAILWAY-UNIFIED - %(levelname)s - %(message)s'
)
logger = logging.getLogger('railway_unified')

# Global process references
whatsapp_process = None

def get_unified_port():
    """Get unified port for both WhatsApp and internal communication"""
    railway_port = os.getenv('PORT', '8080')  # Railway dynamic port
    is_railway = os.getenv('RAILWAY_ENVIRONMENT_NAME') is not None
    
    if is_railway:
        port = railway_port
        logger.info(f"🚂 Railway mode: Using port {port}")
    else:
        port = '3001'  # Local development
        logger.info(f"💻 Local mode: Using port {port}")
    
    return port

def force_database_migration():
    """Force database migration with robust error handling"""
    try:
        logger.info("🗄️ FORCING database migration...")
        
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            logger.error("❌ DATABASE_URL not found")
            return False
        
        # Fix postgres:// URL
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        
        from sqlalchemy import create_engine, text
        
        logger.info("🔗 Connecting to database...")
        engine = create_engine(database_url, pool_pre_ping=True)
        
        with engine.connect() as conn:
            try:
                logger.info("📝 Adding is_default column...")
                conn.execute(text("""
                    ALTER TABLE message_templates 
                    ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE
                """))
                conn.commit()
                logger.info("✅ Column is_default added successfully")
            except Exception as e:
                logger.info(f"⚠️ Column might exist: {e}")
            
            # Verify column exists
            try:
                result = conn.execute(text("""
                    SELECT column_name FROM information_schema.columns 
                    WHERE table_name = 'message_templates' AND column_name = 'is_default'
                """))
                if result.fetchone():
                    logger.info("✅ Column is_default verified in database")
                    return True
                else:
                    logger.error("❌ Column is_default NOT found after migration")
                    return False
            except Exception as e:
                logger.error(f"❌ Failed to verify column: {e}")
                return False
                
    except Exception as e:
        logger.error(f"❌ Database migration failed: {e}")
        return False

def start_whatsapp_service():
    """Start WhatsApp service with unified port configuration"""
    global whatsapp_process
    
    try:
        unified_port = get_unified_port()
        logger.info(f"🚀 Starting WhatsApp on unified port: {unified_port}")
        
        # Check if node_modules exists
        if not Path("node_modules").exists():
            logger.info("📦 Installing Node.js dependencies...")
            result = subprocess.run(['npm', 'install', '--no-package-lock'], 
                                  capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                logger.error(f"❌ npm install failed: {result.stderr}")
                return None
        
        # Environment for WhatsApp with UNIFIED port
        env = os.environ.copy()
        env.update({
            'NODE_ENV': 'production',
            'PORT': unified_port,  # UNIFIED PORT
            'RAILWAY_ENVIRONMENT_NAME': env.get('RAILWAY_ENVIRONMENT_NAME', 'local')
        })
        
        logger.info(f"🌐 WhatsApp will bind to: 0.0.0.0:{unified_port}")
        
        # Start WhatsApp process
        whatsapp_process = subprocess.Popen(
            ['node', 'whatsapp_baileys_multi.js'],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1
        )
        
        # Monitor output
        def log_whatsapp_output():
            try:
                for line in whatsapp_process.stdout:
                    if line.strip():
                        logger.info(f"[WhatsApp] {line.strip()}")
                        if "rodando na porta" in line:
                            logger.info("✅ WhatsApp service is ready!")
            except:
                pass
        
        threading.Thread(target=log_whatsapp_output, daemon=True).start()
        
        logger.info("✅ WhatsApp process started")
        return whatsapp_process
        
    except Exception as e:
        logger.error(f"❌ Failed to start WhatsApp: {e}")
        return None

def wait_for_whatsapp_ready(max_wait=60):
    """Wait for WhatsApp with unified port"""
    unified_port = get_unified_port()
    
    logger.info(f"⏳ Waiting for WhatsApp on port {unified_port}...")
    
    # Health check URLs with UNIFIED port
    health_urls = [
        f'http://127.0.0.1:{unified_port}/health',
        f'http://localhost:{unified_port}/health'
    ]
    
    start_time = time.time()
    while (time.time() - start_time) < max_wait:
        for url in health_urls:
            try:
                response = requests.get(url, timeout=3)
                if response.status_code == 200:
                    logger.info(f"✅ WhatsApp ready at {url}")
                    # Set UNIFIED URL for telegram bot
                    whatsapp_url = url.replace('/health', '')
                    os.environ['WHATSAPP_URL'] = whatsapp_url
                    logger.info(f"🔗 UNIFIED WhatsApp URL set: {whatsapp_url}")
                    return True
            except:
                pass
        
        logger.info(f"⏳ Still waiting... ({int(time.time() - start_time)}s)")
        time.sleep(3)
    
    logger.warning("⚠️ WhatsApp health check failed, setting fallback...")
    # Set fallback UNIFIED URL
    os.environ['WHATSAPP_URL'] = f'http://127.0.0.1:{unified_port}'
    return False

def start_telegram_bot():
    """Start Telegram bot with unified port configuration"""
    try:
        unified_port = get_unified_port()
        logger.info(f"🤖 Starting Telegram bot (WhatsApp port: {unified_port})...")
        
        # Set environment for unified communication
        os.environ['PYTHONUNBUFFERED'] = '1'
        os.environ['PYTHONIOENCODING'] = 'utf-8'
        
        # Ensure WhatsApp URL is set with unified port
        if not os.environ.get('WHATSAPP_URL'):
            os.environ['WHATSAPP_URL'] = f'http://127.0.0.1:{unified_port}'
            
        logger.info(f"📡 Telegram will connect to: {os.environ['WHATSAPP_URL']}")
        
        # Add current directory to path
        if '.' not in sys.path:
            sys.path.insert(0, '.')
        
        # Import and run main bot
        logger.info("📲 Importing bot main module...")
        import main
        logger.info("✅ Telegram bot started successfully!")
        
    except Exception as e:
        logger.error(f"❌ Telegram bot failed: {e}")
        raise

def setup_signal_handlers():
    """Setup graceful shutdown"""
    def signal_handler(signum, frame):
        logger.info("🛑 Received shutdown signal, cleaning up...")
        
        global whatsapp_process
        
        if whatsapp_process:
            try:
                whatsapp_process.terminate()
                whatsapp_process.wait(timeout=10)
            except:
                whatsapp_process.kill()
        
        logger.info("✅ Cleanup complete")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

def main():
    """Main launcher with unified port configuration"""
    try:
        logger.info("🌟 Railway Unified Port Launcher Starting...")
        
        # Show port configuration
        unified_port = get_unified_port()
        is_railway = os.getenv('RAILWAY_ENVIRONMENT_NAME') is not None
        logger.info(f"🎯 UNIFIED PORT: {unified_port} (Railway: {is_railway})")
        
        # Setup signal handlers
        setup_signal_handlers()
        
        # Step 1: Force database migration
        logger.info("📋 Step 1: Database Migration")
        if not force_database_migration():
            logger.error("❌ CRITICAL: Database migration failed!")
            return 1
        
        # Step 2: Start WhatsApp service with unified port
        logger.info("📋 Step 2: WhatsApp Service (Unified Port)")
        whatsapp_proc = start_whatsapp_service()
        if not whatsapp_proc:
            logger.error("❌ CRITICAL: WhatsApp service failed!")
            return 1
        
        # Step 3: Wait for WhatsApp with unified port
        logger.info("📋 Step 3: WhatsApp Health Check (Unified Port)")
        wait_for_whatsapp_ready()
        
        # Step 4: Start Telegram bot with unified port
        logger.info("📋 Step 4: Telegram Bot (Unified Communication)")
        start_telegram_bot()
        
        logger.info("🎉 ALL SERVICES RUNNING WITH UNIFIED PORTS!")
        logger.info(f"🔥 Railway deployment FULLY FUNCTIONAL on port {unified_port}!")
        
        # Keep main thread alive
        while True:
            time.sleep(60)
            
    except KeyboardInterrupt:
        logger.info("📴 Keyboard interrupt received")
    except Exception as e:
        logger.error(f"❌ FATAL ERROR: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)