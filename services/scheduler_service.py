# services/scheduler_service.py
import schedule
import time as pytime
import threading
import logging
from datetime import datetime, timedelta
import asyncio
import queue
import math

import pytz

logger = logging.getLogger(__name__)

SAO_PAULO_TZ = pytz.timezone("America/Sao_Paulo")

class SchedulerService:
    def __init__(self):
        self.is_running = False
        self.thread = None
        self.loop: asyncio.AbstractEventLoop | None = None

        # --- Fila de mensagens (para WhatsApp/Telegram) ---
        self.msg_queue: queue.Queue = queue.Queue(maxsize=2000)
        self.worker_thread = None
        self.worker_stop = threading.Event()

        # Rate limit básico (mensagens/seg)
        self.rate_per_sec = 8            # ajuste se necessário
        self.min_sleep_between_msgs = 1.0 / max(self.rate_per_sec, 1)

        # Retries
        self.max_retries = 3
        self.base_backoff = 2.0  # s

    # ------------- Lifecycle -------------

    def start(self):
        """Start the scheduler service"""
        if self.is_running:
            logger.warning("Scheduler service is already running")
            return

        # Cria o loop asyncio dedicado do scheduler (rodará no mesmo thread do scheduler)
        self.loop = asyncio.new_event_loop()

        # Jobs base
        schedule.clear()
        # Checa horários de lembretes e relatórios a cada minuto (com catch-up)
        schedule.every().minute.do(self._check_reminder_times)
        # Checagem de datas de vencimento por hora
        schedule.every().hour.do(self._check_due_dates)
        # Checagem de pagamentos a cada 2 minutos
        schedule.every(2).minutes.do(self._check_pending_payments)

        # Threads
        self.is_running = True
        self.worker_stop.clear()
        self.worker_thread = threading.Thread(
            target=self._message_worker, name="MsgWorker", daemon=True
        )
        self.worker_thread.start()

        self.thread = threading.Thread(
            target=self._run_scheduler, name="SchedulerThread", daemon=True
        )
        self.thread.start()

        logger.info("✅ Scheduler service started (thread + asyncio loop + queue)")

    def stop(self):
        """Stop the scheduler service"""
        self.is_running = False
        try:
            schedule.clear()
        except Exception:
            pass

        # Para o worker
        self.worker_stop.set()
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=10)

        # Para o loop asyncio
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=10)

        # Fecha o loop
        try:
            if self.loop:
                self.loop.close()
        except Exception:
            pass
        self.loop = None

        logger.info("🛑 Scheduler service stopped")

    # ------------- Threads -------------

    def _run_scheduler(self):
        """
        Roda o schedule a cada 1s (não 60s!) e mantém um event loop asyncio dedicado
        no mesmo thread para executar corrotinas via run_coroutine_threadsafe.
        """
        assert self.loop is not None, "Event loop not created"
        asyncio.set_event_loop(self.loop)

        # Inicia o loop asyncio em paralelo ao polling do schedule
        loop_task = threading.Thread(
            target=self.loop.run_forever, name="SchedulerAsyncioLoop", daemon=True
        )
        loop_task.start()

        try:
            while self.is_running:
                try:
                    schedule.run_pending()
                except Exception as e:
                    logger.exception(f"Error in schedule.run_pending(): {e}")
                pytime.sleep(1)  # 1s evita drift e perdas de janelas
        finally:
            try:
                if self.loop and self.loop.is_running():
                    self.loop.call_soon_threadsafe(self.loop.stop)
            except Exception:
                pass

    def _message_worker(self):
        """
        Worker da fila de mensagens: consome tarefas, aplica rate-limit e retries.
        Uma tarefa da fila é um dict com:
        {
          "type": "whatsapp"|"telegram",
          "payload": { ... },
          "user_id": int,
          "meta": {...}
        }
        """
        last_sent = 0.0
        while not self.worker_stop.is_set():
            try:
                task = self.msg_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            # Rate limit básico
            delta = pytime.time() - last_sent
            if delta < self.min_sleep_between_msgs:
                pytime.sleep(self.min_sleep_between_msgs - delta)

            ok = False
            error_msg = None
            for attempt in range(self.max_retries):
                try:
                    if task["type"] == "whatsapp":
                        ok = self._send_whatsapp_sync(task["payload"])
                    elif task["type"] == "telegram":
                        ok = self._send_telegram_sync(task["payload"])
                    else:
                        error_msg = f"Unknown task type {task['type']}"
                        ok = False

                    if ok:
                        break
                except Exception as e:
                    error_msg = str(e)
                    ok = False

                # Backoff
                sleep_s = self.base_backoff * (2 ** attempt)  # 2, 4, 8...
                pytime.sleep(sleep_s)

            # Pós-envio: logar em MessageLog se houver contexto
            try:
                self._finalize_queue_task_logging(task, ok, error_msg)
            except Exception:
                logger.exception("Failed to write MessageLog for queue task")

            last_sent = pytime.time()
            self.msg_queue.task_done()

    # ------------- Envios síncronos usados pelo worker -------------

    def _send_whatsapp_sync(self, payload: dict) -> bool:
        """
        payload esperado:
        {
           "to": "+55...",
           "content": "mensagem final",
           "user_id": 123
        }
        """
        from services.whatsapp_service import whatsapp_service
        res = whatsapp_service.send_message(
            payload["to"], payload["content"], payload.get("user_id")
        )
        if isinstance(res, dict):
            return res.get("success", False)
        return bool(res)

    def _send_telegram_sync(self, payload: dict) -> bool:
        """
        payload esperado:
        {
           "chat_id": "...",
           "text": "mensagem",
           "parse_mode": "Markdown" | None
        }
        """
        from services.telegram_service import telegram_service

        # telegram_service possivelmente é assíncrono
        fut = asyncio.run_coroutine_threadsafe(
            telegram_service.send_message(
                payload["chat_id"], payload["text"], payload.get("parse_mode")
            ),
            self._get_loop(),
        )
        try:
            result = fut.result(timeout=20)
            return bool(result)
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def _finalize_queue_task_logging(self, task: dict, ok: bool, error_msg: str | None):
        """Registra em MessageLog quando aplicável (apenas para tarefas que vêm de reminders)."""
        if not task.get("meta") or not task["meta"].get("log_ctx"):
            return

        from services.database_service import DatabaseService
        from models import MessageLog
        from datetime import datetime as dt

        ctx = task["meta"]["log_ctx"]
        db = DatabaseService()
        with db.get_session() as session:
            log = MessageLog(
                user_id=ctx["user_id"],
                client_id=ctx.get("client_id"),
                template_id=ctx.get("template_id"),
                template_type=ctx.get("template_type"),
                recipient_phone=ctx.get("recipient_phone"),
                message_content=ctx.get("message_content", ""),
                sent_at=dt.now(SAO_PAULO_TZ).astimezone(pytz.utc).replace(tzinfo=None),
                status="sent" if ok else "failed",
                error_message=None if ok else (error_msg or "failed"),
            )
            session.add(log)
            session.commit()

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self.loop is None:
            # fallback defensivo
            self.loop = asyncio.new_event_loop()
            threading.Thread(
                target=self.loop.run_forever, daemon=True, name="SchedulerAsyncLoopFallback"
            ).start()
        return self.loop

    # ------------- Helpers de data/hora -------------

    @staticmethod
    def _now_sp():
        return datetime.now(SAO_PAULO_TZ)

    @staticmethod
    def _today_sp():
        return SchedulerService._now_sp().date()

    # ------------- Jobs agendados -------------

    def _check_reminder_times(self):
        """
        Executa diariamente:
        - Lembretes automáticos (2 dias antes, 1 dia antes, vence hoje, 1 dia depois)
        - Relatório diário por Telegram
        Com “catch-up” baseado em last_morning_run / last_report_run.
        """
        try:
            from services.database_service import DatabaseService
            from models import User, UserScheduleSettings

            now = self._now_sp()
            now_time = now.time()
            today = now.date()

            # helpers locais
            def _as_date(value):
                if not value:
                    return None
                return value.date() if isinstance(value, datetime) else value

            def _set_run_field(settings_obj, field_name):
                """Grava last_* como date ou datetime conforme o tipo atual do campo."""
                cur_val = getattr(settings_obj, field_name, None)
                try:
                    if isinstance(cur_val, datetime):
                        setattr(settings_obj, field_name, now)
                    else:
                        setattr(settings_obj, field_name, today)
                except Exception:
                    # fallback seguro
                    setattr(settings_obj, field_name, today)

            db = DatabaseService()
            with db.get_session() as session:
                users_settings = (
                    session.query(User, UserScheduleSettings)
                    .join(UserScheduleSettings, User.id == UserScheduleSettings.user_id, isouter=True)
                    .filter(User.is_active == True)
                    .all()
                )

                logger.info(f"[{now.strftime('%H:%M:%S')}] Checking reminders/report for {len(users_settings)} users")

                for user, settings in users_settings:
                    # Trial/assinatura
                    self._check_trial_expiration(user, today)

                    # cria defaults se não existir configuração
                    if not settings:
                        settings = UserScheduleSettings(
                            user_id=user.id,
                            morning_reminder_time="09:00",
                            daily_report_time="08:00",
                            auto_send_enabled=True,
                            is_active=True,  # respeita seu schema
                        )
                        session.add(settings)
                        session.commit()

                    # respeita flags de settings
                    if hasattr(settings, "is_active") and settings.is_active is False:
                        continue
                    if hasattr(settings, "auto_send_enabled") and not settings.auto_send_enabled:
                        continue

                    # Parse horários com default se vier None/"" inválido
                    try:
                        morning_hhmm = datetime.strptime(settings.morning_reminder_time or "09:00", "%H:%M").time()
                    except Exception:
                        logger.warning(f"User {user.id} invalid morning_reminder_time; using 09:00")
                        morning_hhmm = datetime.strptime("09:00", "%H:%M").time()

                    try:
                        report_hhmm = datetime.strptime(settings.daily_report_time or "08:00", "%H:%M").time()
                    except Exception:
                        logger.warning(f"User {user.id} invalid daily_report_time; using 08:00")
                        report_hhmm = datetime.strptime("08:00", "%H:%M").time()

                    # Normaliza last_* para comparação por DATA
                    last_m = _as_date(getattr(settings, "last_morning_run", None))
                    last_r = _as_date(getattr(settings, "last_report_run", None))

                    # Morning reminders (catch-up)
                    run_morning = (now_time >= morning_hhmm) and (last_m != today)
                    if run_morning:
                        fut = asyncio.run_coroutine_threadsafe(
                            self._process_daily_reminders_for_user(user.id), self._get_loop()
                        )
                        try:
                            fut.result(timeout=120)
                            _set_run_field(settings, "last_morning_run")
                            if hasattr(settings, "updated_at"):
                                settings.updated_at = now
                            session.commit()
                            logger.info(f"✅ Morning reminders executed for user {user.id}")
                        except Exception as e:
                            logger.exception(f"Error morning reminders user {user.id}: {e}")

                    # Daily report (catch-up)
                    run_report = (now_time >= report_hhmm) and (last_r != today)
                    if run_report:
                        fut = asyncio.run_coroutine_threadsafe(
                            self._process_user_notifications_for_user(user.id), self._get_loop()
                        )
                        try:
                            fut.result(timeout=120)
                            _set_run_field(settings, "last_report_run")
                            if hasattr(settings, "updated_at"):
                                settings.updated_at = now
                            session.commit()
                            logger.info(f"📤 Daily report sent to user {user.id}")
                        except Exception as e:
                            logger.exception(f"Error sending daily report user {user.id}: {e}")

        except Exception:
            logger.exception("Error checking reminder times")

    def _check_pending_payments(self):
        """Check pending payments and process approved ones automatically"""
        logger.info("🔍 Checking pending payments for automatic processing")

        try:
            from services.database_service import DatabaseService
            from services.payment_service import payment_service
            from models import User, Subscription
            from datetime import datetime as dt

            db = DatabaseService()
            with db.get_session() as session:
                # últimas 24h em UTC
                yesterday_utc = dt.utcnow() - timedelta(hours=24)
                pending = (
                    session.query(Subscription)
                    .filter(Subscription.status == "pending", Subscription.created_at >= yesterday_utc)
                    .all()
                )

                logger.info(f"📋 Found {len(pending)} pending payments to check")

                for sub in pending:
                    try:
                        status = payment_service.check_payment_status(sub.payment_id)
                    except Exception as e:
                        logger.error(f"Payment check error {sub.payment_id}: {e}")
                        continue

                    if not status.get("success"):
                        logger.warning(f"Payment {sub.payment_id} check failed: {status.get('error')}")
                        continue

                    current_status = status.get("status")
                    if current_status == "approved":
                        old = sub.status
                        sub.status = "approved"
                        sub.paid_at = dt.utcnow()
                        sub.expires_at = dt.utcnow() + timedelta(days=30)

                        user = session.query(User).get(sub.user_id)
                        if user:
                            user.is_trial = False
                            user.is_active = True
                            user.last_payment_date = dt.utcnow()
                            user.next_due_date = sub.expires_at

                            # Enfileira notificação por Telegram (fila do worker)
                            sp_now = self._now_sp().strftime("%d/%m/%Y às %H:%M")
                            msg = (
                                "✅ **PAGAMENTO APROVADO AUTOMATICAMENTE!**\n\n"
                                f"💰 **Valor:** R$ {sub.amount:.2f}\n"
                                f"📅 **Aprovado em:** {sp_now}\n\n"
                                "🎉 **Sua conta foi ativada!**\n"
                                "• Plano Premium ativo por 30 dias\n"
                                "• Todos os recursos liberados\n"
                                f"• Próximo vencimento: {sub.expires_at.strftime('%d/%m/%Y')}\n\n"
                                "🚀 Use o comando /start para acessar todas as funcionalidades!"
                            )
                            self.enqueue_telegram_message(str(user.telegram_id), msg, parse_mode="Markdown")

                        session.commit()
                        logger.info(f"💾 Payment {sub.payment_id}: {old} → approved")

                    elif current_status in ("rejected", "cancelled"):
                        sub.status = current_status
                        session.commit()
                        logger.info(f"❌ Payment {sub.payment_id} {current_status}")

                # Expira pendências muito antigas (limpeza)
                very_old = (
                    session.query(Subscription)
                    .filter(Subscription.status == "pending", Subscription.created_at < yesterday_utc)
                    .all()
                )
                for old in very_old:
                    old.status = "expired"
                    logger.info(f"⏰ Expired pending payment {old.payment_id}")
                if very_old:
                    session.commit()

        except Exception:
            logger.exception("❌ Error checking pending payments")

    def _check_due_dates(self):
        """Marca clientes ativos como inativos se vencidos (rodar de hora em hora)"""
        try:
            from services.database_service import DatabaseService
            from models import Client

            # Data local São Paulo
            today = self._today_sp()

            db = DatabaseService()
            with db.get_session() as session:
                overdue = (
                    session.query(Client)
                    .filter(Client.due_date < today, Client.status == "active")
                    .all()
                )

                for c in overdue:
                    c.status = "inactive"
                    logger.info(f"🔻 Client '{c.name}' marked inactive (overdue)")

                session.commit()
        except Exception:
            logger.exception("Error checking due dates")

    # ------------- Blocos assíncronos de alto nível -------------

    async def _process_user_notifications_for_user(self, user_id: int):
        """
        Envia relatório diário por Telegram para um usuário:
        vencidos, hoje, amanhã, em 2 dias. Usa datas em America/Sao_Paulo.
        """
        from services.database_service import DatabaseService
        from services.telegram_service import telegram_service
        from models import User, Client

        db = DatabaseService()
        today = self._today_sp()
        tomorrow = today + timedelta(days=1)
        day_after = today + timedelta(days=2)

        try:
            with db.get_session() as session:
                user = session.query(User).filter_by(id=user_id, is_active=True).first()
                if not user:
                    return

                clients = session.query(Client).filter_by(user_id=user.id).all()
                if not clients:
                    return

                overdue = [c for c in clients if c.due_date and c.due_date < today and c.status == 'active']
                due_today = [c for c in clients if c.due_date and c.due_date == today and c.status == 'active']
                due_tomorrow = [c for c in clients if c.due_date and c.due_date == tomorrow and c.status == 'active']
                due_in_2 = [c for c in clients if c.due_date and c.due_date == day_after and c.status == 'active']

                if overdue or due_today or due_tomorrow or due_in_2:
                    txt = self._build_notification_message(overdue, due_today, due_tomorrow, due_in_2)
                    await telegram_service.send_notification(str(user.telegram_id), txt)
                    logger.info(f"📤 Daily notification sent to user {user.telegram_id}")
        except Exception:
            logger.exception(f"Error processing user notifications for user {user_id}")

    async def _process_daily_reminders_for_user(self, user_id: int):
        """
        Enfila mensagens de lembrete para (2d antes, 1d antes, hoje, 1d depois) para um usuário.
        O envio real é feito pelo worker da fila com retries e rate-limit.
        """
        from services.database_service import DatabaseService
        from models import User, Client, MessageTemplate

        db = DatabaseService()
        today = self._today_sp()

        try:
            with db.get_session() as session:
                user = session.query(User).filter_by(id=user_id, is_active=True).first()
                if not user:
                    logger.warning(f"User {user_id} not found or inactive")
                    return

                # Categorias
                clients_2d = session.query(Client).filter_by(
                    user_id=user.id, status="active", auto_reminders_enabled=True
                ).filter(Client.due_date == (today + timedelta(days=2))).all()

                clients_1d = session.query(Client).filter_by(
                    user_id=user.id, status="active", auto_reminders_enabled=True
                ).filter(Client.due_date == (today + timedelta(days=1))).all()

                clients_today = session.query(Client).filter_by(
                    user_id=user.id, status="active", auto_reminders_enabled=True, due_date=today
                ).all()

                clients_over = session.query(Client).filter_by(
                    user_id=user.id, status="active", auto_reminders_enabled=True
                ).filter(Client.due_date == (today - timedelta(days=1))).all()

                logger.info(
                    f"User {user_id} reminders: 2d={len(clients_2d)}, 1d={len(clients_1d)}, today={len(clients_today)}, over={len(clients_over)}"
                )

                # Enfileira por tipo
                await self._enqueue_reminders_by_type(session, user, clients_2d, "reminder_2_days")
                await self._enqueue_reminders_by_type(session, user, clients_1d, "reminder_1_day")
                await self._enqueue_reminders_by_type(session, user, clients_today, "reminder_due_date")
                await self._enqueue_reminders_by_type(session, user, clients_over, "reminder_overdue")

        except Exception:
            logger.exception(f"Error processing daily reminders for user {user_id}")

    # ------------- Construção das mensagens -------------

    def _build_notification_message(self, overdue_clients, due_today, due_tomorrow, due_day_after):
        """Mensagem de relatório diário (Telegram)"""
        def money(v):
            try:
                return f"R$ {float(getattr(v, 'plan_price', 0.0)):.2f}"
            except Exception:
                return "—"

        lines = ["📅 **Relatório Diário de Vencimentos**", ""]
        if overdue_clients:
            lines.append(f"🔴 **{len(overdue_clients)} cliente(s) em atraso:**")
            for c in overdue_clients[:5]:
                days = (self._today_sp() - c.due_date).days
                lines.append(f"• {c.name} - {days} dia(s) de atraso")
            if len(overdue_clients) > 5:
                lines.append(f"• ... e mais {len(overdue_clients) - 5} cliente(s)")
            lines.append("")

        if due_today:
            lines.append(f"🟡 **{len(due_today)} cliente(s) vencem hoje:**")
            for c in due_today[:5]:
                lines.append(f"• {c.name} - {money(c)}")
            if len(due_today) > 5:
                lines.append(f"• ... e mais {len(due_today) - 5} cliente(s)")
            lines.append("")

        if due_tomorrow:
            lines.append(f"🟠 **{len(due_tomorrow)} cliente(s) vencem amanhã:**")
            for c in due_tomorrow[:5]:
                lines.append(f"• {c.name} - {money(c)}")
            if len(due_tomorrow) > 5:
                lines.append(f"• ... e mais {len(due_tomorrow) - 5} cliente(s)")
            lines.append("")

        if due_day_after:
            lines.append(f"🔵 **{len(due_day_after)} cliente(s) vencem em 2 dias:**")
            for c in due_day_after[:5]:
                lines.append(f"• {c.name} - {money(c)}")
            if len(due_day_after) > 5:
                lines.append(f"• ... e mais {len(due_day_after) - 5} cliente(s)")
            lines.append("")

        lines.append("📱 Use o menu **👥 Clientes** para gerenciar seus clientes.")
        return "\n".join(lines)

    # ------------- Enfileiramento de lembretes -------------

    async def _enqueue_reminders_by_type(self, session, user, clients, reminder_type: str):
        """Prepara conteúdo do template e enfileira envios por WhatsApp com idempotência por dia."""
        from models import MessageTemplate, MessageLog
        from datetime import datetime as dt, time as dtime

        if not clients:
            return

        template = (
            session.query(MessageTemplate)
            .filter_by(user_id=user.id, template_type=reminder_type, is_active=True)
            .first()
        )
        if not template:
            logger.warning(f"No template for {reminder_type} (user {user.id})")
            return

        # Janela do dia para idempotência
        today = self._today_sp()
        start_dt = dt.combine(today, dtime.min).replace(tzinfo=SAO_PAULO_TZ).astimezone(pytz.utc).replace(tzinfo=None)

        for c in clients:
            # já enviado hoje?
            exists = (
                session.query(MessageLog)
                .filter(
                    MessageLog.user_id == user.id,
                    MessageLog.client_id == c.id,
                    MessageLog.template_id == template.id,
                    MessageLog.sent_at >= start_dt,
                )
                .first()
            )
            if exists:
                logger.info(f"[skip] already sent today: {c.name} ({reminder_type})")
                continue

            content = self._replace_template_variables(template.content, c)
            self.enqueue_whatsapp_message(
                to=c.phone_number,
                content=content,
                user_id=user.id,
                log_ctx={
                    "user_id": user.id,
                    "
