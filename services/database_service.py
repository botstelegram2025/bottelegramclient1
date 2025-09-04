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
    Serviço de agendamento com:
    - Loop asyncio dedicado em thread separada (para rodar corrotinas de envio)
    - Polling do schedule a cada 1s (para não perder janelas de 60s)
    - Datas/horários sempre em America/Sao_Paulo
    - Execução única por dia (last_*_run) para lembretes e relatórios
    - Checagem periódica de pagamentos
    - Correções de inconsistências (template_id vs template_type, timezone, event loop)
    - Logs [diag] para decisão por usuário e fluxo de envio
    - Retentativa com backoff no envio WhatsApp
    """

    def __init__(self):
        self.is_running = False
        self.scheduler_thread: threading.Thread | None = None

        # --- Loop asyncio dedicado ---
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_ready = threading.Event()

    # ------------------ Lifecycle ------------------
    def start(self):
        """Inicia o serviço de agendamento e o loop asyncio dedicado."""
        if self.is_running:
            logger.warning("Scheduler service is already running")
            return

        self.is_running = True

        # 1) Inicia loop asyncio em thread dedicada
        self._start_event_loop_thread()

        # 2) Agenda os jobs (polling leve a cada 1 min / 2 min / 1 hora)
        schedule.every().minute.do(self._safe_call, self._check_reminder_times)
        schedule.every().hour.do(self._safe_call, self._check_due_dates)
        schedule.every(2).minutes.do(self._safe_call, self._check_pending_payments)

        # 3) Thread do scheduler (poll de 1s para não perder janelas de execução)
        self.scheduler_thread = threading.Thread(target=self._run_scheduler, daemon=True, name="scheduler-thread")
        self.scheduler_thread.start()

        logger.info("✅ Scheduler service started")

    def stop(self):
        """Interrompe o serviço e o loop asyncio dedicado."""
        self.is_running = False
        schedule.clear()

        if self.scheduler_thread:
            self.scheduler_thread.join(timeout=5)
            self.scheduler_thread = None

        # para o loop asyncio
        if self._loop and self._loop.is_running():
            def _stop_loop():
                self._loop.stop()
            self._loop.call_soon_threadsafe(_stop_loop)
            self._loop_thread.join(timeout=5)

        self._loop = None
        self._loop_thread = None
        self._loop_ready.clear()

        logger.info("🛑 Scheduler service stopped")

    def _run_scheduler(self):
        """Loop que dispara schedule.run_pending() a cada 1s."""
        while self.is_running:
            try:
                schedule.run_pending()
            except Exception:
                logger.exception("Error in scheduler run_pending")
            finally:
                pytime.sleep(1)  # 1 segundo para não perder janelas de execução

    # ------------------ Async loop thread ------------------
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
                try:
                    pending = asyncio.all_tasks(loop=self._loop)
                    for t in pending:
                        t.cancel()
                    if pending:
                        self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                self._loop.close()

        self._loop_thread = threading.Thread(target=_loop_target, daemon=True, name="scheduler-asyncio-loop")
        self._loop_thread.start()
        # espera o loop estar pronto
        self._loop_ready.wait(timeout=5)
        if not self._loop or not self._loop.is_running():
            logger.info("Async loop thread started (waiting for first run_forever tick)")

    def _submit_coro(self, coro, timeout: float | None = 20.0):
        """Submete uma corrotina para o loop dedicado e opcionalmente aguarda resultado."""
        if not self._loop:
            raise RuntimeError("Async loop is not available")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        if timeout is None:
            return None
        return fut.result(timeout=timeout)

    def _safe_call(self, func):
        try:
            func()
        except Exception:
            logger.exception(f"Scheduled function {func.__name__} crashed")

    # ------------------ Jobs ------------------
    def _check_reminder_times(self):
        """Verifica horários do usuário (manhã/relatório) e dispara uma única vez por dia.
        Usa timezone America/Sao_Paulo e tolera execuções perdidas (se o serviço reiniciou).
        Inclui logs [diag] para entender a decisão por usuário.
        """
        try:
            from services.database_service import DatabaseService
            from models import User, UserScheduleSettings

            brazil_now = datetime.now(SAO_PAULO_TZ)
            current_time_str = brazil_now.strftime("%H:%M")
            current_date = brazil_now.date()

            logger.info(f"⏰ Checking reminder times at {current_time_str} (America/Sao_Paulo)")

            db_service = DatabaseService()
            with db_service.get_session() as session:
                rows = (
                    session.query(User, UserScheduleSettings)
                    .join(UserScheduleSettings, User.id == UserScheduleSettings.user_id, isouter=True)
                    .filter(User.is_active.is_(True))
                    .all()
                )

                for user, settings in rows:
                    # cria defaults
                    if not settings:
                        settings = UserScheduleSettings(
                            user_id=user.id,
                            morning_reminder_time="09:00",
                            daily_report_time="08:00",
                            auto_send_enabled=True,
                        )
                        session.add(settings)
                        session.commit()

                    if hasattr(settings, "auto_send_enabled") and not settings.auto_send_enabled:
                        logger.info(f"[diag] user={user.id} auto=False → skip")
                        continue

                    # Parse dos horários
                    try:
                        morning_str = settings.morning_reminder_time or "09:00"
                        mh, mm = map(int, morning_str.split(":"))
                        morning_dt = brazil_now.replace(hour=mh, minute=mm, second=0, microsecond=0)
                    except Exception:
                        logger.error(f"Invalid morning_reminder_time for user {user.id}: {settings.morning_reminder_time}")
                        morning_dt = brazil_now.replace(hour=9, minute=0, second=0, microsecond=0)

                    try:
                        report_str = settings.daily_report_time or "08:00"
                        rh, rm = map(int, report_str.split(":"))
                        report_dt = brazil_now.replace(hour=rh, minute=rm, second=0, microsecond=0)
                    except Exception:
                        logger.error(f"Invalid daily_report_time for user {user.id}: {settings.daily_report_time}")
                        report_dt = brazil_now.replace(hour=8, minute=0, second=0, microsecond=0)

                    will_run_morning = brazil_now >= morning_dt and settings.last_morning_run != current_date
                    will_run_report = brazil_now >= report_dt and settings.last_report_run != current_date

                    logger.info(
                        f"[diag] user={user.id} auto={settings.auto_send_enabled} "
                        f"morning={morning_str} report={report_str} "
                        f"last_run(m)={settings.last_morning_run} last_run(r)={settings.last_report_run}"
                    )
                    logger.info(
                        f"[diag] now={brazil_now.strftime('%Y-%m-%d %H:%M')} "
                        f"will_run_morning={will_run_morning} will_run_report={will_run_report}"
                    )

                    # --- Manhã (lembretes automáticos) ---
                    if will_run_morning:
                        logger.info(f"▶️ Daily reminders for user {user.id}")
                        try:
                            self._submit_coro(self._process_daily_reminders_for_user(user.id), timeout=60)
                            settings.last_morning_run = current_date
                            session.commit()
                        except Exception:
                            logger.exception(f"Error processing daily reminders for user {user.id}")

                    # --- Relatório diário (notificações no Telegram) ---
                    if will_run_report:
                        logger.info(f"▶️ Daily report for user {user.id}")
                        try:
                            self._submit_coro(self._process_user_notifications_for_user(user.id), timeout=60)
                            settings.last_report_run = current_date
                            session.commit()
                        except Exception:
                            logger.exception(f"Error processing daily report for user {user.id}")

                    # Trial expiração (usa current_date local)
                    try:
                        self._check_trial_expiration(user, current_date)
                    except Exception:
                        logger.exception(f"Error check_trial_expiration for user {user.id}")

        except Exception:
            logger.exception("Error checking reminder times")

    def _check_pending_payments(self):
        """Checa pagamentos pendentes (últimas 24h) e atualiza automaticamente aprovados."""
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

                pending = (
                    session.query(Subscription)
                    .filter(Subscription.status == "pending", Subscription.created_at >= yesterday)
                    .all()
                )
                logger.info(f"📋 Found {len(pending)} pending payments to check")

                approved_count = 0
                pending_count = 0

                for sub in pending:
                    resp = payment_service.check_payment_status(sub.payment_id)
                    if not resp.get("success"):
                        logger.warning(f"⚠️ Failed to check payment {sub.payment_id}: {resp.get('error')}")
                        continue

                    status = resp.get("status")
                    detail = resp.get("status_detail", "")
                    logger.info(f"📊 Payment {sub.payment_id} status: {status} ({detail})")

                    if status == "approved":
                        approved_count += 1
                        old = sub.status
                        sub.status = "approved"
                        sub.paid_at = utc_now
                        sub.expires_at = utc_now + timedelta(days=30)

                        user = session.get(User, sub.user_id)
                        if user:
                            user.is_trial = False
                            user.is_active = True
                            user.last_payment_date = utc_now
                            user.next_due_date = sub.expires_at

                            # notifica por telegram (async)
                            msg = (
                                "✅ **PAGAMENTO APROVADO AUTOMATICAMENTE!**

"
                                f"💰 **Valor:** R$ {sub.amount:.2f}
"
                                f"📅 **Aprovado em:** {datetime.now(SAO_PAULO_TZ).strftime('%d/%m/%Y às %H:%M')}

"
                                "🎉 **Sua conta foi ativada!**
• Plano Premium ativo por 30 dias
• Todos os recursos liberados
"
                                f"• Próximo vencimento: {sub.expires_at.astimezone(pytz.UTC).strftime('%d/%m/%Y')}

"
                                "🚀 Use o comando /start para acessar todas as funcionalidades!"
                            )
                            try:
                                self._submit_coro(telegram_service.send_message(user.telegram_id, msg), timeout=10)
                            except Exception:
                                logger.exception("Error sending approval notification")

                        session.commit()
                        logger.info(f"💾 Payment {sub.payment_id} updated: {old} → approved")

                    elif status == "pending":
                        pending_count += 1
                    elif status in ("rejected", "cancelled"):
                        sub.status = status
                        session.commit()

                # expira pendentes muito antigos (>24h)
                old_pending = (
                    session.query(Subscription)
                    .filter(Subscription.status == "pending", Subscription.created_at < yesterday)
                    .all()
                )
                for op in old_pending:
                    op.status = "expired"
                if old_pending:
                    session.commit()
                    logger.info(f"🧹 Cleaned up {len(old_pending)} expired payments")

                if pending:
                    logger.info(
                        f"📊 Payment check summary: {approved_count} approved, {pending_count} still pending, {len(pending) - approved_count - pending_count} other status"
                    )
        except Exception:
            logger.exception("❌ Error checking pending payments")

    def _check_due_dates(self):
        """Marca clientes em atraso como inativos (usa data local)."""
        logger.info("Running due date check")
        try:
            from services.database_service import DatabaseService
            from models import Client

            today_local = datetime.now(SAO_PAULO_TZ).date()
            db_service = DatabaseService()
            with db_service.get_session() as session:
                overdue = (
                    session.query(Client)
                    .filter(Client.due_date < today_local, Client.status == "active")
                    .all()
                )
                for c in overdue:
                    c.status = "inactive"
                    logger.info(f"Marked client {c.name} as inactive (overdue)")
                session.commit()
        except Exception:
            logger.exception("Error checking due dates")

    # ------------------ Notificações diárias (Telegram) ------------------
    async def _process_user_notifications(self):
        from services.database_service import DatabaseService
        from services.telegram_service import telegram_service
        from models import Client, User

        db_service = DatabaseService()
        today = datetime.now(SAO_PAULO_TZ).date()
        tomorrow = today + timedelta(days=1)
        after = today + timedelta(days=2)

        try:
            with db_service.get_session() as session:
                users = session.query(User).filter_by(is_active=True).all()
                for user in users:
                    overdue = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status="active")
                        .filter(Client.due_date < today)
                        .all()
                    )
                    due_today = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status="active", due_date=today)
                        .all()
                    )
                    due_tomorrow = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status="active", due_date=tomorrow)
                        .all()
                    )
                    due_after = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status="active", due_date=after)
                        .all()
                    )

                    if overdue or due_today or due_tomorrow or due_after:
                        text = self._build_notification_message(overdue, due_today, due_tomorrow, due_after)
                        ok = await telegram_service.send_notification(user.telegram_id, text)
                        if ok:
                            logger.info(f"Sent daily notification to user {user.telegram_id}")
                        else:
                            logger.error(f"Failed to send notification to user {user.telegram_id}")
        except Exception:
            logger.exception("Error processing user notifications")

    def _build_notification_message(self, overdue_clients, due_today, due_tomorrow, due_day_after):
        message = "📅 **Relatório Diário de Vencimentos**

"
        today = datetime.now(SAO_PAULO_TZ).date()

        if overdue_clients:
            message += f"🔴 **{len(overdue_clients)} cliente(s) em atraso:**
"
            for c in overdue_clients[:5]:
                days_over = (today - c.due_date).days if c.due_date else "?"
                message += f"• {c.name} - {days_over} dia(s) de atraso
"
            if len(overdue_clients) > 5:
                message += f"• ... e mais {len(overdue_clients) - 5} cliente(s)
"
            message += "
"

        if due_today:
            message += f"🟡 **{len(due_today)} cliente(s) vencem hoje:**
"
            for c in due_today[:5]:
                message += f"• {c.name} - R$ {getattr(c, 'plan_price', 0.0):.2f}
"
            if len(due_today) > 5:
                message += f"• ... e mais {len(due_today) - 5} cliente(s)
"
            message += "
"

        if due_tomorrow:
            message += f"🟠 **{len(due_tomorrow)} cliente(s) vencem amanhã:**
"
            for c in due_tomorrow[:5]:
                message += f"• {c.name} - R$ {getattr(c, 'plan_price', 0.0):.2f}
"
            if len(due_tomorrow) > 5:
                message += f"• ... e mais {len(due_tomorrow) - 5} cliente(s)
"
            message += "
"

        if due_day_after:
            message += f"🔵 **{len(due_day_after)} cliente(s) vencem em 2 dias:**
"
            for c in due_day_after[:5]:
                message += f"• {c.name} - R$ {getattr(c, 'plan_price', 0.0):.2f}
"
            if len(due_day_after) > 5:
                message += f"• ... e mais {len(due_day_after) - 5} cliente(s)
"
            message += "
"

        message += "📱 Use o menu **👥 Clientes** para gerenciar seus clientes."
        return message

    # ------------------ Lembretes (WhatsApp) ------------------
    async def _process_daily_reminders_for_user(self, user_id: int):
        """Envia lembretes para 2 dias antes, 1 dia antes, hoje e 1 dia após o vencimento."""
        from services.database_service import DatabaseService
        from services.whatsapp_service import whatsapp_service  # singleton com .send_message(...)
        from models import User, Client
        from sqlalchemy import or_

        db_service = DatabaseService()
        today = datetime.now(SAO_PAULO_TZ).date()

        try:
            with db_service.get_session() as session:
                user = session.query(User).filter_by(id=user_id, is_active=True).first()
                if not user:
                    logger.warning(f"User {user_id} not found or inactive")
                    return

                all_clients = (
                    session.query(Client)
                    .filter(
                        Client.user_id == user.id,
                        Client.status == "active",
                        Client.auto_reminders_enabled.is_(True),
                        or_(
                            Client.due_date == today + timedelta(days=2),
                            Client.due_date == today + timedelta(days=1),
                            Client.due_date == today,
                            Client.due_date == today - timedelta(days=1),
                        ),
                    )
                    .all()
                )

                groups = {
                    "reminder_2_days": [c for c in all_clients if c.due_date == today + timedelta(days=2)],
                    "reminder_1_day": [c for c in all_clients if c.due_date == today + timedelta(days=1)],
                    "reminder_due_date": [c for c in all_clients if c.due_date == today],
                    "reminder_overdue": [c for c in all_clients if c.due_date == today - timedelta(days=1)],
                }

                for rtype, clients in groups.items():
                    if clients:
                        await self._send_reminders_by_type(session, user, clients, rtype, whatsapp_service)
        except Exception:
            logger.exception(f"Error processing daily reminders for user {user_id}")

    async def _send_reminders_by_type(self, session, user, clients, reminder_type, whatsapp_service):
        from models import MessageTemplate, MessageLog
        from sqlalchemy import and_

        template = (
            session.query(MessageTemplate)
            .filter_by(user_id=user.id, template_type=reminder_type, is_active=True)
            .first()
        )
        if not template:
            logger.warning(f"[diag] No template for {reminder_type} (user {user.id})")
            return

        today_local = datetime.now(SAO_PAULO_TZ).date()
        start_utc = datetime.combine(today_local, datetime.min.time(), tzinfo=SAO_PAULO_TZ).astimezone(pytz.UTC)

        sent_count = 0
        failed_count = 0
        skipped_dedup = 0

        for client in clients:
            # evita duplicidade no dia (por template_id)
            exists = (
                session.query(MessageLog)
                .filter(
                    MessageLog.user_id == user.id,
                    MessageLog.client_id == client.id,
                    MessageLog.template_id == template.id,
                    MessageLog.sent_at >= start_utc,
                    MessageLog.status == 'sent'
                )
                .first()
            )
            if exists:
                skipped_dedup += 1
                logger.info(f"[diag] skip dedup (day+template) client_id={client.id} phone={getattr(client,'phone_number',None)}")
                continue

            message_content = self._replace_template_variables(template.content, client)

            # Retentativa com backoff simples: 3 tentativas
            attempts = 3
            resp = None
            for attempt in range(1, attempts + 1):
                logger.info(f"[diag] attempt={attempt}/{attempts} send to client_id={client.id} phone={getattr(client,'phone_number',None)} type={reminder_type}")
                try:
                    try:
                        resp = await whatsapp_service.send_message(client.phone_number, message_content, user.id)
                    except TypeError:
                        resp = whatsapp_service.send_message(client.phone_number, message_content, user.id)

                    if isinstance(resp, dict) and resp.get('success'):
                        break
                except Exception:
                    logger.exception(f"send_message crashed (attempt {attempt})")
                await asyncio.sleep(attempt)  # backoff 1s, 2s, 3s

            status = "sent" if isinstance(resp, dict) and resp.get("success") else "failed"
            error_msg = None if status == "sent" else (resp.get("error") if isinstance(resp, dict) else "send failed")

            log = MessageLog(
                user_id=user.id,
                client_id=client.id,
                template_id=template.id,
                template_type=reminder_type,
                recipient_phone=getattr(client, "phone_number", None),
                message_content=message_content,
                sent_at=datetime.utcnow(),  # UTC
                status=status,
                error_message=error_msg,
            )
            session.add(log)

            if status == 'sent':
                sent_count += 1
                logger.info(f"[diag] sent client_id={client.id} resp={resp}")
            else:
                failed_count += 1
                logger.warning(f"[diag] failed client_id={client.id} error={error_msg}")

        session.commit()
        logger.info(f"{reminder_type}: sent={sent_count}, failed={failed_count}, skipped_dedup={skipped_dedup}, total={len(clients)} for user {user.id}")

    # ------------------ Auxiliares ------------------
    def _replace_template_variables(self, template_content, client):
        safe_price = getattr(client, "plan_price", 0.0) or 0.0
        server = getattr(client, "server", None) or "Não definido"
        extras = getattr(client, "other_info", None) or ""
        due = getattr(client, "due_date", None)
        due_str = due.strftime("%d/%m/%Y") if due else "--/--/----"

        variables = {
            "{nome}": getattr(client, "name", "Cliente"),
            "{plano}": getattr(client, "plan_name", "Plano"),
            "{valor}": f"{safe_price:.2f}",
            "{vencimento}": due_str,
            "{servidor}": server,
            "{informacoes_extras}": extras,
        }

        result = template_content or ""
        for var, value in variables.items():
            result = result.replace(var, str(value))

        while "


" in result:
            result = result.replace("


", "

")
        return result.strip()

    # ------------------ Trial ------------------
    def _check_trial_expiration(self, user, current_date):
        try:
            if not getattr(user, "is_trial", False):
                return
            created = user.created_at.date() if hasattr(user, "created_at") else current_date
            trial_end = created + timedelta(days=7)
            days_until = (trial_end - current_date).days

            if days_until <= 0 and getattr(user, "is_active", False):
                logger.info(f"Trial expired for user {user.id}")
                from services.database_service import DatabaseService
                db_service = DatabaseService()
                with db_service.get_session() as session:
                    db_user = session.get(type(user), user.id)
                    if db_user:
                        db_user.is_active = False
                        session.commit()
                try:
                    self._submit_coro(self._send_payment_notification(user.telegram_id), timeout=15)
                except Exception:
                    logger.exception("Error sending payment notification")
            elif days_until == 1:
                try:
                    self._submit_coro(self._send_trial_reminder(user.telegram_id, days_until), timeout=15)
                except Exception:
                    logger.exception("Error sending trial reminder")
        except Exception:
            logger.exception(f"Error checking trial expiration for user {getattr(user, 'id', '?')}")

    async def _send_payment_notification(self, telegram_id):
        from services.telegram_service import telegram_service
        message = (
            "
⚠️ **Seu período de teste expirou!**

"
            "Seu teste gratuito de 7 dias chegou ao fim. Para continuar usando todas as funcionalidades do bot, você precisa ativar a assinatura mensal.

"
            "💰 **Assinatura:** R$ 20,00/mês
"
            "✅ **Inclui:**
• Gestão ilimitada de clientes
• Lembretes automáticos via WhatsApp  
• Controle de vencimentos
• Relatórios detalhados
• Suporte prioritário

"
            "🔗 Use o comando /start para assinar e reativar sua conta!
"
        )
        await telegram_service.send_notification(telegram_id, message)

    async def _send_trial_reminder(self, telegram_id, days_left):
        from services.telegram_service import telegram_service
        message = (
            f"
⏰ **Lembrete: Seu teste expira em {days_left} dia(s)!**

"
            "Seu período gratuito está chegando ao fim. Não perca o acesso às suas funcionalidades!

"
            "💰 **Assinatura:** R$ 20,00/mês
"
            "🎯 **Mantenha:**
• Todos os seus clientes cadastrados
• Lembretes automáticos configurados
• Histórico de mensagens

"
            "Para assinar e garantir a continuidade, use o comando /start quando seu teste expirar.
"
        )
        await telegram_service.send_notification(telegram_id, message)

# Instância global
scheduler_service = SchedulerService()
