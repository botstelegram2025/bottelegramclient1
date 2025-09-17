from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager
import logging
import os

from config import Config
from models import Base, User, Client, Subscription, MessageTemplate, MessageLog, SystemSettings

logger = logging.getLogger(__name__)

# OBS: garanta que TODO o código importe "db_service" (singleton) e
# NUNCA faça DatabaseService() em outros módulos.

class DatabaseService:
    def __init__(self):
        db_url = Config.DATABASE_URL

        # Pool controlado para não estourar o limite do Postgres na Railway
        self.engine = create_engine(
            db_url,
            poolclass=QueuePool,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),   # ajuste conforme seu plano
            max_overflow=int(os.getenv("DB_POOL_MAX_OVERFLOW", "0")),  # não estourar
            pool_pre_ping=True,          # valida conexão antes de usar
            pool_recycle=1800,           # recicla a cada 30min
            pool_use_lifo=True,          # reutiliza as conexões mais novas
            echo=False
        )

        # Sessões que não expiram objetos após commit (evita re-loads desnecessários)
        self._SessionFactory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.SessionLocal = scoped_session(self._SessionFactory)

        # Criar/migrar tabelas (executa rápido e é idempotente)
        self.create_tables()

    def create_tables(self):
        """Create all database tables"""
        try:
            Base.metadata.create_all(bind=self.engine)
            self._migrate_existing_tables()
            logger.info("Database tables created successfully")
        except Exception as e:
            logger.error(f"Error creating database tables: {e}")
            raise

    def _migrate_existing_tables(self):
        """Add new columns to existing tables if they don't exist"""
        try:
            with self.engine.connect() as connection:
                # clients.reminder_status
                result = connection.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='clients' AND column_name='reminder_status'
                """))
                if not result.fetchone():
                    logger.info("Adding reminder_status column to clients table")
                    connection.execute(text("""
                        ALTER TABLE clients 
                        ADD COLUMN reminder_status VARCHAR(20) DEFAULT 'pending'
                    """))
                    connection.commit()

                # clients.last_reminder_sent
                result = connection.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='clients' AND column_name='last_reminder_sent'
                """))
                if not result.fetchone():
                    logger.info("Adding last_reminder_sent column to clients table")
                    connection.execute(text("""
                        ALTER TABLE clients 
                        ADD COLUMN last_reminder_sent DATE
                    """))
                    connection.commit()

                # message_templates.is_default
                result = connection.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='message_templates' AND column_name='is_default'
                """))
                if not result.fetchone():
                    logger.info("Adding is_default column to message_templates table")
                    connection.execute(text("""
                        ALTER TABLE message_templates 
                        ADD COLUMN is_default BOOLEAN DEFAULT FALSE
                    """))
                    connection.commit()

                logger.info("Database migration completed successfully")

        except Exception as e:
            # Em ambientes novos é esperado não existir a tabela ainda; mantém warning suave
            logger.warning(f"Migration warning (may be normal for new databases): {e}")

    @contextmanager
    def get_session(self):
        """Get database session with automatic cleanup"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            # scoped_session fecha/retira a sessão do escopo (thread/request)
            session.close()
            self.SessionLocal.remove()

    # --------- Templates default ---------

    def create_default_templates(self, user_id):
        """Create default message templates for a specific user"""
        default_templates = [
            {
                'name': '📅 Lembrete 2 dias antes',
                'template_type': 'reminder_2_days',
                'subject': 'Lembrete: Vencimento em 2 dias',
                'content': '📅 LEMBRETE: 2 DIAS PARA VENCER\n\nOlá {nome}! \n\n📺 Seu plano "{plano}" vencerá em 2 dias.\n📅 Data de vencimento: {vencimento}\n💰 Valor: R$ {valor}\n\nPara renovar, entre em contato conosco.\n\nObrigado! 😊'
            },
            {
                'name': '⏰ Lembrete 1 dia antes',
                'template_type': 'reminder_1_day',
                'subject': 'Lembrete: Vencimento amanhã',
                'content': '⏰ ÚLTIMO AVISO: VENCE AMANHÃ!\n\nOlá {nome}!\n\n📺 Seu plano "{plano}" vence AMANHÃ ({vencimento}).\n💰 Valor: R$ {valor}\n\nNão esqueça de renovar para continuar aproveitando nossos serviços!\n\nRenove agora! 🚀'
            },
            {
                'name': '🚨 Vencimento hoje',
                'template_type': 'reminder_due_date',
                'subject': 'Vencimento hoje',
                'content': '🚨 ATENÇÃO: VENCE HOJE!\n\nOlá {nome}!\n\n📺 Seu plano "{plano}" vence HOJE ({vencimento}).\n💰 Valor: R$ {valor}\n\nRenove agora para não perder o acesso aos nossos serviços.\n\nContate-nos para renovar! 💬'
            },
            {
                'name': '❌ Em atraso',
                'template_type': 'reminder_overdue',
                'subject': 'Plano vencido',
                'content': '❌ PLANO VENCIDO - AÇÃO NECESSÁRIA!\n\nOlá {nome}!\n\n📺 Seu plano "{plano}" venceu em {vencimento}.\n💰 Valor: R$ {valor}\n\n⚠️ Renove o quanto antes para reativar seus serviços.\n\nEstamos aqui para ajudar! 🤝'
            },
            {
                'name': '🎉 Boas-vindas',
                'template_type': 'welcome',
                'subject': 'Bem-vindo!',
                'content': '🎉 SEJA BEM-VINDO(A)!\n\nOlá {nome}!\n\n🌟 Seja muito bem-vindo(a) à nossa família!\n\n📺 Seu plano "{plano}" está ativo e vence em {vencimento}.\n💰 Valor: R$ {valor}\n\nEstamos muito felizes em tê-lo(a) conosco! \n\nAproveite nossos serviços! 🚀'
            },
            {
                'name': '✅ Renovação confirmada',
                'template_type': 'renewal',
                'subject': 'Plano renovado com sucesso!',
                'content': '✅ RENOVAÇÃO CONFIRMADA COM SUCESSO!\n\nOlá {nome}!\n\n🎊 Seu plano "{plano}" foi renovado com sucesso!\n\n📅 Novo vencimento: {vencimento}\n💰 Valor: R$ {valor}\n\nObrigado pela confiança! Continue aproveitando nossos serviços. 🌟'
            }
        ]

        with self.get_session() as session:
            for template_data in default_templates:
                existing = session.query(MessageTemplate).filter_by(
                    template_type=template_data['template_type'],
                    user_id=user_id
                ).first()

                if not existing:
                    template_data['user_id'] = user_id
                    template_data['is_default'] = True
                    template = MessageTemplate(**template_data)
                    session.add(template)
                    logger.info(f"Created default template for user {user_id}: {template_data['name']}")

    def restore_default_templates(self, user_id):
        """Restore all default templates to original state"""
        default_templates = [
            {
                'name': '📅 Lembrete 2 dias antes',
                'template_type': 'reminder_2_days',
                'subject': 'Lembrete: Vencimento em 2 dias',
                'content': '📅 LEMBRETE: 2 DIAS PARA VENCER\n\nOlá {nome}! \n\n📺 Seu plano "{plano}" vencerá em 2 dias.\n📅 Data de vencimento: {vencimento}\n💰 Valor: R$ {valor}\n\nPara renovar, entre em contato conosco.\n\nObrigado! 😊'
            },
            {
                'name': '⏰ Lembrete 1 dia antes',
                'template_type': 'reminder_1_day',
                'subject': 'Lembrete: Vencimento amanhã',
                'content': '⏰ ÚLTIMO AVISO: VENCE AMANHÃ!\n\nOlá {nome}!\n\n📺 Seu plano "{plano}" vence AMANHÃ ({vencimento}).\n💰 Valor: R$ {valor}\n\nNão esqueça de renovar para continuar aproveitando nossos serviços!\n\nRenove agora! 🚀'
            },
            {
                'name': '🚨 Vencimento hoje',
                'template_type': 'reminder_due_date',
                'subject': 'Vencimento hoje',
                'content': '🚨 ATENÇÃO: VENCE HOJE!\n\nOlá {nome}!\n\n📺 Seu plano "{plano}" vence HOJE ({vencimento}).\n💰 Valor: R$ {valor}\n\nRenove agora para não perder o acesso aos nossos serviços.\n\nContate-nos para renovar! 💬'
            },
            {
                'name': '❌ Em atraso',
                'template_type': 'reminder_overdue',
                'subject': 'Plano vencido',
                'content': '❌ PLANO VENCIDO - AÇÃO NECESSÁRIA!\n\nOlá {nome}!\n\n📺 Seu plano "{plano}" venceu em {vencimento}.\n💰 Valor: R$ {valor}\n\n⚠️ Renove o quanto antes para reativar seus serviços.\n\nEstamos aqui para ajudar! 🤝'
            },
            {
                'name': '🎉 Boas-vindas',
                'template_type': 'welcome',
                'subject': 'Bem-vindo!',
                'content': '🎉 SEJA BEM-VINDO(A)!\n\nOlá {nome}!\n\n🌟 Seja muito bem-vindo(a) à nossa família!\n\n📺 Seu plano "{plano}" está ativo e vence em {vencimento}.\n💰 Valor: R$ {valor}\n\nEstamos muito felizes em tê-lo(a) conosco! \n\nAproveite nossos serviços! 🚀'
            },
            {
                'name': '✅ Renovação confirmada',
                'template_type': 'renewal',
                'subject': 'Plano renovado com sucesso!',
                'content': '✅ RENOVAÇÃO CONFIRMADA COM SUCESSO!\n\nOlá {nome}!\n\n🎊 Seu plano "{plano}" foi renovado com sucesso!\n\n📅 Novo vencimento: {vencimento}\n💰 Valor: R$ {valor}\n\nObrigado pela confiança! Continue aproveitando nossos serviços. 🌟'
            }
        ]

        with self.get_session() as session:
            for template_data in default_templates:
                existing = session.query(MessageTemplate).filter_by(
                    template_type=template_data['template_type'],
                    user_id=user_id,
                    is_default=True
                ).first()

                if existing:
                    existing.name = template_data['name']
                    existing.subject = template_data['subject']
                    existing.content = template_data['content']
                    existing.is_active = True
                    logger.info(f"Restored default template for user {user_id}: {template_data['name']}")
                else:
                    template_data['user_id'] = user_id
                    template_data['is_default'] = True
                    template = MessageTemplate(**template_data)
                    session.add(template)
                    logger.info(f"Created missing default template for user {user_id}: {template_data['name']}")

# Global database service instance (singleton)
db_service = DatabaseService()