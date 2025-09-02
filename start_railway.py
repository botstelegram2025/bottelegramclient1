#!/usr/bin/env python3
"""
Script de inicialização para Railway
Roda WhatsApp Baileys em background e depois o bot Telegram
"""
import os
import sys
import threading
import subprocess
import time
import signal
import logging
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - Railway - %(levelname)s - %(message)s'
)
logger = logging.getLogger('railway_starter')

class RailwayStarter:
    def __init__(self):
        self.whatsapp_process = None
        self.running = True
        
    def start_whatsapp_background(self):
        """Start WhatsApp Baileys server in background"""
        try:
            logger.info("🚀 Starting WhatsApp Baileys server in background...")
            cmd = ["node", "whatsapp_baileys_multi.js"]
            
            env = os.environ.copy()
            env['NODE_ENV'] = 'production'
            env['PORT'] = str(os.getenv('WHATSAPP_PORT', 3001))
            
            self.whatsapp_process = subprocess.Popen(
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            
            # Log WhatsApp output in separate thread
            def log_whatsapp():
                try:
                    if self.whatsapp_process and self.whatsapp_process.stdout:
                        for line in self.whatsapp_process.stdout:
                            logger.info(f"[WhatsApp] {line.strip()}")
                except Exception as e:
                    logger.error(f"Error logging WhatsApp output: {e}")
                    
            threading.Thread(target=log_whatsapp, daemon=True).start()
            logger.info("✅ WhatsApp Baileys started in background")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start WhatsApp server: {e}")
            return False
    
    def wait_whatsapp_ready(self):
        """Wait for WhatsApp server to be ready"""
        logger.info("⏳ Waiting for WhatsApp server to be ready...")
        whatsapp_url = os.getenv('WHATSAPP_URL', 'http://127.0.0.1:3001')
        
        for attempt in range(30):  # 30 tentativas = 30 segundos
            try:
                response = requests.get(f"{whatsapp_url}/health", timeout=2)
                if response.status_code == 200:
                    logger.info("✅ WhatsApp server is ready!")
                    return True
            except:
                pass
            
            time.sleep(1)
            logger.info(f"⏳ Attempt {attempt + 1}/30 - WhatsApp not ready yet...")
        
        logger.warning("⚠️ WhatsApp server not responding, continuing anyway...")
        return False
        
    def start_telegram_bot(self):
        """Start Telegram bot directly (not subprocess)"""
        try:
            logger.info("🤖 Starting Telegram bot...")
            
            # Import and run main bot
            import sys
            sys.path.insert(0, '.')  # Ensure current directory is in path
            import main
            logger.info("✅ Telegram bot started!")
            
        except Exception as e:
            logger.error(f"❌ Failed to start Telegram bot: {e}")
            raise
    
    def handle_signal(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"📴 Received signal {signum}, shutting down...")
        self.running = False
        self.shutdown()
        
    def shutdown(self):
        """Gracefully shutdown all processes"""
        logger.info("🛑 Shutting down services...")
        
        if self.whatsapp_process:
            try:
                logger.info("⏹️ Stopping WhatsApp...")
                self.whatsapp_process.terminate()
                self.whatsapp_process.wait(timeout=10)
                logger.info("✅ WhatsApp stopped")
            except subprocess.TimeoutExpired:
                logger.warning("⚠️ Force killing WhatsApp...")
                self.whatsapp_process.kill()
            except Exception as e:
                logger.error(f"❌ Error stopping WhatsApp: {e}")
        
        sys.exit(0)
    
    def run(self):
        """Main execution method"""
        logger.info("🌟 Starting Railway deployment...")
        
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)
        
        try:
            # 1. Start WhatsApp in background
            if not self.start_whatsapp_background():
                logger.error("❌ Failed to start WhatsApp server")
                return
            
            # 2. Wait for WhatsApp to be ready
            self.wait_whatsapp_ready()
            
            # 3. Start Telegram bot (blocking)
            self.start_telegram_bot()
            
        except KeyboardInterrupt:
            logger.info("📴 Received keyboard interrupt")
            self.shutdown()
        except Exception as e:
            logger.error(f"❌ Startup error: {e}")
            self.shutdown()

if __name__ == "__main__":
    starter = RailwayStarter()
    starter.run()