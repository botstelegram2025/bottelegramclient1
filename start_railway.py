#!/usr/bin/env python3
"""
Railway deployment startup script for Telegram Client Management Bot
Manages both Telegram Bot and WhatsApp Baileys services in Railway environment
"""

import os
import sys
import logging
import subprocess
import time
import threading
import signal
from datetime import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('railway_startup.log')
    ]
)
logger = logging.getLogger(__name__)

class RailwayServiceManager:
    def __init__(self):
        self.processes = {}
        self.running = True
        
    def setup_environment(self):
        """Setup Railway-specific environment variables"""
        logger.info("🔧 Setting up Railway environment...")
        
        # Set default values for Railway
        os.environ.setdefault('NODE_ENV', 'production')
        os.environ.setdefault('PYTHONUNBUFFERED', '1')
        os.environ.setdefault('WHATSAPP_PORT', '3001')
        os.environ.setdefault('PORT', '8080')
        
        # Railway PostgreSQL database URL
        if 'DATABASE_URL' not in os.environ:
            logger.warning("⚠️  DATABASE_URL not found. Make sure to add PostgreSQL service in Railway")
            
        # WhatsApp service URL for internal communication
        os.environ.setdefault('WHATSAPP_SERVICE_URL', 'http://localhost:3001')
        
        logger.info(f"✅ Environment configured for Railway deployment")
        
    def start_whatsapp_service(self):
        """Start WhatsApp Baileys service"""
        try:
            logger.info("🚀 Starting WhatsApp Baileys service...")
            
            # Install Node.js dependencies if needed
            if not os.path.exists('node_modules'):
                logger.info("📦 Installing Node.js dependencies...")
                subprocess.run(['npm', 'install'], check=True)
            
            # Start WhatsApp service
            cmd = ['node', 'whatsapp_baileys_multi.js']
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                env=os.environ.copy()
            )
            
            self.processes['whatsapp'] = process
            logger.info("✅ WhatsApp Baileys service started")
            
            # Monitor WhatsApp service output
            def monitor_whatsapp():
                while self.running and process.poll() is None:
                    try:
                        if process.stdout:
                            line = process.stdout.readline()
                            if line:
                                logger.info(f"WhatsApp: {line.strip()}")
                    except:
                        break
                        
            threading.Thread(target=monitor_whatsapp, daemon=True).start()
            
        except Exception as e:
            logger.error(f"❌ Failed to start WhatsApp service: {e}")
            raise
            
    def start_telegram_bot(self):
        """Start Telegram Bot service"""
        try:
            logger.info("🤖 Starting Telegram Bot service...")
            
            # Wait a moment for WhatsApp service to initialize
            time.sleep(5)
            
            # Start Telegram bot
            cmd = ['python', 'main.py']
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                env=os.environ.copy()
            )
            
            self.processes['telegram'] = process
            logger.info("✅ Telegram Bot service started")
            
            # Monitor Telegram bot output
            def monitor_telegram():
                while self.running and process.poll() is None:
                    try:
                        if process.stdout:
                            line = process.stdout.readline()
                            if line:
                                logger.info(f"Telegram: {line.strip()}")
                    except:
                        break
                        
            threading.Thread(target=monitor_telegram, daemon=True).start()
            
        except Exception as e:
            logger.error(f"❌ Failed to start Telegram Bot: {e}")
            raise
            
    def health_check(self):
        """Simple health check endpoint"""
        try:
            from flask import Flask, jsonify
            app = Flask(__name__)
            
            @app.route('/health')
            def health():
                status = {
                    'status': 'healthy',
                    'timestamp': datetime.now().isoformat(),
                    'services': {}
                }
                
                for service_name, process in self.processes.items():
                    if process and process.poll() is None:
                        status['services'][service_name] = 'running'
                    else:
                        status['services'][service_name] = 'stopped'
                        
                return jsonify(status)
                
            # Start health check server
            port = int(os.environ.get('PORT', 8080))
            threading.Thread(
                target=lambda: app.run(host='0.0.0.0', port=port, debug=False),
                daemon=True
            ).start()
            
            logger.info(f"🏥 Health check endpoint started on port {port}")
            
        except Exception as e:
            logger.warning(f"⚠️  Health check setup failed: {e}")
            
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"📡 Received signal {signum}, shutting down...")
        self.shutdown()
        
    def shutdown(self):
        """Shutdown all services gracefully"""
        self.running = False
        logger.info("🛑 Shutting down services...")
        
        for service_name, process in self.processes.items():
            if process and process.poll() is None:
                logger.info(f"🔄 Stopping {service_name} service...")
                try:
                    process.terminate()
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    logger.warning(f"⚠️  Force killing {service_name} service")
                    process.kill()
                    
        logger.info("✅ All services stopped")
        sys.exit(0)
        
    def run(self):
        """Main entry point"""
        try:
            logger.info("🚀 Starting Railway deployment...")
            
            # Setup signal handlers
            signal.signal(signal.SIGTERM, self.signal_handler)
            signal.signal(signal.SIGINT, self.signal_handler)
            
            # Setup environment
            self.setup_environment()
            
            # Start health check endpoint
            self.health_check()
            
            # Start services
            self.start_whatsapp_service()
            self.start_telegram_bot()
            
            logger.info("✅ All services started successfully")
            
            # Keep main thread alive
            while self.running:
                time.sleep(1)
                
                # Check if any process died and restart if needed
                for service_name, process in list(self.processes.items()):
                    if process and process.poll() is not None:
                        logger.error(f"❌ {service_name} service died, attempting restart...")
                        
                        if service_name == 'whatsapp':
                            self.start_whatsapp_service()
                        elif service_name == 'telegram':
                            self.start_telegram_bot()
                            
        except KeyboardInterrupt:
            logger.info("👋 Received keyboard interrupt")
            self.shutdown()
        except Exception as e:
            logger.error(f"💥 Fatal error: {e}")
            self.shutdown()
            raise

if __name__ == '__main__':
    logger.info("🎯 Railway Telegram Bot starting...")
    manager = RailwayServiceManager()
    manager.run()