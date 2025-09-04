import schedule
import time as pytime
import threading
import logging
from datetime import datetime, timedelta
import asyncio
import traceback
import pytz

logger = logging.getLogger(__name__)

SAO_PAULO_TZ = pytz.timezone("America/Sao_Paulo")

class SchedulerService:
    """
    Serviço de agendamento com logs de diagnóstico e retentativa no envio.
    """

    def __init__(self):
        self.is_running = False
        self.scheduler_thread = None
        self._loop = None
        self._loop_thread = None
        self._loop_ready = threading.Event()

    def start(self):
        if self.is_running:
            logger.warning("Scheduler service is already running")
            return
        self.is_running = True
        self._start_event_loop_thread()
        schedule.every().minute.do(self._safe_call, self._check_reminder_times)
        schedule.every().hour.do(self._safe_call, self._check_due_dates)
        schedule.every(2).minutes.do(self._safe_call, self._check_pending_payments)
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.scheduler_thread.start()
        logger.info("✅ Scheduler service started")

    def stop(self):
        self.is_running = False
        schedule.clear()
        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
            self.scheduler_thread = None
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop_thread.join(timeout=5)
        self._loop = None
        self._loop_thread = None
        self._loop_ready.clear()
        logger.info("🛑 Scheduler service stopped")

    def _run_scheduler(self):
        while self.is_running:
            try:
                schedule.run_pending()
            except Exception:
                logger.exception("Error in scheduler run_pending")
            pytime.sleep(1)

    def _start_event_loop_thread(self):
        if self._loop_thread and self._loop_thread.is_alive():
            return
        def _loop_target():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop_ready.set()
            try:
                self._loop.run_forever()
            except Exception:
                logger.exception("Async loop crashed")
            finally:
                pending = asyncio.all_tasks(loop=self._loop)
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                self._loop.close()
        self._loop_thread = threading.Thread(target=_loop_target, daemon=True)
        self._loop_thread.start()
        self._loop_ready.wait(timeout=5)

    def _submit_coro(self, coro, timeout: float | None = 20.0):
        if not self._loop:
            raise RuntimeError("Async loop is not available")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return None if timeout is None else fut.result(timeout=timeout)

    def _safe_call(self, func):
        try:
            func()
        except Exception:
            logger.exception(f"Scheduled function {func.__name__} crashed")

    def _check_reminder_times(self):
        try:
            from services.database_service import DatabaseService
            from models import User, UserScheduleSettings
            brazil_now = datetime.now(SAO_PAULO_TZ)
            current_date = brazil_now.date()
            logger.info(f"⏰ Checking reminder times at {brazil_now.strftime('%H:%M')} (America/Sao_Paulo)")
            db_service = DatabaseService()
            with db_service.get_session() as session:
                rows = (session.query(User, UserScheduleSettings)
                        .join(UserScheduleSettings, User.id == UserScheduleSettings.user_id, isouter=True)
                        .filter(User.is_active.is_(True)).all())
                for user, settings in rows:
                    if not settings:
                        settings = UserScheduleSettings(user_id=user.id,
                                                       morning_reminder_time="09:00",
                                                       daily_report_time="08:00",
                                                       auto_send_enabled=True)
                        session.add(settings)
                        session.commit()
                    if hasattr(settings, "auto_send_enabled") and not settings.auto_send_enabled:
                        logger.info(f"[diag] user={user.id} auto=False → skip")
                        continue
                    try:
                        mh, mm = map(int, (settings.morning_reminder_time or "09:00").split(":"))
                        morning_dt = brazil_now.replace(hour=mh, minute=mm, second=0, microsecond=0)
                    except Exception:
                        logger.error(f"Invalid morning_reminder_time for user {user.id}")
                        morning_dt = brazil_now.replace(hour=9, minute=0, second=0, microsecond=0)
                    try:
                        rh, rm = map(int, (settings.daily_report_time or "08:00").split(":"))
                        report_dt = brazil_now.replace(hour=rh, minute=rm, second=0, microsecond=0)
                    except Exception:
                        logger.error(f"Invalid daily_report_time for user {user.id}")
                        report_dt = brazil_now.replace(hour=8, minute=0, second=0, microsecond=0)
                    will_run_morning = brazil_now >= morning_dt and settings.last_morning_run != current_date
                    will_run_report = brazil_now >= report_dt and settings.last_report_run != current_date
                    logger.info(f"[diag] user={user.id} morning={settings.morning_reminder_time} report={settings.daily_report_time} last_morning_run={settings.last_morning_run} last_report_run={settings.last_report_run}")
                    if will_run_morning:
                        logger.info(f"▶️ Daily reminders for user {user.id}")
                        self._submit_coro(self._process_daily_reminders_for_user(user.id), timeout=60)
                        settings.last_morning_run = current_date
                        session.commit()
                    if will_run_report:
                        logger.info(f"▶️ Daily report for user {user.id}")
                        self._submit_coro(self._process_user_notifications_for_user(user.id), timeout=60)
                        settings.last_report_run = current_date
                        session.commit()
                    self._check_trial_expiration(user, current_date)
        except Exception:
            logger.exception("Error checking reminder times")

    def _check_pending_payments(self):
        logger.info("🔍 Checking pending payments for automatic processing")
        try:
            from services.database_service import DatabaseService
            from services.payment_service import payment_service
            from services.telegram_service import telegram_service
            from models import User, Subscription
            db_service = DatabaseService()
            with db_service.get_session() as session:
                utc_now = datetime.utcnow()
                yesterday = utc_now - timedelta(hours=24)
                pending = (session.query(Subscription)
                           .filter(Subscription.status == "pending", Subscription.created_at >= yesterday)
                           .all())
                logger.info(f"📋 Found {len(pending)} pending payments to check")
                for sub in pending:
                    resp = payment_service.check_payment_status(sub.payment_id)
                    if not resp.get("success"):
                        logger.warning(f"⚠️ Failed to check payment {sub.payment_id}")
                        continue
                    status = resp.get("status")
                    if status == "approved":
                        sub.status = "approved"
                        sub.paid_at = utc_now
                        sub.expires_at = utc_now + timedelta(days=30)
                        user = session.get(User, sub.user_id)
                        if user:
                            user.is_active = True
                            msg = f"""✅ **PAGAMENTO APROVADO AUTOMATICAMENTE!**\n\n💰 **Valor:** R$ {sub.amount:.2f}\n📅 **Aprovado em:** {datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y às %H:%M')}\n\n🎉 **Sua conta foi ativada!**\n• Plano Premium ativo por 30 dias\n• Todos os recursos liberados\n• Próximo vencimento: {sub.expires_at.strftime('%d/%m/%Y')}\n\n🚀 Use o comando /start para acessar todas as funcionalidades!"""
                            self._submit_coro(telegram_service.send_message(user.telegram_id, msg), timeout=10)
                        session.commit()
        except Exception:
            logger.exception("❌ Error checking pending payments")

    def _check_due_dates(self):
        logger.info("Running due date check")
        try:
            from services.database_service import DatabaseService
            from models import Client
            today = datetime.now(SAO_PAULO_TZ).date()
            db_service = DatabaseService()
            with db_service.get_session() as session:
                overdue = session.query(Client).filter(Client.due_date < today, Client.status == "active").all()
                for c in overdue:
                    c.status = "inactive"
                session.commit()
        except Exception:
            logger.exception("Error checking due dates")

    async def _process_daily_reminders_for_user(self, user_id: int):
        from services.database_service import DatabaseService
        from services.whatsapp_service import whatsapp_service
        from models import User, Client
        from sqlalchemy import or_
        db_service = DatabaseService()
        today = datetime.now(SAO_PAULO_TZ).date()
        try:
            with db_service.get_session() as session:
                user = session.query(User).filter_by(id=user_id, is_active=True).first()
                if not user:
                    return
                all_clients = session.query(Client).filter(Client.user_id == user.id, Client.status == "active", Client.auto_reminders_enabled.is_(True), or_(Client.due_date == today + timedelta(days=2), Client.due_date == today + timedelta(days=1), Client.due_date == today, Client.due_date == today - timedelta(days=1))).all()
                groups = {
                    "reminder_2_days": [c for c in all_clients if c.due_date == today + timedelta(days=2)],
                    "reminder_1_day": [c for c in all_clients if c.due_date == today + timedelta(days=1)],
                    "reminder_due_date": [c for c in all_clients if c.due_date == today],
                    "reminder_overdue": [c for c in all_clients if c.due_date == today - timedelta(days=1)]
                }
                for rtype, clients in groups.items():
                    if clients:
                        await self._send_reminders_by_type(session, user, clients, rtype, whatsapp_service)
        except Exception:
            logger.exception(f"Error processing daily reminders for user {user_id}")

    async def _send_reminders_by_type(self, session, user, clients, reminder_type, whatsapp_service):
        from models import MessageTemplate, MessageLog
        template = session.query(MessageTemplate).filter_by(user_id=user.id, template_type=reminder_type, is_active=True).first()
        if not template:
            logger.warning(f"[diag] No template for {reminder_type} (user {user.id})")
            return
        today = datetime.now(SAO_PAULO_TZ).date()
        start_utc = datetime.combine(today, datetime.min.time(), tzinfo=SAO_PAULO_TZ).astimezone(pytz.UTC)
        for client in clients:
            exists = session.query(MessageLog).filter(MessageLog.user_id == user.id, MessageLog.client_id == client.id, MessageLog.template_id == template.id, MessageLog.sent_at >= start_utc, MessageLog.status == 'sent').first()
            if exists:
                logger.info(f"[diag] skip dedup client_id={client.id}")
                continue
            content = self._replace_template_variables(template.content, client)
            status = 'failed'
            resp = None
            for attempt in range(1, 4):
                try:
                    resp = await whatsapp_service.send_message(client.phone_number, content, user.id)
                    if resp.get('success'):
                        status = 'sent'
                        break
                except Exception:
                    logger.exception(f"send_message crashed (attempt {attempt})")
                await asyncio.sleep(attempt)
            from models import MessageLog
            log = MessageLog(user_id=user.id, client_id=client.id, template_id=template.id, template_type=reminder_type, recipient_phone=getattr(client, "phone_number", None), message_content=content, sent_at=datetime.utcnow(), status=status, error_message=None if status=='sent' else str(resp))
            session.add(log)
        session.commit()

    def _replace_template_variables(self, template_content, client):
        price = getattr(client, "plan_price", 0.0)
        due = getattr(client, "due_date", None)
        due_str = due.strftime('%d/%m/%Y') if due else '--/--/----'
        variables = {
            '{nome}': getattr(client, 'name', ''),
            '{plano}': getattr(client, 'plan_name', ''),
            '{valor}': f"{price:.2f}",
            '{vencimento}': due_str,
            '{servidor}': getattr(client, 'server', 'Não definido'),
            '{informacoes_extras}': getattr(client, 'other_info', '')
        }
        result = template_content
        for var, val in variables.items():
            result = result.replace(var, val)
        return result.strip()

    def _check_trial_expiration(self, user, current_date):
        try:
            if not getattr(user, 'is_trial', False):
                return
            created = user.created_at.date() if hasattr(user, 'created_at') else current_date
            trial_end = created + timedelta(days=7)
            days_until = (trial_end - current_date).days
            if days_until <= 0 and getattr(user, 'is_active', False):
                from services.database_service import DatabaseService
                db_service = DatabaseService()
                with db_service.get_session() as session:
                    db_user = session.get(type(user), user.id)
                    if db_user:
                        db_user.is_active = False
                        session.commit()
                self._submit_coro(self._send_payment_notification(user.telegram_id), timeout=15)
            elif days_until == 1:
                self._submit_coro(self._send_trial_reminder(user.telegram_id, days_until), timeout=15)
        except Exception:
            logger.exception(f"Error checking trial expiration for user {getattr(user, 'id', '?')}")

    async def _send_payment_notification(self, telegram_id):
        from services.telegram_service import telegram_service
        msg = ("""⚠️ **Seu período de teste expirou!**\n\nSeu teste gratuito de 7 dias chegou ao fim. Para continuar usando todas as funcionalidades do bot, você precisa ativar a assinatura mensal.\n\n💰 **Assinatura:** R$ 20,00/mês\n✅ **Inclui:**\n• Gestão ilimitada de clientes\n• Lembretes automáticos via WhatsApp\n• Controle de vencimentos\n• Relatórios detalhados\n• Suporte prioritário\n\n🔗 Use o comando /start para assinar e reativar sua conta!""")
        await telegram_service.send_notification(telegram_id, msg)

    async def _send_trial_reminder(self, telegram_id, days_left):
        from services.telegram_service import telegram_service
        msg = (f"""⏰ **Lembrete: Seu teste expira em {days_left} dia(s)!**\n\nSeu período gratuito está chegando ao fim. Não perca o acesso às suas funcionalidades!\n\n💰 **Assinatura:** R$ 20,00/mês\n🎯 **Mantenha:**\n• Todos os seus clientes cadastrados\n• Lembretes automáticos configurados\n• Histórico de mensagens\n\nPara assinar e garantir a continuidade, use o comando /start quando seu teste expirar.""")
        await telegram_service.send_notification(telegram_id, msg)

scheduler_service = SchedulerService()
