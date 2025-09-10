import schedule
import time
import threading
import logging
from datetime import datetime, timedelta, date
import asyncio
import traceback
import pytz

logger = logging.getLogger(__name__)

# =========================
#  Timezone helpers (SP)
# =========================
SAO_PAULO_TZ = pytz.timezone("America/Sao_Paulo")

def now_sp() -> datetime:
    """Retorna datetime timezone-aware em America/Sao_Paulo"""
    return datetime.now(SAO_PAULO_TZ)

def today_sp() -> date:
    """Retorna a data de hoje em America/Sao_Paulo"""
    return now_sp().date()

def naive_from_sp(dt_sp: datetime) -> datetime:
    """Converte um datetime aware (SP) para naive mantendo o horário local de SP."""
    if dt_sp.tzinfo is None:
        return dt_sp
    return dt_sp.replace(tzinfo=None)

def today_bounds_sp():
    """Retorna (start, end) naive em SP para a janela de hoje [00:00, 23:59:59.999999]."""
    n = now_sp()
    start = naive_from_sp(n.replace(hour=0, minute=0, second=0, microsecond=0))
    end   = naive_from_sp(n.replace(hour=23, minute=59, second=59, microsecond=999999))
    return start, end


class SchedulerService:
    def __init__(self):
        self.is_running = False
        self.thread = None
        self.loop = None
        # controle do reset diário (em SP)
        self._last_reset_date = None  # type: date | None

    def start(self):
        """Start the scheduler service"""
        if self.is_running:
            logger.warning("Scheduler service is already running")
            return

        self.is_running = True

        # Agendamentos básicos
        schedule.every().minute.do(self._check_reminder_times)  # verifica horários por usuário
        schedule.every().hour.do(self._check_due_dates)          # checagem de vencidos
        schedule.every(2).minutes.do(self._check_pending_payments)  # verificação de pagamentos

        # Importante: NÃO usamos schedule.at("05:00") porque depende do TZ do SO.
        # O reset às 05:00 SP é disparado internamente no loop principal (_run_scheduler).

        # Start thread
        self.thread = threading.Thread(target=self._run_scheduler, daemon=True)
        self.thread.start()

        logger.info(f"Scheduler service started. TZ efetivo: America/Sao_Paulo; now={now_sp().isoformat()}")

    def stop(self):
        """Stop the scheduler service"""
        self.is_running = False
        schedule.clear()
        if self.thread:
            self.thread.join()
        logger.info("Scheduler service stopped")

    def _run_scheduler(self):
        """Run the scheduler in a separate thread"""
        while self.is_running:
            try:
                schedule.run_pending()

                # Gatilho interno para reset diário às 05:00 em SP
                sp_now = now_sp()
                if sp_now.hour == 5 and sp_now.minute == 0:
                    if self._last_reset_date != sp_now.date():
                        self._auto_daily_reset()
                        self._last_reset_date = sp_now.date()

                # loop mais responsivo para não “pular” janelas de 1 min
                time.sleep(1)
            except Exception as e:
                logger.error(f"Error in scheduler: {e}", exc_info=True)

    def _check_reminder_times(self):
        """Check if it's time for any user's scheduled reminders or reports - improved to handle missed executions"""
        try:
            from services.database_service import DatabaseService
            from models import User, UserScheduleSettings
            # imports usados dentro do processamento direto
            # from services.whatsapp_service import whatsapp_service
            # from services.telegram_service import telegram_service

            db_service = DatabaseService()

            current_datetime = now_sp()
            current_time_str = current_datetime.strftime("%H:%M")
            current_date = current_datetime.date()
            current_time = current_datetime.time()

            logger.info(f"[SP {current_time_str}] Checking reminder times")

            with db_service.get_session() as session:
                users_settings = (
                    session.query(User, UserScheduleSettings)
                    .join(UserScheduleSettings, User.id == UserScheduleSettings.user_id, isouter=True)
                    .filter(User.is_active == True)
                    .all()
                )

                logger.info(f"Found {len(users_settings)} users to check")

                for user, settings in users_settings:
                    # Trial / expiração
                    self._check_trial_expiration(user, current_date)

                    if not settings:
                        # cria defaults
                        settings = UserScheduleSettings(
                            user_id=user.id,
                            morning_reminder_time='09:00',
                            daily_report_time='08:00',
                            auto_send_enabled=True
                        )
                        session.add(settings)
                        session.commit()

                    # Auto-send ativo?
                    if hasattr(settings, 'auto_send_enabled') and not settings.auto_send_enabled:
                        logger.info(f"Auto send disabled for user {user.id}, skipping")
                        continue

                    morning_time_str = settings.morning_reminder_time or '09:00'
                    try:
                        daily_time = datetime.strptime(morning_time_str, "%H:%M").time()
                    except ValueError as e:
                        logger.error(f"Invalid time format for user {user.id}: {e}")
                        continue

                    # Estratégia: dispara quando o horário de SP passar do horário do usuário.
                    # Evita duplicata por meio do log “enviado hoje”.
                    if current_time >= daily_time:
                        logger.info(f"🧪 TEST MODE: Processing daily reminders for user {user.id} (SP {current_time_str} >= {morning_time_str})")
                        try:
                            self._process_daily_reminders_sync(user.id)
                            logger.info(f"✅ TEST MODE: Daily reminders completed for user {user.id}")
                        except Exception as e:
                            logger.error(f"Error processing daily reminders for user {user.id}: {str(e)}")
                            logger.error(f"Full traceback: {traceback.format_exc()}")

                    # Relatórios diários desativados no modo de teste
        except Exception as e:
            logger.error(f"Error checking reminder times: {e}", exc_info=True)

    def _get_event_loop(self):
        """Get or create event loop for async operations"""
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

    def _send_user_reminders(self, user_id, time_period):
        """Send reminders for a specific user"""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

            if time_period == 'morning':
                self.loop.run_until_complete(self._process_reminders_for_user(user_id))
            else:
                self.loop.run_until_complete(self._process_evening_reminders_for_user(user_id))
        except Exception as e:
            logger.error(f"Error sending {time_period} reminders for user {user_id}: {e}")
        finally:
            if self.loop:
                self.loop.close()

    def _send_user_notifications_for_user(self, user_id):
        """Send daily notifications to specific user about their clients' due dates"""
        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._process_user_notifications_for_user(user_id))
        except Exception as e:
            logger.error(f"Error sending daily notifications to user {user_id}: {e}")
        finally:
            if self.loop:
                self.loop.close()

    def _auto_daily_reset(self):
        """Automatic daily reset at 5:00 AM São Paulo time to clear 'already sent today' flags"""
        try:
            current_datetime = now_sp()
            current_time_str = current_datetime.strftime("%H:%M")

            logger.info(f"🔄 AUTO RESET: Starting daily reset at {current_time_str} (São Paulo time)")

            from services.database_service import DatabaseService
            from models import Client

            db_service = DatabaseService()

            with db_service.get_session() as session:
                yesterday = today_sp() - timedelta(days=1)

                # Caso o campo seja date
                updated_count = (
                    session.query(Client)
                    .filter_by(status='active')
                    .update({'last_reminder_sent': yesterday})
                )

                session.commit()

                logger.info(f"✅ AUTO RESET: Reset reminder flags for {updated_count} active clients")
                logger.info(f"🔄 AUTO RESET: Daily reset completed successfully at {current_time_str}")

        except Exception as e:
            logger.error(f"❌ Error in auto daily reset: {e}")
            logger.error(traceback.format_exc())

    def _check_pending_payments(self):
        """Check pending payments and process approved ones automatically"""
        logger.info("🔍 Checking pending payments for automatic processing")

        try:
            from services.database_service import DatabaseService
            from services.payment_service import payment_service
            from services.telegram_service import telegram_service
            from models import User, Subscription

            db_service = DatabaseService()

            with db_service.get_session() as session:
                # janela de 24h em UTC para busca
                yesterday_utc = datetime.utcnow() - timedelta(hours=24)
                pending_subscriptions = (
                    session.query(Subscription)
                    .filter(
                        Subscription.status == 'pending',
                        Subscription.created_at >= yesterday_utc
                    )
                    .all()
                )

                logger.info(f"📋 Found {len(pending_subscriptions)} pending payments to check")

                approved_count = 0
                pending_count = 0

                for subscription in pending_subscriptions:
                    logger.info(f"🔍 Checking payment {subscription.payment_id} for user {subscription.user_id}")

                    payment_status = payment_service.check_payment_status(subscription.payment_id)

                    if payment_status['success']:
                        current_status = payment_status['status']
                        status_detail = payment_status.get('status_detail', 'N/A')
                        logger.info(f"📊 Payment {subscription.payment_id} status: {current_status} ({status_detail})")

                        if current_status == 'approved':
                            approved_count += 1
                            logger.info(f"✅ Payment {subscription.payment_id} APPROVED! Processing automatically...")

                            # Atualiza assinatura (mantém created/pago em UTC para auditoria)
                            subscription.status = 'approved'
                            subscription.paid_at = datetime.utcnow()

                            # expiração em SP (armazenado naive em SP)
                            expires_at_sp = now_sp() + timedelta(days=30)
                            subscription.expires_at = naive_from_sp(expires_at_sp)

                            # Atualiza usuário
                            user = session.query(User).get(subscription.user_id)
                            if user:
                                user.is_trial = False
                                user.is_active = True
                                user.last_payment_date = datetime.utcnow()
                                user.next_due_date = subscription.expires_at

                                # Notificação em horário local SP para o texto
                                try:
                                    ts_text = now_sp().strftime('%d/%m/%Y às %H:%M')
                                    prox_venc = expires_at_sp.strftime('%d/%m/%Y')
                                    notification_message = f"""
✅ **PAGAMENTO APROVADO AUTOMATICAMENTE!**

💰 **Valor:** R$ {subscription.amount:.2f}
📅 **Aprovado em:** {ts_text}

🎉 **Sua conta foi ativada!**
• Plano Premium ativo por 30 dias
• Todos os recursos liberados
• Próximo vencimento: {prox_venc}

🚀 Use o comando /start para acessar todas as funcionalidades!
"""
                                    future = asyncio.run_coroutine_threadsafe(
                                        telegram_service.send_message(
                                            user.telegram_id,
                                            notification_message
                                        ),
                                        self._get_event_loop()
                                    )
                                    future.result(timeout=10)

                                    logger.info(f"📲 Automatic approval notification sent to user {user.telegram_id}")
                                except Exception as e:
                                    logger.error(f"❌ Error sending approval notification: {e}")

                            session.commit()
                            logger.info(f"💾 Payment {subscription.payment_id} updated to approved")

                        elif current_status == 'pending':
                            pending_count += 1
                            if status_detail == 'pending_waiting_transfer':
                                logger.info(f"⏳ Payment {subscription.payment_id} - waiting PIX scan")
                            else:
                                logger.info(f"⏳ Payment {subscription.payment_id} - Still processing: {status_detail}")

                        elif current_status in ['rejected', 'cancelled']:
                            logger.info(f"❌ Payment {subscription.payment_id} {current_status} - updating status")
                            subscription.status = current_status
                            session.commit()
                    else:
                        logger.warning(f"⚠️ Failed to check payment {subscription.payment_id}: {payment_status.get('error')}")

                if len(pending_subscriptions) > 0:
                    logger.info(f"📊 Payment check summary: {approved_count} approved, {pending_count} pending")

                # Expira pendências muito antigas
                old_pending = (
                    session.query(Subscription)
                    .filter(
                        Subscription.status == 'pending',
                        Subscription.created_at < yesterday_utc
                    ).all()
                )
                for old_sub in old_pending:
                    old_sub.status = 'expired'
                    logger.info(f"⏰ Expired old pending payment {old_sub.payment_id}")

                if old_pending:
                    session.commit()
                    logger.info(f"🧹 Cleaned up {len(old_pending)} expired payments")

        except Exception as e:
            logger.error(f"❌ Error checking pending payments: {e}")
            logger.error(traceback.format_exc())

    def _check_due_dates(self):
        """Check for overdue clients and update status"""
        logger.info("Running due date check")

        try:
            from services.database_service import DatabaseService
            from models import Client

            db_service = DatabaseService()

            with db_service.get_session() as session:
                today = today_sp()

                overdue_clients = (
                    session.query(Client)
                    .filter(
                        Client.due_date < today,
                        Client.status == 'active'
                    ).all()
                )

                for client in overdue_clients:
                    client.status = 'inactive'
                    logger.info(f"Marked client {client.name} as inactive (overdue)")

                session.commit()

        except Exception as e:
            logger.error(f"Error checking due dates: {e}", exc_info=True)

    def _send_user_notifications(self):
        """Send daily notifications to users about their clients' due dates"""
        logger.info("Running daily user notifications")

        try:
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
            self.loop.run_until_complete(self._process_user_notifications())
        except Exception as e:
            logger.error(f"Error sending user notifications: {e}")
        finally:
            if self.loop:
                self.loop.close()

    async def _process_user_notifications(self):
        """Process and send daily notifications to users via Telegram"""
        from services.database_service import DatabaseService
        from services.telegram_service import telegram_service
        from models import Client, User

        db_service = DatabaseService()

        today = today_sp()
        tomorrow = today + timedelta(days=1)
        day_after_tomorrow = today + timedelta(days=2)

        try:
            with db_service.get_session() as session:
                users = session.query(User).filter_by(is_active=True).all()

                for user in users:
                    overdue_clients = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status='active')
                        .filter(Client.due_date < today)
                        .all()
                    )

                    due_today = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status='active', due_date=today)
                        .all()
                    )

                    due_tomorrow = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status='active', due_date=tomorrow)
                        .all()
                    )

                    due_day_after = (
                        session.query(Client)
                        .filter_by(user_id=user.id, status='active', due_date=day_after_tomorrow)
                        .all()
                    )

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
        """Build the notification message for user"""
        message = "📅 **Relatório Diário de Vencimentos**\n\n"

        if overdue_clients:
            message += f"🔴 **{len(overdue_clients)} cliente(s) em atraso:**\n"
            for client in overdue_clients[:5]:
                days_overdue = (today_sp() - client.due_date).days
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

    async def _process_reminders(self):
        """Process and send reminder messages"""
        from services.database_service import DatabaseService
        from services.whatsapp_service import WhatsAppService
        from models import Client, User

        db_service = DatabaseService()
        whatsapp_service = WhatsAppService()

        today = today_sp()
        reminder_2_days = today + timedelta(days=2)
        reminder_1_day = today + timedelta(days=1)

        try:
            with db_service.get_session() as session:
                users = session.query(User).filter_by(is_active=True).all()

                for user in users:
                    await self._send_reminder_type(session, user, today, 'reminder_due_date', whatsapp_service)
                    await self._send_reminder_type(session, user, reminder_1_day, 'reminder_1_day', whatsapp_service)
                    await self._send_reminder_type(session, user, reminder_2_days, 'reminder_2_days', whatsapp_service)

                    overdue_date = today - timedelta(days=1)
                    await self._send_reminder_type(session, user, overdue_date, 'reminder_overdue', whatsapp_service)
        except Exception as e:
            logger.error(f"Error processing reminders: {e}", exc_info=True)

    async def _process_evening_reminders(self):
        """Process evening reminders for next day due dates"""
        from services.database_service import DatabaseService
        from services.whatsapp_service import WhatsAppService
        from models import Client, User

        db_service = DatabaseService()
        whatsapp_service = WhatsAppService()

        tomorrow = today_sp() + timedelta(days=1)

        try:
            with db_service.get_session() as session:
                users = session.query(User).filter_by(is_active=True).all()
                for user in users:
                    await self._send_reminder_type(session, user, tomorrow, 'reminder_1_day', whatsapp_service)
        except Exception as e:
            logger.error(f"Error processing evening reminders: {e}", exc_info=True)

    async def _send_reminder_type(self, session, user, target_date, reminder_type, whatsapp_service):
        """Send specific type of reminder"""
        from models import Client, MessageTemplate, MessageLog
        from sqlalchemy import and_

        try:
            template = (
                session.query(MessageTemplate)
                .filter_by(user_id=user.id, template_type=reminder_type, is_active=True)
                .first()
            )
            if not template:
                logger.warning(f"No template found for {reminder_type} for user {user.id}")
                return

            clients = (
                session.query(Client)
                .filter_by(user_id=user.id, status='active', auto_reminders_enabled=True)
                .filter(Client.due_date == target_date)
                .all()
            )

            start, end = today_bounds_sp()

            for client in clients:
                existing_log = (
                    session.query(MessageLog)
                    .filter(
                        MessageLog.user_id == user.id,
                        MessageLog.client_id == client.id,
                        MessageLog.template_id == template.id,
                        MessageLog.sent_at.between(start, end)
                    )
                    .first()
                )
                if existing_log:
                    logger.info(f"Message already sent today for client {client.name}, type {reminder_type}")
                    continue

                message_content = self._replace_template_variables(template.content, client)

                # Checagem rápida de conexão
                try:
                    connection_status = asyncio.wait_for(
                        asyncio.to_thread(whatsapp_service.check_instance_status, user.id),
                        timeout=2
                    )
                    connection_status = await connection_status
                    if not connection_status.get('connected', False):
                        logger.warning(f"WhatsApp not connected for user {user.id}, skipping message to {client.name}")
                        msg_log = MessageLog(
                            user_id=user.id,
                            client_id=client.id,
                            template_type=reminder_type,
                            recipient_phone=client.phone_number,
                            message_content=message_content,
                            sent_at=naive_from_sp(now_sp()),
                            status='failed',
                            error_message='WhatsApp not connected'
                        )
                        session.add(msg_log)
                        continue
                except asyncio.TimeoutError:
                    logger.warning(f"Connection check timeout for user {user.id}, trying to send anyway")
                except Exception as e:
                    logger.warning(f"Connection check failed for user {user.id}: {e}, trying to send anyway")

                result = whatsapp_service.send_message(client.phone_number, message_content, user.id)

                if result.get('success'):
                    msg_log = MessageLog(
                        user_id=user.id,
                        client_id=client.id,
                        template_id=template.id,
                        message_content=message_content,
                        sent_at=naive_from_sp(now_sp()),
                        status='sent'
                    )
                    session.add(msg_log)
                    logger.info(f"Sent {reminder_type} reminder to {client.name}")
                else:
                    msg_log = MessageLog(
                        user_id=user.id,
                        client_id=client.id,
                        template_id=template.id,
                        message_content=message_content,
                        sent_at=naive_from_sp(now_sp()),
                        status='failed',
                        error_message=result.get('error', 'WhatsApp send failed')
                    )
                    session.add(msg_log)
                    logger.error(f"Failed to send {reminder_type} reminder to {client.name}")

            session.commit()

        except Exception as e:
            logger.error(f"Error sending {reminder_type} reminders: {e}", exc_info=True)

    def _replace_template_variables(self, template_content, client):
        """Replace template variables with client data"""
        variables = {
            '{nome}': client.name,
            '{plano}': client.plan_name,
            '{valor}': f"{client.plan_price:.2f}",
            '{vencimento}': client.due_date.strftime('%d/%m/%Y') if client.due_date else '',
            '{servidor}': client.server or 'Não definido',
            '{informacoes_extras}': client.other_info or ''
        }

        result = template_content or ''
        for var, value in variables.items():
            result = result.replace(var, str(value))

        if not client.other_info:
            result = result.replace('\n\n\n', '\n\n')

        return result.strip()

    async def _send_reminders_by_type(self, session, user, clients, reminder_type, whatsapp_service):
        """Send reminders to specific clients by type (simplificado)"""
        from models import MessageTemplate, MessageLog

        logger.info(f"📤 Sending {reminder_type} to {len(clients)} clients")

        try:
            template = (
                session.query(MessageTemplate)
                .filter_by(user_id=user.id, template_type=reminder_type, is_active=True)
                .first()
            )
            if not template:
                logger.warning(f"❌ No template for {reminder_type}")
                return

            start, end = today_bounds_sp()

            for client in clients:
                # simples: não repete no mesmo dia (SP)
                already = (
                    session.query(MessageLog)
                    .filter(
                        MessageLog.user_id == user.id,
                        MessageLog.client_id == client.id,
                        MessageLog.template_type == reminder_type,
                        MessageLog.sent_at.between(start, end)
                    )
                    .first()
                )
                if already:
                    continue

                message_content = self._replace_template_variables(template.content, client)

                try:
                    result = whatsapp_service.send_message(client.phone_number, message_content, user.id)
                    status = 'sent' if result.get('success') else 'failed'
                    error_msg = result.get('error') if not result.get('success') else None
                except Exception as e:
                    status = 'failed'
                    error_msg = str(e)

                message_log = MessageLog(
                    user_id=user.id,
                    client_id=client.id,
                    template_type=reminder_type,
                    recipient_phone=client.phone_number,
                    message_content=message_content,
                    sent_at=naive_from_sp(now_sp()),
                    status=status,
                    error_message=error_msg
                )
                session.add(message_log)

            session.commit()
            logger.info(f"✅ Completed sending {reminder_type} reminders")

        except Exception as e:
            logger.error(f"❌ Error in _send_reminders_by_type: {e}", exc_info=True)

    async def _process_daily_reminders_for_user(self, user_id):
        """Mantido para compatibilidade; use _process_daily_reminders_sync"""
        logger.info(f"🚀 DIRECT: Starting reminder processing for user {user_id}")
        try:
            result_container = {"success": False, "error": None}

            def direct_send():
                try:
                    from services.database_service import DatabaseService
                    from services.whatsapp_service import WhatsAppService
                    from models import Client, MessageTemplate, MessageLog

                    db = DatabaseService()
                    ws = WhatsAppService()
                    today = today_sp()
                    tomorrow = today + timedelta(days=1)

                    with db.get_session() as session:
                        clients = (
                            session.query(Client)
                            .filter(
                                Client.user_id == user_id,
                                Client.status == 'active',
                                Client.auto_reminders_enabled == True,
                                Client.due_date == tomorrow
                            ).all()
                        )

                        if not clients:
                            result_container["success"] = True
                            return

                        template = (
                            session.query(MessageTemplate)
                            .filter_by(user_id=user_id, template_type='reminder_1_day', is_active=True)
                            .first()
                        )
                        if not template:
                            result_container["error"] = "No template"
                            return

                        for client in clients:
                            message_content = self._replace_template_variables(template.content, client)
                            try:
                                result = ws.send_message(client.phone_number, message_content, user_id)
                                status = 'sent' if result.get('success') else 'failed'
                                error_msg = result.get('error') if not result.get('success') else None
                            except Exception as e:
                                status = 'failed'
                                error_msg = str(e)

                            msg_log = MessageLog(
                                user_id=user_id,
                                client_id=client.id,
                                template_type='reminder_1_day',
                                recipient_phone=client.phone_number,
                                message_content=message_content,
                                sent_at=naive_from_sp(now_sp()),
                                status=status,
                                error_message=error_msg
                            )
                            session.add(msg_log)

                        session.commit()
                        result_container["success"] = True
                except Exception as e:
                    logger.error(f"❌ DIRECT: Error in thread: {e}")
                    result_container["error"] = str(e)

            thread = threading.Thread(target=direct_send)
            thread.start()
            thread.join(timeout=25)

            if thread.is_alive():
                logger.error(f"❌ DIRECT: Thread timeout for user {user_id}")
            elif result_container["success"]:
                logger.info(f"✅ DIRECT: Success for user {user_id}")
            elif result_container["error"]:
                logger.error(f"❌ DIRECT: Error for user {user_id}: {result_container['error']}")
        except Exception as e:
            logger.error(f"❌ DIRECT: Main error for user {user_id}: {e}", exc_info=True)

    def _process_daily_reminders_sync(self, user_id):
        """COMPLETAMENTE SÍNCRONO — padronizado em SP"""
        logger.info(f"🚀 SYNC: Starting reminder processing for user {user_id}")

        try:
            from services.database_service import DatabaseService
            from services.whatsapp_service import WhatsAppService
            from models import Client, MessageTemplate, MessageLog
            from sqlalchemy import func

            db = DatabaseService()
            ws = WhatsAppService()
            today = today_sp()
            tomorrow = today + timedelta(days=1)
            day_after_tomorrow = today + timedelta(days=2)

            with db.get_session() as session:
                reminder_groups = {
                    'reminder_2_days': session.query(Client).filter(
                        Client.user_id == user_id,
                        Client.status == 'active',
                        Client.auto_reminders_enabled == True,
                        Client.due_date == day_after_tomorrow
                    ).all(),
                    'reminder_1_day': session.query(Client).filter(
                        Client.user_id == user_id,
                        Client.status == 'active',
                        Client.auto_reminders_enabled == True,
                        Client.due_date == tomorrow
                    ).all(),
                    'reminder_due_date': session.query(Client).filter(
                        Client.user_id == user_id,
                        Client.status == 'active',
                        Client.auto_reminders_enabled == True,
                        Client.due_date == today
                    ).all(),
                    'reminder_overdue': session.query(Client).filter(
                        Client.user_id == user_id,
                        Client.status == 'active',
                        Client.auto_reminders_enabled == True,
                        Client.due_date < today
                    ).all()
                }

                total_clients = sum(len(v) for v in reminder_groups.values())
                logger.info(f"🔄 SYNC: Found {total_clients} clients eligible for reminders for user {user_id}")

                if total_clients == 0:
                    logger.info(f"✅ SYNC: No clients eligible for reminders for user {user_id}")
                    return

                start, end = today_bounds_sp()

                for reminder_type, clients in reminder_groups.items():
                    if not clients:
                        continue

                    logger.info(f"🔔 SYNC: Processing {len(clients)} clients for {reminder_type}")

                    template = (
                        session.query(MessageTemplate)
                        .filter_by(user_id=user_id, template_type=reminder_type, is_active=True)
                        .first()
                    )
                    if not template:
                        logger.warning(f"❌ SYNC: No {reminder_type} template found for user {user_id}")
                        continue

                    for client in clients:
                        # não duplicar no mesmo dia (SP)
                        existing_log = (
                            session.query(MessageLog)
                            .filter(
                                MessageLog.user_id == user_id,
                                MessageLog.client_id == client.id,
                                MessageLog.template_type == reminder_type,
                                MessageLog.sent_at.between(start, end),
                                MessageLog.status == 'sent'
                            )
                            .first()
                        )
                        if existing_log:
                            logger.info(f"⏩ SYNC: SKIPPING {client.name} - {reminder_type} already sent today")
                            continue

                        message_content = self._replace_template_variables(template.content, client)

                        try:
                            result = ws.send_message(client.phone_number, message_content, user_id)
                            status = 'sent' if result.get('success') else 'failed'
                            error_msg = result.get('error') if not result.get('success') else None
                        except Exception as e:
                            status = 'failed'
                            error_msg = str(e)
                            logger.error(f"❌ SYNC: Send failed for {client.name}: {e}")

                        try:
                            msg_log = MessageLog(
                                user_id=user_id,
                                client_id=client.id,
                                template_type=reminder_type,
                                recipient_phone=client.phone_number,
                                message_content=message_content,
                                sent_at=naive_from_sp(now_sp()),
                                status=status,
                                error_message=error_msg
                            )
                            session.add(msg_log)

                            if status == 'sent':
                                try:
                                    # se for DATE:
                                    client.last_reminder_sent = today_sp()
                                except Exception as e:
                                    logger.warning(f"Could not update last_reminder_sent: {e}")

                        except Exception as e:
                            logger.error(f"❌ SYNC: Log error for {client.name}: {e}")

                session.commit()
                logger.info(f"✅ SYNC: Completed processing {total_clients} clients for user {user_id}")

        except Exception as e:
            logger.error(f"❌ SYNC: Main error for user {user_id}: {e}")
            logger.error(f"SYNC traceback: {traceback.format_exc()}")

    async def _send_simple_reminders(self, session, user, clients, reminder_type):
        """Simplified reminder sending without complex checks"""
        from models import MessageTemplate, MessageLog
        from services.whatsapp_service import whatsapp_service

        logger.info(f"📤 Sending {reminder_type} to {len(clients)} clients")

        try:
            template = (
                session.query(MessageTemplate)
                .filter_by(user_id=user.id, template_type=reminder_type, is_active=True)
                .first()
            )
            if not template:
                logger.warning(f"❌ No template for {reminder_type}")
                return

            start, end = today_bounds_sp()

            for client in clients:
                message_content = self._replace_template_variables(template.content, client)

                try:
                    result = whatsapp_service.send_message(client.phone_number, message_content, user.id)
                    status = 'sent' if result.get('success') else 'failed'
                    error_msg = result.get('error') if not result.get('success') else None
                except Exception as e:
                    status = 'failed'
                    error_msg = str(e)

                msg_log = MessageLog(
                    user_id=user.id,
                    client_id=client.id,
                    template_type=reminder_type,
                    recipient_phone=client.phone_number,
                    message_content=message_content,
                    sent_at=naive_from_sp(now_sp()),
                    status=status,
                    error_message=error_msg
                )
                session.add(msg_log)

            session.commit()
            logger.info(f"✅ Completed sending {reminder_type} reminders")

        except Exception as e:
            logger.error(f"❌ Error in _send_simple_reminders: {e}", exc_info=True)

    async def _process_user_notifications_for_user(self, user_id):
        """Process daily user notifications for specific user"""
        try:
            from services.database_service import DatabaseService
            from services.telegram_service import telegram_service
            from models import User, Client

            db_service = DatabaseService()

            with db_service.get_session() as session:
                user = session.query(User).filter_by(id=user_id, is_active=True).first()
                if not user:
                    return

                clients = session.query(Client).filter_by(user_id=user.id).all()
                if not clients:
                    return

                today = today_sp()
                tomorrow = today + timedelta(days=1)
                day_after = today + timedelta(days=2)

                overdue = [c for c in clients if c.due_date and c.due_date < today and c.status == 'active']
                due_today = [c for c in clients if c.due_date and c.due_date == today and c.status == 'active']
                due_tomorrow = [c for c in clients if c.due_date and c.due_date == tomorrow and c.status == 'active']
                due_in_2_days = [c for c in clients if c.due_date and c.due_date == day_after and c.status == 'active']

                if overdue or due_today or due_tomorrow or due_in_2_days:
                    notification_text = self._build_notification_message(
                        overdue, due_today, due_tomorrow, due_in_2_days
                    )
                    await telegram_service.send_notification(str(user.telegram_id), notification_text)
                    logger.info(f"Sent daily notification to user {user.telegram_id}")

        except Exception as e:
            logger.error(f"Error processing daily notifications for user {user_id}: {str(e)}")
            logger.error(f"Full traceback: {traceback.format_exc()}")

    async def _send_daily_sending_report(self, session, user):
        """Send daily report of automated message sending results"""
        try:
            from services.telegram_service import telegram_service
            from models import MessageLog, Client

            today = today_sp()
            start, end = today_bounds_sp()

            today_logs = (
                session.query(MessageLog)
                .filter(
                    MessageLog.user_id == user.id,
                    MessageLog.sent_at.between(start, end),
                    MessageLog.template_type.in_(
                        ['reminder_2_days', 'reminder_1_day', 'reminder_due_date', 'reminder_overdue']
                    )
                ).all()
            )

            if not today_logs:
                return

            sent_logs = [log for log in today_logs if log.status == 'sent']
            failed_logs = [log for log in today_logs if log.status == 'failed']

            client_ids = list(set([log.client_id for log in today_logs if log.client_id]))
            clients_dict = {}
            if client_ids:
                clients = session.query(Client).filter(Client.id.in_(client_ids)).all()
                clients_dict = {c.id: c for c in clients}

            report_text = f"📊 **RELATÓRIO DIÁRIO DE ENVIOS AUTOMÁTICOS**\n"
            report_text += f"📅 Data: {today.strftime('%d/%m/%Y')}\n\n"

            report_text += f"📈 **RESUMO GERAL:**\n"
            report_text += f"✅ Envios com sucesso: **{len(sent_logs)}**\n"
            report_text += f"❌ Envios que falharam: **{len(failed_logs)}**\n"
            report_text += f"📊 Total de envios: **{len(today_logs)}**\n\n"

            if sent_logs:
                report_text += f"✅ **MENSAGENS ENVIADAS COM SUCESSO:**\n"
                by_type = {}
                for log in sent_logs:
                    by_type.setdefault(log.template_type, []).append(log)

                type_names = {
                    'reminder_2_days': '📅 Lembrete 2 dias antes',
                    'reminder_1_day': '⏰ Lembrete 1 dia antes',
                    'reminder_due_date': '🚨 Lembrete vencimento hoje',
                    'reminder_overdue': '💸 Lembrete em atraso'
                }

                for reminder_type, logs in by_type.items():
                    type_name = type_names.get(reminder_type, reminder_type)
                    report_text += f"\n{type_name}:\n"
                    for log in logs[:5]:
                        client = clients_dict.get(log.client_id)
                        client_name = client.name if client else f"ID:{log.client_id}"
                        phone = log.recipient_phone
                        timestamp = (log.sent_at or naive_from_sp(now_sp())).strftime('%H:%M')
                        report_text += f"• {client_name} ({phone}) - {timestamp}\n"
                    if len(logs) > 5:
                        report_text += f"• ... e mais {len(logs) - 5} clientes\n"
                report_text += "\n"

            if failed_logs:
                report_text += f"❌ **MENSAGENS QUE FALHARAM:**\n"
                for log in failed_logs[:8]:
                    client = clients_dict.get(log.client_id)
                    client_name = client.name if client else f"ID:{log.client_id}"
                    phone = log.recipient_phone
                    error = log.error_message or 'Erro desconhecido'
                    timestamp = (log.sent_at or naive_from_sp(now_sp())).strftime('%H:%M')
                    report_text += f"• {client_name} ({phone}) - {timestamp}\n"
                    report_text += f"  💬 Erro: {error}\n\n"
                if len(failed_logs) > 8:
                    report_text += f"• ... e mais {len(failed_logs) - 8} falhas\n\n"

            report_text += f"🎯 **Próximo envio automático:** Amanhã no horário configurado\n"
            report_text += f"📋 **Ver histórico completo:** Menu → 👥 Clientes → Ver cliente → 📜 Histórico"

            await telegram_service.send_notification(str(user.telegram_id), report_text)
            logger.info(f"Sent daily sending report to user {user.telegram_id}: {len(sent_logs)} success, {len(failed_logs)} failed")

        except Exception as e:
            logger.error(f"Error sending daily sending report for user {user.id}: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")

    def _check_trial_expiration(self, user, current_date):
        """Check if user's trial period has expired and send payment notification"""
        try:
            if not user.is_trial:
                return

            trial_end_date = user.created_at.date() + timedelta(days=7)
            days_until_expiry = (trial_end_date - current_date).days

            if days_until_expiry <= 0 and user.is_active:
                logger.info(f"Trial expired for user {user.id}, sending payment notification")

                from services.database_service import DatabaseService
                db_service = DatabaseService()

                with db_service.get_session() as session:
                    db_user = session.query(type(user)).filter_by(id=user.id).first()
                    if db_user:
                        db_user.is_active = False
                        session.commit()

                        future = asyncio.run_coroutine_threadsafe(
                            self._send_payment_notification(user.telegram_id),
                            self._get_event_loop()
                        )
                        try:
                            future.result(timeout=15)
                        except Exception as e:
                            logger.error(f"Error sending payment notification: {e}")

            elif days_until_expiry == 1:
                logger.info(f"Sending trial expiry reminder for user {user.id} (1 day left)")
                future = asyncio.run_coroutine_threadsafe(
                    self._send_trial_reminder(user.telegram_id, days_until_expiry),
                    self._get_event_loop()
                )
                try:
                    future.result(timeout=15)
                except Exception as e:
                    logger.error(f"Error sending trial reminder: {e}")

        except Exception as e:
            logger.error(f"Error checking trial expiration for user {user.id}: {e}", exc_info=True)

    async def _send_payment_notification(self, telegram_id):
        """Send payment notification when trial expires"""
        try:
            from services.telegram_service import telegram_service

            message = """
⚠️ **Seu período de teste expirou!**

Seu teste gratuito de 7 dias chegou ao fim. Para continuar usando todas as funcionalidades do bot, você precisa ativar a assinatura mensal.

💰 **Assinatura:** R$ 20,00/mês
✅ **Inclui:**
• Gestão ilimitada de clientes
• Lembretes automáticos via WhatsApp  
• Controle de vencimentos
• Relatórios detalhados
• Suporte prioritário

🔗 Use o comando /start para assinar e reativar sua conta!
"""
            await telegram_service.send_notification(telegram_id, message)
            logger.info(f"Payment notification sent to user {telegram_id}")

        except Exception as e:
            logger.error(f"Error sending payment notification: {e}", exc_info=True)

    async def _send_trial_reminder(self, telegram_id, days_left):
        """Send trial expiry reminder"""
        try:
            from services.telegram_service import telegram_service

            message = f"""
⏰ **Lembrete: Seu teste expira em {days_left} dia(s)!**

Seu período gratuito está chegando ao fim. Não perca o acesso às suas funcionalidades!

💰 **Assinatura:** R$ 20,00/mês
🎯 **Mantenha:**
• Todos os seus clientes cadastrados
• Lembretes automáticos configurados
• Histórico de mensagens

Para assinar e garantir a continuidade, use o comando /start quando seu teste expirar.
"""
            await telegram_service.send_notification(telegram_id, message)
            logger.info(f"Trial reminder sent to user {telegram_id}")

        except Exception as e:
            logger.error(f"Error sending trial reminder: {e}", exc_info=True)


# Global scheduler service instance
scheduler_service = SchedulerService()
