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

# Conjunto dos tipos CANÔNICOS do sistema (sem prefixo)
CANONICAL_BUCKETS = {
    "reminder_2_days",
    "reminder_1_day",
    "reminder_due_date",
    "reminder_overdue",
    "welcome",
    "renewal",
    "custom",
}

class DatabaseService:
    def __init__(self):
        db_url = Config.DATABASE_URL

        # Pool controlado para não estourar o limite do Postgres na Railway
        self.engine = create_engine(
            db_url,
            poolclass=QueuePool,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_POOL_MAX_OVERFLOW", "0")),
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_use_lifo=True,
            echo=False
        )

        self._SessionFactory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.SessionLocal = scoped_session(self._SessionFactory)

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
        """
        Add/ajusta colunas e constraints e normaliza tipos de templates:
        - Garante clients.reminder_status e clients.last_reminder_sent
        - Garante message_templates.is_default
        - Cria UNIQUE (user_id, template_type)
        - Converte templates de usuário canônicos para 'user_<canônico>'
          sem tocar nos padrões (is_default = TRUE)
        - Desativa duplicatas quando já existir a versão 'user_<...>' do mesmo usuário
        """
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

                # UNIQUE (user_id, template_type)
                result = connection.execute(text("""
                    SELECT 1
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.constraint_column_usage ccu
                      ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.table_name = 'message_templates'
                      AND tc.constraint_type = 'UNIQUE'
                      AND tc.constraint_name = 'uq_user_template_type'
                """))
                if not result.fetchone():
                    logger.info("Adding unique constraint uq_user_template_type on message_templates(user_id, template_type)")
                    try:
                        connection.execute(text("""
                            ALTER TABLE message_templates
                            ADD CONSTRAINT uq_user_template_type UNIQUE (user_id, template_type)
                        """))
                        connection.commit()
                    except Exception as e:
                        logger.warning(f"Could not create unique constraint yet: {e}")

                # ---------- Normalização: prefixa user_ quando necessário ----------
                # buckets principais usados pelo agendador + RENEWAL
                canonical_with_renewal = ('reminder_2_days','reminder_1_day','reminder_due_date','reminder_overdue','renewal')

                # 1) Desativar duplicatas: se já existe user_<tipo>, desativa a versão canônica do usuário
                logger.info("Deactivating duplicate canonical user templates (including renewal)")
                connection.execute(text(f"""
                    UPDATE message_templates t
                    SET is_active = FALSE
                    FROM message_templates u
                    WHERE t.user_id = u.user_id
                      AND t.is_default = FALSE
                      AND t.template_type IN {canonical_with_renewal}
                      AND u.template_type = ('user_' || t.template_type)
                """))
                connection.commit()

                # 2) Renomear as que restarem: canônicas de usuário -> user_<canônico> (inclui renewal)
                logger.info("Renaming canonical user templates to user_ prefixed types (including renewal)")
                connection.execute(text(f"""
                    UPDATE message_templates
                    SET template_type = ('user_' || template_type)
                    WHERE is_default = FALSE
                      AND template_type IN {canonical_with_renewal}
                """))
                connection.commit()

                # 3) Recria UNIQUE se tiver falhado antes
                result = connection.execute(text("""
                    SELECT 1
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.constraint_column_usage ccu
                      ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.table_name = 'message_templates'
                      AND tc.constraint_type = 'UNIQUE'
                      AND tc.constraint_name = 'uq_user_template_type'
                """))
                if not result.fetchone():
                    try:
                        connection.execute(text("""
                            ALTER TABLE message_templates
                            ADD CONSTRAINT uq_user_template_type UNIQUE (user_id, template_type)
                        """))
                        connection.commit()
                        logger.info("Unique constraint uq_user_template_type created")
                    except Exception as e:
                        logger.warning(f"Unique constraint still not created: {e}")

                logger.info("Database migration completed successfully")

        except Exception as e:
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
            session.close()
            self.SessionLocal.remove()

    # --------- Templates default ---------

    def create_default_templates(self, user_id):
        """Create default message templates for a specific user (canônicas e is_default=True)"""
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
