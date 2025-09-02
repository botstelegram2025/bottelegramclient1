#!/usr/bin/env python3
"""
Launcher unificado para Railway - versão simplificada
Executa WhatsApp em background thread e Telegram em processo principal
"""
import os
import sys
import threading
import subprocess
import time
import logging
import signal

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - RAILWAY - %(levelname)s - %(message)s'
)
logger = logging.getLogger('railway_unified')

def start_whatsapp_background():
    """Start WhatsApp Baileys in background thread"""
    def run_whatsapp():
        try:
            logger.info("🚀 Starting WhatsApp Baileys in background...")
            
            env = os.environ.copy()
            env['NODE_ENV'] = 'production'
            env['PORT'] = str(os.getenv('WHATSAPP_PORT', 3001))
            
            process = subprocess.Popen(
                ['node', 'whatsapp_baileys_multi.js'],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Log output
            while True:
                output = process.stdout.readline() if process.stdout else ""
                if output == '' and process.poll() is not None:
                    break
                if output:
                    logger.info(f"[WhatsApp] {output.strip()}")
                    
        except Exception as e:
            logger.error(f"❌ WhatsApp error: {e}")
    
    # Start WhatsApp in daemon thread
    thread = threading.Thread(target=run_whatsapp, daemon=True)
    thread.start()
    logger.info("✅ WhatsApp started in background thread")
    
    # Wait a bit for WhatsApp to initialize
    time.sleep(5)

def main():
    """Main execution"""
    logger.info("🌟 Starting Railway Unified Launcher...")
    
    try:
        # 1. Start WhatsApp in background
        start_whatsapp_background()
        
        # 2. Wait for WhatsApp to be ready
        logger.info("⏳ Waiting for WhatsApp server...")
        time.sleep(10)  # Give more time for WhatsApp initialization
        
        # 3. Set environment for Telegram
        os.environ['WHATSAPP_URL'] = 'http://127.0.0.1:3001'
        os.environ['PYTHONUNBUFFERED'] = '1'
        
        # 4. Import and start Telegram bot
        logger.info("🤖 Starting Telegram bot...")
        
        # Configure runtime
        try:
            import runtime_config
            runtime_config.configure_for_railway()
        except ImportError:
            logger.warning("⚠️ No runtime_config found, using defaults")
        
        # Start main bot
        import main
        logger.info("✅ Telegram bot started!")
        
    except KeyboardInterrupt:
        logger.info("📴 Received keyboard interrupt, shutting down...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Startup error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()