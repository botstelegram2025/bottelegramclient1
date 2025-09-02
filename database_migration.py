"""
Database migration script for Railway
Forces database schema sync with models
"""
import os
import logging
from sqlalchemy import create_engine, text
from models import Base
from services.database_service import db_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_database():
    """Force database schema update"""
    try:
        logger.info("🔄 Starting database migration...")
        
        # Get database URL
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            logger.error("❌ DATABASE_URL not found")
            return False
            
        # Fix postgres:// to postgresql://
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
            
        logger.info(f"🔗 Connecting to database...")
        
        # Create engine
        engine = create_engine(database_url, pool_pre_ping=True)
        
        # Add missing columns manually
        with engine.connect() as conn:
            try:
                # Add is_default column to message_templates if not exists
                logger.info("📝 Adding is_default column to message_templates...")
                conn.execute(text("""
                    ALTER TABLE message_templates 
                    ADD COLUMN IF NOT EXISTS is_default BOOLEAN DEFAULT FALSE
                """))
                conn.commit()
                logger.info("✅ is_default column added successfully")
                
            except Exception as e:
                logger.warning(f"⚠️ Column might already exist: {e}")
        
        # Create all tables (safe - won't drop existing)
        logger.info("🏗️ Creating/updating all tables...")
        Base.metadata.create_all(engine)
        logger.info("✅ Database schema updated successfully")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Database migration failed: {e}")
        return False

if __name__ == "__main__":
    success = migrate_database()
    if success:
        logger.info("🎉 Migration completed successfully!")
    else:
        logger.error("💥 Migration failed!")
        exit(1)