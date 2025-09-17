import schedule
import time
import threading
import logging
from datetime import datetime, timedelta, date as _date
import asyncio
import pytz

logger = logging.getLogger(__name__)

SAO_PAULO_TZ = pytz.timezone("America/Sao_Paulo")

class SchedulerService:
    def __init__(self):
        self.is_running = False
        self.thread = None
        self.loop = None
        self._last_reset_date_sp = None

        # ---- aliases de templates (flexível para seus nomes atuais) ----
        # ordem importa: o primeiro encontrado ativo é usado
        self.TEMPLATE_ALIASES = {
            "D_MINUS_2": ["reminder_2_days", "two_days_before", "vencimento_2_dias", "2_dias", "2dias_antes"],
            "D_MINUS_1": ["reminder_1_day", "one_day_before", "vencimento_1_dia", "1_dia", "1dia_antes"],
            "D_ZERO":    ["reminder_due_date", "vencimento_hoje", "vence_hoje", "hoje"],
            "OVERDUE":   ["reminder_overdue", "vencido", "atraso", "em_atraso"],
        }

    # -------------------- Lifecycle --------------------

    def start(self):
        if self.is_running:
            logger.warning("Scheduler service is already running")
            return

        self.is_running = True
        schedule.every().minute.do(self._check_reminder_times)
        schedule.every().hour.do(self._check_due_dates)    # só informativo
        schedule.every(2).minutes.do(self._check_pending_payments)
        schedule.every(1).seconds.do(self._tick)

        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()
        logger.info("Scheduler service started")

    def stop(self):
        self.is_running = False
        schedule.clear()
        if self.thread:
            self.thread.join()
        logger.info("Scheduler service stopped")

    def _run_scheduler(self):
        while self.is_running:
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                logger.error(f"Error in scheduler: {e}", exc_info=True)

    # -------------------- Tick / Virada de dia --------------------

    def _tick(self):
        try:
            now_sp = datetime.now(SAO_PAULO_TZ)
            today_sp = now_sp.date()
            if self._last_reset_date_sp != today_sp:
                logger.info(f"🌙 DIA TROCOU (SP): {today_sp}")
                self._execute_daily_reset()
                self._last_reset_date_sp = today_sp
        except Exception as e:
            logger.error(f"Error in _tick: {e}", exc_info=True)

    def _execute_daily_reset(self):
        try:
            now_sp = datetime.now(SAO_PAULO_TZ)
            current_time_str = now_sp.strftime("%H:%M")
            logger.info(f"🔄 MIDNIGHT RESET @ {current_time_str} (SP)")

            from services.database_service import DatabaseService
            from models import Client

            db_service = DatabaseService()
            with db_service.get_session() as session:
                yesterday_sp = (now_sp - timedelta(days=1)).date()
                # compat: não interfere na lógica por log; apenas mantém campo legados
                session.query(Client).filter_by(status='active').update({
                    'last_reminder_sent': yesterday_sp
                })
                session.commit()
        except Exception as e:
            logger.error(f"❌ Error in daily reset: {e}", exc_info=True)

    # -------------------- Janela de disparo por usuário --------------------

    def _check_reminder_times(self):
        try:
            from services.database_service import DatabaseService
            from models import User, UserScheduleSettings

            db_service = DatabaseService()
            now_sp = datetime.now(SAO_PAULO_TZ)
            current_time_hhmm = now_sp.strftime("%H:%M")
            current_date_sp = now_sp.date()

            logger.info(f"⏰ Checking reminder times at {current_time_hhmm} (São Paulo) — date={current_date_sp}")

            with db_service.get_session() as session:
                users_settings = session.query(User, UserScheduleSettings).join(
                    UserScheduleSettings, User.id == UserScheduleSettings.user_id, isouter=True
                ).filter(User.is_active == True).all()

                logger.info(f"Found {len(users_settings)} users to check")

                for user, settings in users_settings:
                    if not settings:
                        settings = UserScheduleSettings(
                            user_id=user.id,
                            morning_reminder_time="09:00",
                            daily_report_time="08:00",
                            auto_send_enabled=True
                        )
                        session.add(settings)
                        session.commit()
                        logger.info(f"Created default schedule settings for user {user.id}")

                    if hasattr(settings, "auto_send_enabled") and not settings.auto_send_enabled:
                        logger.info(f"Auto send disabled for user {user.id}, skipping")
                        continue

                    times_to_check = set()
                    if getattr(settings, "morning_reminder_time", None):
                        times_to_check.add(settings.morning_reminder_time.strip())
                    if getattr(settings, "evening_reminder_time", None):
                        t = settings.evening_reminder_time.strip()
                        if t:
                            times_to_check.add(t)
                    if getattr(settings, "custom_times", None):
                        raw = settings.custom_times
                        if raw and isinstance(raw, str):
                            for t in raw.split(","):
                                t = t.strip()
                                if t:
                                    times_to_check.add(t)

                    valid_times = []
                    for t in times_to_check:
                        try:
                            _ = datetime.strptime(t, "%H:%M").time()
                            valid_times.append(t)
                        except Exception:
                            logger.warning(f"User {user.id}: ignoring invalid time '{t}'")

                    if not valid_times:
                        logger.info(f"User {user.id}: no valid times configured, skipping")
                        continue

                    already_ran_today = (getattr(settings, "last_morning_run", None) == current_date_sp)

                    if (not already_ran_today) and (current_time_hhmm in valid_times):
                        logger.info(f"✅ EXECUTING reminders for user {user.id} at {current_time_hhmm} (SP)")
                        try:
                            self._process_daily_reminders_sync(user.id)
                            settings.last_morning_run = current_date_sp
                            session.commit()
                            logger.info(f"✅ COMPLETED user {user.id} at {current_time_hhmm}; last_morning_run={current_date_sp}")
                        except Exception as e:
                            logger.error(f"❌ Error processing reminders for user {user.id}: {e}", exc_info=True)
                    else:
                        if already_ran_today:
                            logger.debug(f"⏭️  SKIP user {user.id}: already ran on {current_date_sp}")
                        else:
                            logger.debug(f"⏱️  WAIT user {user.id}: {current_time_hhmm} not in {sorted(valid_times)}")
        except Exception as e:
            logger.error(f"❌ Error checking reminder times: {e}", exc_info=True)

    def _get_event_loop(self):
        try:
            loop = asyncio.get_running_loop()
            return loop
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    raise RuntimeError("Loop is closed")
                return loop
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                return loop

    # -------------------- Due-dates (informativo, não bloqueia) --------------------

    def _check_due_dates(self):
        logger.info("Running due date info pass")
        try:
            from services.database_service import DatabaseService
            from models import Client

            db_service = DatabaseService()
            with db_service.get_session() as session:
                today_sp = datetime.now(SAO_PAULO_TZ).date()
                try:
                    overdue = session.query(Client).filter(Client.due_date < today_sp).all()
                    ok = session.query(Client).filter(Client.due_date >= today_sp).all()
                    for c in overdue:
                        if hasattr(c, "is_overdue"):
                            c.is_overdue = True
                    for c in ok:
                        if hasattr(c, "is_overdue"):
                            c.is_overdue = False
                    session.commit()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"Error checking due dates: {e}", exc_info=True)

    # -------------------- Pending payments --------------------

    def _check_pending_payments(self):
        logger.info("🔍 Checking pending payments for automatic processing")
        try:
            from services.database_service import DatabaseService
            from services.payment_service import payment_service
            from services.telegram_service import telegram_service
            from models import User, Subscription

            db_service = DatabaseService()

            with db_service.get_session() as session:
                yesterday_utc = datetime.utcnow() - timedelta(hours=24)
                pending_subscriptions = session.query(Subscription).filter(
                    Subscription.status == 'pending',
                    Subscription.created_at >= yesterday_utc
                ).all()

                approved_count = 0
                pending_count = 0

                for subscription in pending_subscriptions:
                    payment_status = payment_service.check_payment_status(subscription.payment_id)
                    if payment_status['success']:
                        current_status = payment_status['status']
                        status_detail = payment_status.get('status_detail', 'N/A')

                        if current_status == 'approved':
                            approved_count += 1
                            old_status = subscription.status
                            subscription.status = 'approved'
                            subscription.paid_at = datetime.utcnow()
                            subscription.expires_at = datetime.utcnow() + timedelta(days=30)

                            user = session.query(User).get(subscription.user_id)
                            if user:
                                user.is_trial = False
                                user.is_active = True
                                user.last_payment_date = datetime.utcnow()
                                user.next_due_date = subscription.expires_at
                                try:
                                    msg = (
                                        "✅ **PAGAMENTO APROVADO AUTOMATICAMENTE!**\n\n"
                                        f"💰 **Valor:** R$ {subscription.amount:.2f}\n"
                                        f"📅 **Aprovado em:** {datetime.now().strftime('%d/%m/%Y às %H:%M')}\n\n"
                                        "🎉 **Sua conta foi ativada!**\n"
                                        "• Plano Premium ativo por 30 dias\n"
                                        "• Todos os recursos liberados\n"
                                        f"• Próximo vencimento: {subscription.expires_at.strftime('%d/%m/%Y')}\n\n"
                                        "🚀 Use o comando /start para acessar todas as funcionalidades!"
                                    )
                                    future = asyncio.run_coroutine_threadsafe(
                                        telegram_service.send_message(user.telegram_id, msg),
                                        self._get_event_loop()
                                    )
                                    future.result(timeout=10)
                                except Exception:
                                    logger.exception("Error sending approval notification")

                            session.commit()
                            logger.info(f"Payment {subscription.payment_id} updated: {old_status} → approved")

                        elif current_status == 'pending':
                            pending_count += 1
                        elif current_status in ['rejected', 'cancelled']:
                            subscription.status = current_status
                            session.commit()
                    else:
                        logger.warning(f"Failed to check payment {subscription.payment_id}: {payment_status.get('error')}")

                if len(pending_subscriptions) > 0:
                    logger.info(
                        f"Payments summary: {approved_count} approved, {pending_count} still pending, "
                        f"{len(pending_subscriptions) - approved_count - pending_count} other status"
                    )

                old_pending = session.query(Subscription).filter(
                    Subscription.status == 'pending',
                    Subscription.created_at < yesterday_utc
                ).all()
                for old_sub in old_pending:
                    old_sub.status = 'expired'
                if old_pending:
                    session.commit()
                    logger.info(f"Expired {len(old_pending)} old pending payments")
        except Exception as e:
            logger.error(f"❌ Error checking pending payments: {e}", exc_info=True)

    # -------------------- Notificações (informativas) --------------------

    def _send_user_notifications(self):
        logger.info("Running daily user notifications")
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._process_user_notifications())
        except Exception as e:
            logger.error(f"Error sending user notifications: {e}", exc_info=True)
        finally:
            if self.loop:
                self.loop.close()

    async def _process_user_notifications(self):
        from services.database_service import DatabaseService
        from services.telegram_service import telegram_service
        from models import Client, User

        db_service = DatabaseService()

        today = datetime.now(SAO_PAULO_TZ).date()
        tomorrow = today + timedelta(days=1)
        day_after_tomorrow = today + timedelta(days=2)

        try:
            with db_service.get_session() as session:
                users = session.query(User).filter_by(is_active=True).all()
                for user in users:
                    overdue_clients = session.query(Client).filter_by(
                        user_id=user.id, status='active'
                    ).filter(Client.due_date < today).all()
                    due_today = session.query(Client).filter_by(
                        user_id=user.id, status='active', due_date=today
                    ).all()
                    due_tomorrow = session.query(Client).filter_by(
                        user_id=user.id, status='active', due_date=tomorrow
                    ).all()
                    due_day_after = session.query(Client).filter_by(
                        user_id=user.id, status='active', due_date=day_after_tomorrow
                    ).all()

                    if overdue_clients or due_today or due_tomorrow or due_day_after:
                        notification_text = self._build_notification_message(
                            overdue_clients, due_today, due_tomorrow, due_day_after
                        )
                        success = await telegram_service.send_notification(
                            user.telegram_id, notification_text
                        )
                        if success:
                            logger.info(f"Sent daily notification to user {user.telegram_id}")
                        else:
                            logger.error(f"Failed to send notification to user {user.telegram_id}")
        except Exception as e:
            logger.error(f"Error processing user notifications: {e}", exc_info=True)

    def _build_notification_message(self, overdue_clients, due_today, due_tomorrow, due_day_after):
        message = "📅 **Relatório Diário de Vencimentos**\n\n"
        if overdue_clients:
            message += f"🔴 **{len(overdue_clients)} cliente(s) em atraso:**\n"
            for client in overdue_clients[:5]:
                days_overdue = (datetime.now(SAO_PAULO_TZ).date() - client.due_date).days
                message += f"• {client.name} - {days_overdue} dia(s) de atraso\n"
            if len(overdue_clients) > 5:
                message += f"• ... e mais {len(overdue_clients) - 5} cliente(s)\n"
            message += "\n"
        if due_today:
            message += f"🟡 **{len(due_today)} cliente(s) vencem hoje:**\n"
            for client in due_today[:5]:
                message += f"• {client.name} - R$ {client.plan_price:.2f}\n"
            if len(due_today) > 5:
                message += f"• ... e mais {len(due_today) - 5} cliente(s)\n"
            message += "\n"
        if due_tomorrow:
            message += f"🟠 **{len(due_tomorrow)} cliente(s) vencem amanhã:**\n"
            for client in due_tomorrow[:5]:
                message += f"• {client.name} - R$ {client.plan_price:.2f}\n"
            if len(due_tomorrow) > 5:
                message += f"• ... e mais {len(due_tomorrow) - 5} cliente(s)\n"
            message += "\n"
        if due_day_after:
            message += f"🔵 **{len(due_day_after)} cliente(s) vencem em 2 dias:**\n"
            for client in due_day_after[:5]:
                message += f"• {client.name} - R$ {client.plan_price:.2f}\n"
            if len(due_day_after) > 5:
                message += f"• ... e mais {len(due_day_after) - 5} cliente(s)\n"
            message += "\n"
        message += "📱 Use o menu **👥 Clientes** para gerenciar seus clientes."
        return message

    # -------------------- Motor diário por delta (OFICIAL) --------------------

    def _template_for_delta_key(self, delta_days: int) -> str | None:
        if delta_days == 2:
            return "D_MINUS_2"
        if delta_days == 1:
            return "D_MINUS_1"
        if delta_days == 0:
            return "D_ZERO"
        if delta_days <= -1:
            return "OVERDUE"
        return None

    def _get_active_template(self, session, user_id, alias_keys):
        """Busca o primeiro template ativo que exista para a lista de aliases indicada."""
        from models import MessageTemplate
        for key in alias_keys:
            names = self.TEMPLATE_ALIASES.get(key, [])
            if not names:
                continue
            # procura por qualquer um dos nomes
            template = session.query(MessageTemplate).filter(
                MessageTemplate.user_id == user_id,
                MessageTemplate.is_active == True,
                MessageTemplate.template_type.in_(names)
            ).order_by(MessageTemplate.id.asc()).first()
            if template:
                return template
        return None

    def _already_sent_today(self, session, user_id, client_id, template_type) -> bool:
        from models import MessageLog
        from sqlalchemy import func
        today_sp = datetime.now(SAO_PAULO_TZ).date()
        return session.query(MessageLog).filter(
            MessageLog.user_id == user_id,
            MessageLog.client_id == client_id,
            MessageLog.template_type == template_type,
            func.date(MessageLog.sent_at) == today_sp
        ).first() is not None

    def _process_daily_reminders_sync(self, user_id):
        """
        Envia 1 template por cliente/dia, conforme o delta:
        D-2, D-1, D0 e D+N (overdue) diariamente até renovar (mudar due_date).
        Usa aliases de template para suportar nomes diferentes.
        """
        logger.info(f"🚀 SYNC DAILY ENGINE: user {user_id}")
        try:
            from services.database_service import DatabaseService
            from services.whatsapp_service import WhatsAppService
            from models import Client, MessageLog

            db = DatabaseService()
            ws = WhatsAppService()
            today_sp = datetime.now(SAO_PAULO_TZ).date()

            with db.get_session() as session:
                clients = session.query(Client).filter(
                    Client.user_id == user_id,
                    Client.auto_reminders_enabled == True
                ).all()

                if not clients:
                    logger.info(f"SYNC DAILY ENGINE: user {user_id} sem clientes elegíveis")
                    return

                # métricas por bucket
                bucket_counts = {"D-2": 0, "D-1": 0, "D0": 0, "OVERDUE": 0}
                sent_count = 0
                no_template = 0
                dedup = 0

                for client in clients:
                    if not client.due_date:
                        continue

                    delta = (client.due_date - today_sp).days
                    key = self._template_for_delta_key(delta)
                    if not key:
                        continue

                    # métrica
                    if key == "D_MINUS_2":
                        bucket_counts["D-2"] += 1
                    elif key == "D_MINUS_1":
                        bucket_counts["D-1"] += 1
                    elif key == "D_ZERO":
                        bucket_counts["D0"] += 1
                    elif key == "OVERDUE":
                        bucket_counts["OVERDUE"] += 1

                    # pega primeiro template ativo dentre os aliases dessa chave
                    template = self._get_active_template(session, user_id, [key])
                    if not template:
                        no_template += 1
                        continue

                    # de-dup por dia
                    if self._already_sent_today(session, user_id, client.id, template.template_type):
                        dedup += 1
                        continue

                    msg = self._replace_template_variables(template.content or "", client)

                    try:
                        result = ws.send_message(client.phone_number, msg, user_id)
                        status = 'sent' if result.get('success') else 'failed'
                        error_msg = result.get('error') if not result.get('success') else None
                    except Exception as e:
                        status = 'failed'
                        error_msg = str(e)

                    log = MessageLog(
                        user_id=user_id,
                        client_id=client.id,
                        template_type=template.template_type,  # guarda o nome real do template usado
                        recipient_phone=client.phone_number,
                        message_content=msg,
                        sent_at=datetime.now(),
                        status=status,
                        error_message=error_msg
                    )
                    session.add(log)
                    if status == 'sent':
                        sent_count += 1

                session.commit()
                logger.info(
                    f"✅ SYNC DAILY ENGINE (user {user_id}) "
                    f"buckets: D-2={bucket_counts['D-2']}, D-1={bucket_counts['D-1']}, "
                    f"D0={bucket_counts['D0']}, OVERDUE={bucket_counts['OVERDUE']} | "
                    f"enviados={sent_count}, sem_template={no_template}, ja_enviado_hoje={dedup}"
                )

        except Exception as e:
            logger.error(f"❌ SYNC DAILY ENGINE error (user {user_id}): {e}", exc_info=True)

    # -------------------- Util --------------------

    def _replace_template_variables(self, template_content, client):
        variables = {
            '{nome}': client.name,
            '{plano}': client.plan_name,
            '{valor}': f"{client.plan_price:.2f}" if getattr(client, "plan_price", None) is not None else "",
            '{vencimento}': client.due_date.strftime('%d/%m/%Y') if client.due_date else '',
            '{servidor}': getattr(client, "server", None) or 'Não definido',
            '{informacoes_extras}': getattr(client, "other_info", None) or ''
        }
        result = template_content or ""
        for var, value in variables.items():
            result = result.replace(var, str(value))
        return result.strip()

# Global instance
scheduler_service = SchedulerService()