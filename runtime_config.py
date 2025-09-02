"""
Runtime configuration for Railway deployment
This file is imported by main.py to configure services correctly
"""
import os

def configure_for_railway():
    """Configure environment variables for Railway deployment"""
    # Set WHATSAPP_URL if not already set
    if not os.getenv('WHATSAPP_URL'):
        os.environ['WHATSAPP_URL'] = 'http://127.0.0.1:3001'
    
    # Set PYTHONUNBUFFERED for better logging
    if not os.getenv('PYTHONUNBUFFERED'):
        os.environ['PYTHONUNBUFFERED'] = '1'
    
    print(f"🔧 Runtime configured - WhatsApp: {os.getenv('WHATSAPP_URL')}")
    
    return True