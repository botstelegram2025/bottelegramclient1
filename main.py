import logging
import os
import asyncio
from datetime import datetime, date, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes
)

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Phone number utility function
def normalize_brazilian_phone(phone_number: str) -> str:
    """
    Normalize Brazilian phone numbers for Baileys compatibility.
    Removes 9th digit from mobile numbers to match old format.
    
    Examples:
    - '11987654321' -> '1187654321' (removes 9th digit)
    - '1187654321' -> '1187654321' (already correct)
    - '11 9 8765-4321' -> '1187654321' (cleans and removes 9th)
    """
    if not phone_number:
        return ''
    
    # Remove all non-digit characters
    clean_phone = ''.join(filter(str.isdigit, phone_number))
    
    # Remove country code if present
    if clean_phone.startswith('55'):
        clean_phone = clean_phone[2:]
    
    # Handle different phone formats
    if len(clean_phone) == 11:  # DDD + 9 + 8 digits (new format)
        # Remove the 9th digit (3rd position after DDD)
        ddd = clean_phone[:2]
        remaining = clean_phone[3:]  # Skip the 9th digit
        clean_phone = ddd + remaining
    elif len(clean_phone) == 10:  # DDD + 8 digits (old format) - already correct
        pass
    elif len(clean_phone) == 9:  # 9 + 8 digits (missing DDD)
        # Default to São Paulo (11) if no DDD provided
        clean_phone = '11' + clean_phone[1:]  # Remove the 9 and add DDD
    elif len(clean_phone) == 8:  # 8 digits (missing DDD and 9)
        # Default to São Paulo (11)
        clean_phone = '11' + clean_phone
    
    # Ensure we have exactly 10 digits (DDD + 8)
    if len(clean_phone) != 10:
        # If still not 10 digits, return original cleaned number
        return ''.join(filter(str.isdigit, phone_number))
    
    return clean_phone

# Import configurations and services
from config import Config
from services.database_service import db_service
from services.scheduler_service import scheduler_service
from services.whatsapp_service import whatsapp_service
from services.payment_service import payment_service
from models import User, Client, Subscription, MessageTemplate, MessageLog


from flask import Flask, request, jsonify

# ---- SAFE GUARD: ensure helper exists even if removed by merge ----
if "_format_pix_copy_code" not in globals():
    def _format_pix_copy_code(code: str, chunk: int = 36) -> str:
        try:
            s = str(code or "").strip()
            if not s:
                return ""
            return "\n".join(s[i:i+chunk] for i in range(0, len(s), chunk))
        except Exception:
            return str(code)
# ----------------------------------------------------------------------------


# Conversation states
WAITING_FOR_PHONE = 1
WAITING_CLIENT_NAME = 2
WAITING_CLIENT_PHONE = 3
WAITING_CLIENT_PACKAGE = 4
WAITING_CLIENT_PLAN = 5
WAITING_CLIENT_PRICE_SELECTION = 6
WAITING_CLIENT_PRICE = 7
WAITING_CLIENT_SERVER = 8
WAITING_CLIENT_DUE_DATE_SELECTION = 9
WAITING_CLIENT_DUE_DATE = 10
WAITING_CLIENT_OTHER_INFO = 11

# Edit client states
EDIT_WAITING_FIELD = 12
EDIT_WAITING_NAME = 13
EDIT_WAITING_PHONE = 14
EDIT_WAITING_PACKAGE = 15
EDIT_WAITING_PRICE = 16
EDIT_WAITING_SERVER = 17
EDIT_WAITING_DUE_DATE = 18
EDIT_WAITING_OTHER_INFO = 19

# Renew client states
RENEW_WAITING_CUSTOM_DATE = 20
RENEW_WAITING_SEND_MESSAGE = 21

# Template states
TEMPLATE_WAITING_TYPE = 22
TEMPLATE_WAITING_NAME = 23
TEMPLATE_WAITING_CONTENT = 24

# Schedule configuration states
SCHEDULE_WAITING_MORNING_TIME = 25
SCHEDULE_WAITING_REPORT_TIME = 26

# Main menu keyboard
def get_main_keyboard(db_user=None):
    """Get main menu persistent keyboard"""
    keyboard = [
        [KeyboardButton("👥 Clientes"), KeyboardButton("📊 Dashboard")],
        [KeyboardButton("📋 Ver Templates"), KeyboardButton("⏰ Horários")],
        [KeyboardButton("💳 Assinatura"), KeyboardButton("🚀 Forçar Hoje")],
        [KeyboardButton("📱 WhatsApp"), KeyboardButton("❓ Ajuda")]
    ]
    
    # Add early payment button for trial users
    if db_user and db_user.is_trial and db_user.is_active:
        keyboard.insert(-1, [KeyboardButton("🚀 PAGAMENTO ANTECIPADO")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# Client management keyboard
def get_client_keyboard():
    """Get client management persistent keyboard"""
    keyboard = [
        [KeyboardButton("➕ Adicionar Cliente"), KeyboardButton("📋 Ver Clientes")],
        [KeyboardButton("📊 Dashboard"), KeyboardButton("🏠 Menu Principal")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_price_selection_keyboard():
    """Get price selection keyboard"""
    keyboard = [
        [KeyboardButton("💰 R$ 25"), KeyboardButton("💰 R$ 30"), KeyboardButton("💰 R$ 35")],
        [KeyboardButton("💰 R$ 40"), KeyboardButton("💰 R$ 45"), KeyboardButton("💰 R$ 50")],
        [KeyboardButton("💰 R$ 60"), KeyboardButton("💰 R$ 70"), KeyboardButton("💰 R$ 90")],
        [KeyboardButton("💸 Outro valor")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_server_keyboard():
    """Get server selection keyboard"""
    keyboard = [
        [KeyboardButton("🖥️ FAST TV"), KeyboardButton("🖥️ EITV"), KeyboardButton("🖥️ ZTECH")],
        [KeyboardButton("🖥️ UNITV"), KeyboardButton("🖥️ GENIAL"), KeyboardButton("🖥️ SLIM PLAY")],
        [KeyboardButton("🖥️ LIVE 21"), KeyboardButton("🖥️ X SERVER")],
        [KeyboardButton("📦 OUTRO SERVIDOR")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_name_keyboard():
    """Get keyboard for adding client name step"""
    keyboard = [
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_phone_keyboard():
    """Get keyboard for adding client phone step"""
    keyboard = [
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_package_keyboard():
    """Get keyboard for package selection"""
    keyboard = [
        [KeyboardButton("📅 MENSAL"), KeyboardButton("📅 TRIMESTRAL")],
        [KeyboardButton("📅 SEMESTRAL"), KeyboardButton("📅 ANUAL")],
        [KeyboardButton("📦 Outros pacotes")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_plan_keyboard():
    """Get keyboard for custom plan name"""
    keyboard = [
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_custom_price_keyboard():
    """Get keyboard for custom price input"""
    keyboard = [
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_due_date_keyboard():
    """Get keyboard for custom due date input"""
    keyboard = [
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_other_info_keyboard():
    """Get keyboard for other info input"""
    keyboard = [
        [KeyboardButton("Pular")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_due_date_keyboard(months):
    """Get due date selection keyboard based on package"""
    from datetime import datetime, timedelta
    
    today = datetime.now()
    
    # Calculate dates based on package
    if months == 1:  # Mensal
        date1 = today + timedelta(days=30)
        date2 = today + timedelta(days=31)
        label1 = f"📅 {date1.strftime('%d/%m/%Y')} (30 dias)"
        label2 = f"📅 {date2.strftime('%d/%m/%Y')} (31 dias)"
    elif months == 3:  # Trimestral
        date1 = today + timedelta(days=90)
        date2 = today + timedelta(days=91)
        label1 = f"📅 {date1.strftime('%d/%m/%Y')} (3 meses)"
        label2 = f"📅 {date2.strftime('%d/%m/%Y')} (3 meses +1)"
    elif months == 6:  # Semestral
        date1 = today + timedelta(days=180)
        date2 = today + timedelta(days=181)
        label1 = f"📅 {date1.strftime('%d/%m/%Y')} (6 meses)"
        label2 = f"📅 {date2.strftime('%d/%m/%Y')} (6 meses +1)"
    elif months == 12:  # Anual
        date1 = today + timedelta(days=365)
        date2 = today + timedelta(days=366)
        label1 = f"📅 {date1.strftime('%d/%m/%Y')} (1 ano)"
        label2 = f"📅 {date2.strftime('%d/%m/%Y')} (1 ano +1)"
    else:  # Outro/padrão
        date1 = today + timedelta(days=30)
        date2 = today + timedelta(days=31)
        label1 = f"📅 {date1.strftime('%d/%m/%Y')} (30 dias)"
        label2 = f"📅 {date2.strftime('%d/%m/%Y')} (31 dias)"
    
    keyboard = [
        [KeyboardButton(label1)],
        [KeyboardButton(label2)],
        [KeyboardButton("📝 Outra data")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# Bot Handlers

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    if not update.effective_user:
        return
        
    user = update.effective_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if db_user:
                # Check if user is active
                if db_user.is_active:
                    await show_main_menu(update, context)
                else:
                    # User exists but inactive (trial expired)
                    await show_reactivation_screen(update, context)
            else:
                return await start_registration(update, context)
                
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        if update.message:
            await update.message.reply_text("❌ Erro interno. Tente novamente.")

async def show_reactivation_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show payment options for expired trial users"""
    user = update.effective_user
    
    message = f"""
⚠️ **Olá {user.first_name}, sua conta está inativa!**

Seu período de teste gratuito de 7 dias expirou. Para continuar usando todas as funcionalidades do bot, você precisa ativar a assinatura mensal.

💰 **Assinatura:** R$ 20,00/mês via PIX
✅ **Inclui:**
• Gestão ilimitada de clientes
• Lembretes automáticos via WhatsApp  
• Controle de vencimentos
• Relatórios detalhados
• Suporte prioritário

🎯 **Seus dados permanecem salvos!**
Todos os clientes e configurações já cadastradas serão mantidos após a ativação.

Deseja reativar sua conta?
"""
    
    keyboard = [
        [InlineKeyboardButton("💳 Assinar Agora (PIX)", callback_data="subscribe_now")],
        [InlineKeyboardButton("📋 Ver Detalhes", callback_data="subscription_info")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(
            message,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start user registration process"""
    user = update.effective_user
    
    welcome_message = f"""
🎉 **Bem-vindo ao Bot de Gestão de Clientes!**

Olá {user.first_name}! 

Este bot te ajuda a:
✅ Gerenciar seus clientes
✅ Enviar lembretes automáticos via WhatsApp
✅ Controlar vencimentos de planos
✅ Receber pagamentos via PIX

🆓 **Teste Grátis por 7 dias!**
Após o período de teste, a assinatura custa apenas R$ 20,00/mês.

📱 Para continuar, preciso do seu número de telefone.
Digite seu número com DDD (ex: 11999999999):
"""
    
    if update.message:
        await update.message.reply_text(welcome_message, parse_mode='Markdown')
    return WAITING_FOR_PHONE

# --- Accept Telegram contact during registration (minimal patch) ---
async def handle_phone_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Aceita contato compartilhado pelo Telegram (update.message.contact.phone_number)
    e reutiliza a lógica do handle_phone_number.
    """
    if not update.message or not update.message.contact:
        if update.message:
            await update.message.reply_text("❌ Não recebi um contato válido. Envie seu número ou compartilhe o contato.")
        return WAITING_FOR_PHONE

    contact_number = update.message.contact.phone_number or ""
    # Reaproveita a validação existente: simula como se fosse texto
    update.message.text = contact_number
    return await handle_phone_number(update, context)


async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle phone number input during registration (safe session usage)"""
    if not update.effective_user or not update.message:
        return ConversationHandler.END

    user = update.effective_user
    logger.info(f"🔄 REGISTRATION: Processing phone number for user {user.id}")

    phone_number = update.message.text or ""
    normalized_phone = normalize_brazilian_phone(phone_number)
    if len(normalized_phone) < 10 or len(normalized_phone) > 11:
        await update.message.reply_text(
            "❌ Número inválido. Digite apenas números com DDD.\n**Exemplo:** 11999999999",
            parse_mode='Markdown'
        )
        return WAITING_FOR_PHONE

    clean_phone = normalized_phone

    # Prepare locals to avoid using ORM instance outside session
    trial_start = datetime.utcnow()
    trial_end = trial_start + timedelta(days=7)
    trial_end_str = trial_end.strftime('%d/%m/%Y às %H:%M')
    new_user_id = None

    try:
        with db_service.get_session() as session:
            existing_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            if existing_user:
                logger.warning(f"User {user.id} already exists")
                await update.message.reply_text("❌ Usuário já cadastrado. Use /start para acessar o menu.")
                return ConversationHandler.END

            new_user = User(
                telegram_id=str(user.id),
                first_name=user.first_name or 'Usuário',
                last_name=user.last_name or '',
                username=user.username or '',
                phone_number=clean_phone,
                trial_start_date=trial_start,
                trial_end_date=trial_end,
                is_trial=True,
                is_active=True
            )
            session.add(new_user)
            session.flush()
            new_user_id = new_user.id
            session.commit()

        # Create defaults in a new session (avoid stale bindings)
        if new_user_id:
            try:
                await create_default_templates_in_db(new_user_id)
            except Exception as e:
                logger.error(f"Error creating templates for user {new_user_id}: {e}")

        success_message = f"""
✅ **Cadastro realizado com sucesso!**

🆓 Seu período de teste de 7 dias já começou!
📅 Válido até: {trial_end_str}

🚀 **Próximos passos:**
1. Cadastre seus primeiros clientes
2. Configure os lembretes automáticos
3. Teste todas as funcionalidades

Use o teclado abaixo para começar:
"""
        await update.message.reply_text(success_message, parse_mode='Markdown')
        await show_main_menu(update, context)
        return ConversationHandler.END

    except Exception as e:
        logger.error(f"CRITICAL ERROR during user registration for {user.id}: {e}")
        error_msg = "❌ Erro ao cadastrar. "
        if "duplicate" in str(e).lower() or "unique" in str(e).lower():
            error_msg += "Usuário já existe. Use /start para acessar."
        else:
            error_msg += "Tente novamente em alguns segundos."
        await update.message.reply_text(error_msg)
        return WAITING_FOR_PHONE

async def force_process_reminders_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force process reminders for today - ADMIN FUNCTION"""
    if not update.effective_user:
        return
        
    user = update.effective_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await update.message.reply_text("❌ Conta inativa.")
                return
            
            # Reset user's morning time to 09:00 if needed
            from models import UserScheduleSettings
            schedule_settings = session.query(UserScheduleSettings).filter_by(
                user_id=db_user.id
            ).first()
            
            if not schedule_settings:
                schedule_settings = UserScheduleSettings(
                    user_id=db_user.id,
                    morning_reminder_time='09:00',
                    daily_report_time='08:00',
                    auto_send_enabled=True
                )
                session.add(schedule_settings)
            else:
                schedule_settings.morning_reminder_time = '09:00'
                schedule_settings.daily_report_time = '08:00'
                schedule_settings.auto_send_enabled = True
            
            session.commit()
            
            # Force process reminders now
            from services.scheduler_service import scheduler_service
            scheduler_service._process_daily_reminders_sync(db_user.id)
            
            await update.message.reply_text("""✅ **Lembretes Processados!**

🔧 **Ações realizadas:**
• Horário matinal definido para: **09:00**
• Relatório diário definido para: **08:00** 
• Processamento forçado de lembretes de hoje

📨 Verifique se os lembretes foram enviados!""", parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error forcing reminder processing: {e}")
        await update.message.reply_text("❌ Erro ao processar lembretes. Tente novamente.")

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main menu safely, without assuming created_at is populated."""
    if not update.effective_user:
        return
    user = update.effective_user
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            if not db_user:
                if update.message:
                    await update.message.reply_text("❌ Usuário não encontrado.")
                return

            # Compute status text safely
            status_text = "💎 Premium"
            trial_info = ""
            if getattr(db_user, "is_trial", False):
                status_text = "🎁 Teste"
                # Prefer trial_end_date when present
                days_left = 0
                try:
                    if getattr(db_user, "trial_end_date", None):
                        from datetime import datetime as _dt
                        days_left = max(0, (db_user.trial_end_date.date() - _dt.utcnow().date()).days)
                except Exception as _e:
                    days_left = 0
                if days_left:
                    trial_info = f" ({days_left} dias restantes)"

            menu_text = f"""
🏠 **Menu Principal**

👋 Olá, {user.first_name}!

📊 **Status:** {status_text}{trial_info}
{'⚠️ Conta inativa' if not getattr(db_user, 'is_active', True) else '✅ Conta ativa'}

O que deseja fazer?
"""
            reply_markup = get_main_keyboard(db_user)
            if update.message:
                await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')
            elif update.callback_query:
                await update.callback_query.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
        try:
            if update.message:
                await update.message.reply_text("✅ Cadastro concluído! Use o menu abaixo para continuar.", reply_markup=get_main_keyboard())
            elif update.callback_query and update.callback_query.message:
                await update.callback_query.message.reply_text("✅ Cadastro concluído! Use o menu abaixo para continuar.", reply_markup=get_main_keyboard())
        except Exception as _:
            pass

async def dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle dashboard callback"""
    if not update.callback_query or not update.callback_query.from_user:
        return
        
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user:
                await query.edit_message_text("❌ Usuário não encontrado.")
                return
            
            # Get statistics
            total_clients = session.query(Client).filter_by(user_id=db_user.id).count()
            active_clients = session.query(Client).filter_by(user_id=db_user.id, status='active').count()
            
            # Get clients expiring soon
            today = date.today()
            expiring_soon = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.due_date <= today + timedelta(days=7),
                Client.due_date >= today
            ).count()
            
            # Monthly statistics - current month
            from calendar import monthrange
            current_year = today.year
            current_month = today.month
            month_start = date(current_year, current_month, 1)
            month_end = date(current_year, current_month, monthrange(current_year, current_month)[1])
            
            # Monthly financial calculations - clients due this month
            clients_due_query = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.due_date >= month_start,
                Client.due_date <= month_end
            )
            clients_to_pay = clients_due_query.count()
            
            # Calculate total revenue for the month (all clients due)
            monthly_revenue_total = sum(client.plan_price or 0 for client in clients_due_query.all())
            
            # Clients that already paid this month (payment date within this month)
            clients_paid_query = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.last_payment_date >= month_start,
                Client.last_payment_date <= month_end
            )
            clients_paid = clients_paid_query.count()
            
            # Calculate revenue from clients who already paid this month
            revenue_paid = sum(client.plan_price or 0 for client in clients_paid_query.all())
            
            # Revenue still to be collected
            revenue_pending = monthly_revenue_total - revenue_paid
            
            # Get overdue clients (vencidos)
            overdue_clients = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.due_date < today
            ).count()
            overdue_revenue = sum(client.plan_price or 0 for client in session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.due_date < today
            ).all())
            
            # Get clients due today
            due_today = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.due_date == today
            ).count()
            due_today_revenue = sum(client.plan_price or 0 for client in session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.due_date == today
            ).all())
            
            # Get clients paid in last 30 days (renovados)
            from datetime import datetime
            thirty_days_ago = today - timedelta(days=30)
            clients_renewed = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.last_payment_date >= thirty_days_ago,
                Client.last_payment_date <= today
            ).count()
            renewal_revenue = sum(client.plan_price or 0 for client in session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.last_payment_date >= thirty_days_ago,
                Client.last_payment_date <= today
            ).all())
            
            # Get upcoming clients (next 7 days)
            upcoming_clients = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.due_date > today,
                Client.due_date <= today + timedelta(days=7)
            ).count()
            upcoming_revenue = sum(client.plan_price or 0 for client in session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.due_date > today,
                Client.due_date <= today + timedelta(days=7)
            ).all())
            
            # Annual statistics
            year_start = date(current_year, 1, 1)
            annual_paid = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.last_payment_date >= year_start,
                Client.last_payment_date <= today
            ).count()
            annual_revenue = sum(client.plan_price or 0 for client in session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active',
                Client.last_payment_date >= year_start,
                Client.last_payment_date <= today
            ).all())
            
            # Total potential annual revenue
            total_annual_potential = sum(client.plan_price * 12 if client.plan_price else 0 for client in session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.status == 'active'
            ).all())
            
            dashboard_text = f"""
📊 **Dashboard - Gestão Financeira**

👥 **Resumo Geral:**
• Total: {total_clients} clientes
• Ativos: {active_clients} | Inativos: {total_clients - active_clients}

💰 **Status de Pagamento:**
✅ **PAGOS (Renovados - 30 dias):** {clients_renewed} 
   💵 Recebido: R$ {renewal_revenue:.2f}

⏰ **VENCEM HOJE:** {due_today} 
   💰 A receber: R$ {due_today_revenue:.2f}

🔔 **PRÓXIMOS 7 DIAS:** {upcoming_clients}
   💰 A receber: R$ {upcoming_revenue:.2f}

❌ **VENCIDOS:** {overdue_clients}
   💸 Em atraso: R$ {overdue_revenue:.2f}

📈 **Resumo Financeiro:**
**Mês Atual ({month_start.strftime('%m/%Y')}):**
• 📈 Pagos: {clients_paid} (R$ {revenue_paid:.2f})
• 📋 A Pagar: {clients_to_pay - clients_paid} (R$ {revenue_pending:.2f})
• 💵 **Total Mensal**: R$ {monthly_revenue_total:.2f}

**Ano {current_year}:**
• 💰 Pagamentos recebidos: {annual_paid}
• 🏆 Receita anual: R$ {annual_revenue:.2f}
• 🎯 Potencial anual: R$ {total_annual_potential:.2f}

📱 **WhatsApp:**
• Status: {"✅ Conectado" if whatsapp_service.check_instance_status(db_user.id).get('connected') else "❌ Desconectado"}

💳 **Assinatura:**
• Status: {"🆓 Teste" if db_user.is_trial else "💎 Premium"}
"""
            
            dashboard_text += "\n📲 Use o teclado abaixo para navegar"
            
            reply_markup = get_main_keyboard()
            
            await query.message.reply_text(
                dashboard_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
    except Exception as e:
        logger.error(f"Error showing dashboard: {e}")
        await query.edit_message_text("❌ Erro ao carregar dashboard.")

async def manage_clients_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle manage clients callback"""
    if not update.callback_query or not update.callback_query.from_user:
        return
        
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user:
                await query.edit_message_text("❌ Usuário não encontrado.")
                return
            
            if not db_user.is_active:
                await query.edit_message_text("⚠️ Conta inativa. Assine o plano para continuar.")
                return
            
            # Get clients ordered by due date (descending - most urgent first)
            clients = session.query(Client).filter_by(user_id=db_user.id).order_by(Client.due_date.desc()).all()
            
            if not clients:
                text = """
👥 **Gerenciar Clientes**

📋 Nenhum cliente cadastrado ainda.

Comece adicionando seu primeiro cliente!
"""
                keyboard = [
                    [InlineKeyboardButton("➕ Adicionar Cliente", callback_data="add_client")],
                    [InlineKeyboardButton("🔍 Buscar Cliente", callback_data="search_client")],
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
                return
            
            # Create client list with inline buttons
            from datetime import date
            today = date.today()
            
            text = f"👥 **Gerenciar Clientes** ({len(clients)} total)\n\n📋 Selecione um cliente para gerenciar:"
            
            keyboard = []
            for client in clients:
                # Status indicator
                if client.status == 'active':
                    if client.due_date < today:
                        status = "🔴"  # Overdue
                    elif (client.due_date - today).days <= 7:
                        status = "🟡"  # Due soon
                    else:
                        status = "🟢"  # Active
                else:
                    status = "⚫"  # Inactive
                
                # Format button text
                due_str = client.due_date.strftime('%d/%m')
                button_text = f"{status} {client.name} - {due_str}"
                
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"client_{client.id}")])
            
            # Add navigation buttons
            keyboard.extend([
                [InlineKeyboardButton("➕ Adicionar Cliente", callback_data="add_client")],
                [InlineKeyboardButton("🔍 Buscar Cliente", callback_data="search_client")],
                [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error managing clients: {e}")
        await query.edit_message_text("❌ Erro ao carregar clientes.")

async def search_client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle search client callback - Ask user to type client name"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await query.edit_message_text("❌ Conta inativa.")
                return
            
            text = """🔍 **Buscar Cliente**

Digite o nome do cliente que você quer encontrar:

💡 *Pode digitar apenas parte do nome*"""
            
            keyboard = [
                [InlineKeyboardButton("🔙 Lista Clientes", callback_data="manage_clients")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            
            # Set user state for search
            context.user_data['searching_client'] = True
            
    except Exception as e:
        logger.error(f"Error starting client search: {e}")
        await query.edit_message_text("❌ Erro ao iniciar busca.")

async def process_client_search(update: Update, context: ContextTypes.DEFAULT_TYPE, search_term: str):
    """Process client search from user input"""
    if not update.effective_user:
        return
        
    user = update.effective_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await update.message.reply_text("❌ Conta inativa.")
                return
            
            # Import Client model
            from models import Client
            
            # Search clients by name (case insensitive) using ILIKE for PostgreSQL
            search_pattern = f"%{search_term}%"
            clients = session.query(Client).filter(
                Client.user_id == db_user.id,
                Client.name.ilike(search_pattern)
            ).order_by(Client.due_date.desc()).all()
            
            if not clients:
                text = f"""🔍 **Resultado da Busca**

❌ Nenhum cliente encontrado com "{search_term}"

Tente buscar com outro nome ou parte do nome."""
                
                keyboard = [
                    [InlineKeyboardButton("🔍 Buscar Novamente", callback_data="search_client")],
                    [InlineKeyboardButton("📋 Lista Clientes", callback_data="manage_clients")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
                return
            
            # Show search results
            from datetime import date
            today = date.today()
            
            text = f"""🔍 **Resultado da Busca**

Encontrados {len(clients)} cliente(s) com "{search_term}":"""
            
            keyboard = []
            for client in clients:
                # Status indicator
                if client.status == 'active':
                    if client.due_date < today:
                        status = "🔴"  # Overdue
                    elif (client.due_date - today).days <= 7:
                        status = "🟡"  # Due soon
                    else:
                        status = "🟢"  # Active
                else:
                    status = "⚫"  # Inactive
                
                # Format button text
                due_str = client.due_date.strftime('%d/%m')
                button_text = f"{status} {client.name} - {due_str}"
                
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"client_{client.id}")])
            
            # Add navigation buttons
            keyboard.extend([
                [InlineKeyboardButton("🔍 Buscar Novamente", callback_data="search_client")],
                [InlineKeyboardButton("📋 Lista Clientes", callback_data="manage_clients")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error searching clients: {e}")
        await update.message.reply_text("❌ Erro ao buscar clientes.")
    finally:
        # Clear search state
        if 'searching_client' in context.user_data:
            del context.user_data['searching_client']

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current conversation and return to main menu"""
    try:
        # Clear any user data
        context.user_data.clear()
        
        # Send cancellation message
        if update.message:
            await update.message.reply_text(
                "❌ **Operação cancelada.**\n\nVoltando ao menu principal...",
                parse_mode='Markdown'
            )
            await show_main_menu(update, context)
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "❌ **Operação cancelada.**\n\nVoltando ao menu principal...",
                parse_mode='Markdown'
            )
            # Show main menu in new message
            if update.effective_user:
                await show_main_menu_message(update.callback_query.message, context)
        
        logger.info(f"Conversation cancelled by user {update.effective_user.id if update.effective_user else 'Unknown'}")
        
    except Exception as e:
        logger.error(f"Error cancelling conversation: {e}")
    
    return ConversationHandler.END

async def show_main_menu_message(message, context):
    """Helper to show main menu as new message"""
    try:
        keyboard = get_main_keyboard()
        await message.reply_text(
            "🏠 **Menu Principal**\n\nEscolha uma opção:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")

async def add_client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle add client callback"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    text = """
➕ **Adicionar Cliente**

Vamos cadastrar um novo cliente! 

Por favor, envie o **nome do cliente**:
"""
    
    await query.edit_message_text(text, parse_mode='Markdown')
    
    # Send keyboard in a separate message
    await query.message.reply_text(
        "📝 **Digite o nome do cliente:**",
        reply_markup=get_add_client_name_keyboard(),
        parse_mode='Markdown'
    )
    
    return WAITING_CLIENT_NAME

async def handle_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client name input"""
    if not update.message:
        return
        
    client_name = update.message.text or ""
    client_name = client_name.strip()
    
    # Check for cancel/menu options
    if client_name in ["🔙 Cancelar", "🏠 Menu Principal", "Cancelar", "cancelar", "CANCELAR"]:
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    if len(client_name) < 2:
        await update.message.reply_text(
            "❌ Nome muito curto. Digite um nome válido.",
            reply_markup=get_add_client_name_keyboard()
        )
        return WAITING_CLIENT_NAME
    
    # Store client name in context
    context.user_data['client_name'] = client_name
    
    await update.message.reply_text(
        f"✅ Nome: **{client_name}**\n\n📱 Agora digite o número de telefone (com DDD):\n**Exemplo:** 11999999999",
        reply_markup=get_add_client_phone_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_PHONE

async def handle_client_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client phone input"""
    if not update.message:
        return
        
    phone_number = update.message.text or ""
    phone_number = phone_number.strip()
    
    # Check for cancel/menu options
    if phone_number in ["🔙 Cancelar", "🏠 Menu Principal", "Cancelar", "cancelar", "CANCELAR"]:
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    # Validate and normalize phone number
    normalized_phone = normalize_brazilian_phone(phone_number)
    if len(normalized_phone) < 10 or len(normalized_phone) > 11:
        await update.message.reply_text(
            "❌ Número inválido. Digite apenas números com DDD.\n**Exemplo:** 11999999999",
            reply_markup=get_add_client_phone_keyboard(),
            parse_mode='Markdown'
        )
        return WAITING_CLIENT_PHONE
    
    clean_phone = normalized_phone
    
    # Store phone in context
    context.user_data['client_phone'] = clean_phone
    
    await update.message.reply_text(
        f"✅ Telefone: **{clean_phone}**\n\n📦 Agora escolha o pacote:",
        reply_markup=get_add_client_package_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_PACKAGE

async def handle_client_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client package selection"""
    if not update.message:
        return
        
    package_text = update.message.text or ""
    package_text = package_text.strip()
    
    # Check for cancel
    if package_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    # Define package options and their values
    package_options = {
        "📅 MENSAL": ("Plano Mensal", 1),
        "📅 TRIMESTRAL": ("Plano Trimestral", 3),
        "📅 SEMESTRAL": ("Plano Semestral", 6),
        "📅 ANUAL": ("Plano Anual", 12),
        "📦 Outros pacotes": ("Outro", 0)
    }
    
    if package_text in package_options:
        plan_name, months = package_options[package_text]
        
        # Store package info in context
        context.user_data['client_package'] = package_text
        context.user_data['client_plan'] = plan_name
        context.user_data['client_months'] = months
        
        if package_text == "📦 Outros pacotes":
            # Ask for custom plan name
            await update.message.reply_text(
                f"✅ Pacote: **{package_text}**\n\n📦 Digite o nome do plano personalizado:\n**Exemplo:** Plano Básico",
                reply_markup=get_add_client_plan_keyboard(),
                parse_mode='Markdown'
            )
            return WAITING_CLIENT_PLAN
        else:
            # Go to price selection
            await update.message.reply_text(
                f"✅ Pacote: **{plan_name}**\n\n💰 Escolha o valor:",
                reply_markup=get_price_selection_keyboard(),
                parse_mode='Markdown'
            )
            return WAITING_CLIENT_PRICE_SELECTION
    else:
        # Invalid selection
        await update.message.reply_text(
            "❌ Opção inválida. Escolha uma das opções do teclado:",
            reply_markup=get_add_client_package_keyboard()
        )
        return WAITING_CLIENT_PACKAGE

async def handle_client_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client plan input"""
    if not update.message:
        return
        
    plan_name = update.message.text or ""
    plan_name = plan_name.strip()
    
    # Check for cancel
    if plan_name == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    if len(plan_name) < 2:
        await update.message.reply_text(
            "❌ Nome do plano muito curto. Digite um nome válido.",
            reply_markup=get_add_client_plan_keyboard()
        )
        return WAITING_CLIENT_PLAN
    
    # Store plan in context
    context.user_data['client_plan'] = plan_name
    
    await update.message.reply_text(
        f"✅ Plano: **{plan_name}**\n\n💰 Escolha o valor:",
        reply_markup=get_price_selection_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_PRICE_SELECTION

async def handle_client_price_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client price selection"""
    if not update.message:
        return
        
    price_text = update.message.text or ""
    price_text = price_text.strip()
    
    # Check for cancel
    if price_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    # Define price options
    price_options = {
        "💰 R$ 25": 25.0,
        "💰 R$ 30": 30.0,
        "💰 R$ 35": 35.0,
        "💰 R$ 40": 40.0,
        "💰 R$ 45": 45.0,
        "💰 R$ 50": 50.0,
        "💰 R$ 60": 60.0,
        "💰 R$ 70": 70.0,
        "💰 R$ 90": 90.0,
        "💸 Outro valor": 0.0
    }
    
    if price_text in price_options:
        if price_text == "💸 Outro valor":
            # Ask for custom price
            await update.message.reply_text(
                f"✅ Opção: **{price_text}**\n\n💰 Digite o valor personalizado:\n**Exemplo:** 75.00",
                reply_markup=get_add_client_custom_price_keyboard(),
                parse_mode='Markdown'
            )
            return WAITING_CLIENT_PRICE
        else:
            # Use predefined price
            price = price_options[price_text]
            context.user_data['client_price'] = price
            
            await update.message.reply_text(
                f"✅ Valor: **R$ {price:.2f}**\n\n🖥️ Agora escolha o servidor:",
                reply_markup=get_server_keyboard(),
                parse_mode='Markdown'
            )
            return WAITING_CLIENT_SERVER
    else:
        # Invalid selection
        await update.message.reply_text(
            "❌ Opção inválida. Escolha uma das opções do teclado:",
            reply_markup=get_price_selection_keyboard()
        )
        return WAITING_CLIENT_PRICE_SELECTION

async def handle_client_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client price input"""
    if not update.message:
        return
        
    price_text = update.message.text or ""
    price_text = price_text.strip().replace(',', '.')
    
    # Check for cancel
    if price_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    # Handle custom price input - clean the text first
    import re
    
    # Remove all non-digit and non-decimal characters except comma and dot
    clean_price_text = re.sub(r'[^\d,.]', '', price_text)
    clean_price_text = clean_price_text.replace(',', '.')
    
    # Handle cases like "50" or "50.00" or "50,00"
    try:
        price = float(clean_price_text) if clean_price_text else 0
        if price <= 0:
            raise ValueError("Price must be positive")
    except ValueError:
        await update.message.reply_text(
            "❌ Valor inválido. Digite apenas números.\n**Exemplos:** 50 ou 50.00 ou 50,00",
            reply_markup=get_add_client_custom_price_keyboard()
        )
        return WAITING_CLIENT_PRICE
    
    # Store price in context
    context.user_data['client_price'] = price
    
    await update.message.reply_text(
        f"✅ Valor: **R$ {price:.2f}**\n\n🖥️ Agora escolha o servidor:",
        reply_markup=get_server_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_SERVER

async def handle_client_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client server selection"""
    if not update.message:
        return
        
    text = update.message.text or ""
    text = text.strip()
    
    # Check for cancel
    if text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    # Extract server name from button text
    if text.startswith("🖥️"):
        server = text.replace("🖥️ ", "")
    elif "OUTRO SERVIDOR" in text:
        await update.message.reply_text(
            "📦 Digite o nome do servidor:",
            reply_markup=get_add_client_plan_keyboard()
        )
        return WAITING_CLIENT_SERVER
    else:
        server = text  # Manual input
    
    # Store server selection
    context.user_data['client_server'] = server
    
    # Show date selection
    months = context.user_data.get('client_months', 1)
    await update.message.reply_text(
        f"✅ Servidor: **{server}**\n\n📅 Escolha a data de vencimento:",
        reply_markup=get_due_date_keyboard(months),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_DUE_DATE_SELECTION

async def handle_client_due_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client due date selection"""
    if not update.message:
        return
        
    date_text = update.message.text or ""
    date_text = date_text.strip()
    
    # Check for cancel
    if date_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    if date_text == "📝 Outra data":
        # Ask for custom date
        await update.message.reply_text(
            f"✅ Opção: **{date_text}**\n\n📅 Digite a data de vencimento (DD/MM/AAAA):\n**Exemplo:** 25/12/2024",
            reply_markup=get_add_client_due_date_keyboard(),
            parse_mode='Markdown'
        )
        return WAITING_CLIENT_DUE_DATE
    elif date_text.startswith("📅"):
        # Extract date from selected option
        import re
        from datetime import datetime
        
        # Extract date part (DD/MM/YYYY) from the button text
        date_match = re.search(r'(\d{2}/\d{2}/\d{4})', date_text)
        if date_match:
            try:
                date_str = date_match.group(1)
                due_date = datetime.strptime(date_str, '%d/%m/%Y').date()
                
                # Ask for other information
                context.user_data['client_due_date'] = due_date
                await update.message.reply_text(
                    f"✅ Data: **{due_date.strftime('%d/%m/%Y')}**\n\n📝 Digite outras informações (MAC, OTP, chaves, etc.):",
                    reply_markup=get_add_client_other_info_keyboard(),
                    parse_mode='Markdown'
                )
                return WAITING_CLIENT_OTHER_INFO
                
            except ValueError:
                await update.message.reply_text(
                    "❌ Erro ao processar data. Tente novamente:",
                    reply_markup=get_due_date_keyboard(context.user_data.get('client_months', 1))
                )
                return WAITING_CLIENT_DUE_DATE_SELECTION
    else:
        # Invalid selection
        await update.message.reply_text(
            "❌ Opção inválida. Escolha uma das opções do teclado:",
            reply_markup=get_due_date_keyboard(context.user_data.get('client_months', 1))
        )
        return WAITING_CLIENT_DUE_DATE_SELECTION

async def handle_client_due_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client due date input and save client"""
    if not update.message or not update.effective_user:
        return
        
    date_text = update.message.text or ""
    date_text = date_text.strip()
    
    # Check for cancel
    if date_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    try:
        due_date = datetime.strptime(date_text, '%d/%m/%Y').date()
        # Removed future date validation - now allows past dates
    except ValueError:
        await update.message.reply_text(
            "❌ Data inválida. Use o formato DD/MM/AAAA.\n**Exemplo:** 25/12/2024",
            reply_markup=get_add_client_due_date_keyboard()
        )
        return WAITING_CLIENT_DUE_DATE
    
    # Ask for other information
    context.user_data['client_due_date'] = due_date
    await update.message.reply_text(
        f"✅ Data: **{due_date.strftime('%d/%m/%Y')}**\n\n📝 Digite outras informações (MAC, OTP, chaves, etc.):",
        reply_markup=get_add_client_other_info_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_OTHER_INFO

async def handle_client_other_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client other information input and save client"""
    if not update.message or not update.effective_user:
        return
        
    other_info = update.message.text or ""
    other_info = other_info.strip()
    
    # Check for cancel
    if other_info == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    
    # If user wants to skip
    if other_info.lower() in ['pular', 'skip', ''] or other_info == "Pular":
        other_info = ""
    
    # Store other info
    context.user_data['client_other_info'] = other_info
    
    # Get due date from context
    due_date = context.user_data.get('client_due_date')
    
    # Save client to database
    await save_client_to_database(update, context, due_date)
    return ConversationHandler.END

async def save_client_to_database(update: Update, context: ContextTypes.DEFAULT_TYPE, due_date):
    """Save client to database"""
    user = update.effective_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await update.message.reply_text("❌ Conta inativa. Assine o plano para continuar.")
                return ConversationHandler.END
            
            # Get data from context
            client_name = context.user_data.get('client_name', '')
            client_phone = context.user_data.get('client_phone', '')
            client_plan = context.user_data.get('client_plan', '')
            client_price = context.user_data.get('client_price', 0)
            client_server = context.user_data.get('client_server', '')
            client_other_info = context.user_data.get('client_other_info', '')
            
            if not client_name or not client_phone or not client_plan or not client_price or not client_server:
                await update.message.reply_text("❌ Dados incompletos. Tente novamente.")
                return ConversationHandler.END
            
            # Create client
            client = Client(
                user_id=db_user.id,
                name=client_name,
                phone_number=client_phone,
                plan_name=client_plan,
                plan_price=client_price,
                reminder_status='pending',  # AUTO-ENTER REMINDER QUEUE!
                server=client_server,
                other_info=client_other_info,
                due_date=due_date,
                status='active'
            )
            
            session.add(client)
            session.commit()
            session.refresh(client)  # Refresh to get updated data
            
            # Send welcome message within session
            await send_welcome_message_with_session(session, client, db_user.id)
            
            # Build success message
            other_info_display = f"\n📝 {client.other_info}" if client.other_info else ""
            
            success_message = f"""
✅ **Cliente cadastrado com sucesso!**

👤 **{client.name}**
📱 {client.phone_number}
📦 {client.plan_name}
🖥️ {client.server}
💰 R$ {client.plan_price:.2f}
📅 Vence: {client.due_date.strftime('%d/%m/%Y')}{other_info_display}

📱 Mensagem de boas-vindas enviada via WhatsApp!
"""
            
            keyboard = [
                [InlineKeyboardButton("➕ Adicionar Outro", callback_data="add_client")],
                [InlineKeyboardButton("📋 Ver Clientes", callback_data="manage_clients")],
                [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(success_message, reply_markup=reply_markup, parse_mode='Markdown')
            
            # Clear context
            context.user_data.clear()
            
    except Exception as e:
        logger.error(f"Error saving client: {e}")
        await update.message.reply_text("❌ Erro ao cadastrar cliente. Tente novamente.")
        return ConversationHandler.END

async def subscription_info_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subscription info callback"""
    if not update.callback_query or not update.callback_query.from_user:
        return
        
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user:
                await query.edit_message_text("❌ Usuário não encontrado.")
                return
            
            # Get subscription info
            trial_days_left = 0
            if db_user.is_trial:
                # Calculate trial days based on created_at + 7 days
                trial_end = db_user.created_at.date() + timedelta(days=7)
                trial_days_left = max(0, (trial_end - datetime.utcnow().date()).days)
            
            subscription_days_left = 0
            if db_user.next_due_date:
                subscription_days_left = max(0, (db_user.next_due_date - datetime.utcnow()).days)
            
            if db_user.is_trial:
                status_text = f"""
💳 **Informações da Assinatura**

🎁 **Período de Teste Ativo**
📅 Dias restantes: **{trial_days_left}**

💎 **Plano Premium - R$ 20,00/mês**

✅ **Funcionalidades incluídas:**
• Gestão ilimitada de clientes
• Lembretes automáticos via WhatsApp  
• Controle de vencimentos
• Templates personalizáveis
• Suporte prioritário

{"⚠️ **Seu teste expira em breve!**" if trial_days_left <= 2 else ""}

💡 **Pode pagar antecipadamente para garantir continuidade!**
"""
                keyboard = [
                    [InlineKeyboardButton("💳 Assinar Agora (PIX)", callback_data="subscribe_now")],
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
                ]
            else:
                status_text = f"""
💳 **Informações da Assinatura**

💎 **Plano Premium Ativo**
💰 Valor: R$ 20,00/mês
📅 Próximo vencimento: {db_user.next_due_date.strftime('%d/%m/%Y') if db_user.next_due_date else 'N/A'}
⏰ Dias restantes: {subscription_days_left}

✅ **Status:** {'Ativa' if db_user.is_active else 'Inativa'}
"""
                keyboard = [
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
                ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(status_text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error showing subscription info: {e}")
        await query.edit_message_text("❌ Erro ao carregar informações da assinatura.")



# === PIX / Assinatura ===

# === PIX / Assinatura — compatível com create_subscription_payment ===

async def subscribe_now_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inicia pagamento via PIX e envia:
       1) QR Code (foto)
       2) Código copia-e-cola (mensagem separada, texto puro)
       3) Instruções e link (texto puro)
    """
    if not update.callback_query:
        return

    q = update.callback_query
    try:
        await q.answer()
    except Exception:
        pass

    try:
        with db_service.get_session() as session:
            tg_user = q.from_user
            db_user = session.query(User).filter_by(telegram_id=str(tg_user.id)).first()
            if not db_user:
                try:
                    await q.edit_message_text("❌ Usuário não encontrado. Use /start para se registrar.")
                except Exception:
                    pass
                return

            amount = getattr(Config, "MONTHLY_SUBSCRIPTION_PRICE", 20.00) or 20.00
            description = "Assinatura Mensal - Bot Gestor"

            # Tentativas de criação do pagamento
            result = None
            try:
                if hasattr(payment_service, "create_subscription_payment"):
                    try:
                        result = payment_service.create_subscription_payment(
                            user_telegram_id=str(tg_user.id), amount=amount, method="pix"
                        )
                    except TypeError:
                        result = payment_service.create_subscription_payment(str(tg_user.id), amount)
            except Exception as e:
                logger.error(f"create_subscription_payment error: {e}")

            # --- Auto-confirm watcher (polling Mercado Pago) ---
            try:
                __pid = None
                if result and isinstance(result, dict):
                    __pid = result.get("payment_id") or ((result.get("raw") or {}).get("id") if result.get("raw") else None)
                if __pid:
                    _spawn_payment_watch(str(__pid), int(tg_user.id))
            except Exception as __e:
                logger.error("[WATCH] failed to spawn watcher: " + str(__e))

            if not result:

                try:
                    if hasattr(payment_service, "create_pix_subscription"):
                        result = payment_service.create_pix_subscription(user_id=db_user.id, amount=amount, description=description)
                    elif hasattr(payment_service, "create_pix_payment"):
                        result = payment_service.create_pix_payment(user_id=db_user.id, amount=amount, description=description)
                    elif hasattr(payment_service, "create_payment"):
                        result = payment_service.create_payment(user_id=db_user.id, amount=amount, description=description, method="pix")
                except Exception as e:
                    logger.error(f"payment_service fallback error: {e}")
                    result = {"error": str(e)}

            raw = result or {}

            # Extract normalized fields
            def g(d, path, default=None):
                cur = d
                for part in path.split("."):
                    if not isinstance(cur, dict) or part not in cur:
                        return default
                    cur = cur[part]
                return cur

            tx = g(raw, "point_of_interaction.transaction_data", {}) or {}

            qr_b64 = (
                raw.get("qr_code_base64")
                or raw.get("qrCodeBase64")
                or tx.get("qr_code_base64")
                or tx.get("qr_code_base64_image")
                or g(raw, "transaction_data.qr_code_base64")
            )

            copia = (
                raw.get("copy_paste")
                or raw.get("copia_cola")
                or raw.get("pix_code")
                or raw.get("qr_code")  # alias
                or tx.get("qr_code")
                or g(raw, "transaction_data.qr_code")
            )

            link = (
                raw.get("payment_link")
                or raw.get("checkout_url")
                or tx.get("ticket_url")
                or tx.get("url")
                or raw.get("init_point")
            )

            # 0) Informe curto na mensagem original (não travar se falhar)
            try:
                await q.edit_message_text("✅ Enviamos abaixo as instruções e o QR Code/PIX.")
            except Exception as e:
                logger.error(f"edit original msg failed: {e}")

            # 1) QR Code (se houver)
            if qr_b64:
                try:
                    import base64, io
                    if isinstance(qr_b64, str) and qr_b64.startswith("data:image"):
                        qr_b64 = qr_b64.split(",")[1]
                    qr_bytes = base64.b64decode(qr_b64) if isinstance(qr_b64, str) else qr_b64
                    qr_photo = io.BytesIO(qr_bytes); qr_photo.name = "pix_qr_code.png"
                    await context.bot.send_photo(
                        chat_id=q.message.chat_id,
                        photo=qr_photo,
                        caption="📲 QR Code PIX\nEscaneie para pagar.",
                        parse_mode=None
                    )
                except Exception as e:
                    logger.error(f"send_photo failed: {e}")

            # 2) Copia-e-cola (sempre texto puro)
            if copia:
                try:
                    pretty = _format_pix_copy_code(copia)
                    await context.bot.send_message(
                        chat_id=q.message.chat_id,
                        text="📋 Copia e Cola PIX:\n```\n" + str(copia) + "\n```",
                        parse_mode="MarkdownV2"
                    )
                except Exception as e:
                    logger.error(f"send copy-paste failed: {e}")

            # 3) Instruções + link (texto puro)
            try:
                parts = [
                    "💳 Pagamento da Assinatura (PIX)",
                    f"Valor: R$ {amount:.2f}",
                    "",
                    "Pague usando uma das opções:"
                ]
                if link:
                    parts.append(f"Link de pagamento:\n{link}")
                await context.bot.send_message(
                    chat_id=q.message.chat_id,
                    text="\n".join(parts),
                    parse_mode=None,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]])
                )
            except Exception as e:
                logger.error(f"send instructions failed: {e}")

            return

    except Exception as e:
        logger.error(f"subscribe_now_callback fatal error: {e}")
        try:
            await update.callback_query.edit_message_text("❌ Erro ao iniciar pagamento. Tente novamente.")
        except Exception:
            pass

            def get_nested(d, path, default=None):
                cur = d
                for p in path.split("."):
                    if not isinstance(cur, dict) or p not in cur:
                        return default
                    cur = cur[p]
                return cur

            # Mercado Pago clássico
            mp_tx = get_nested(raw, "point_of_interaction.transaction_data", {}) or {}

            # QR base64 (imagem)
            qr_b64 = (
                raw.get("qr_code_base64")
                or raw.get("qrCodeBase64")
                or mp_tx.get("qr_code_base64")
                or mp_tx.get("qr_code_base64_image")
                or get_nested(raw, "transaction_data.qr_code_base64")
            )

            # Código copia-e-cola (alguns serviços chamam de qr_code)
            copia_cola = (
                raw.get("copy_paste")
                or raw.get("copia_cola")
                or raw.get("pix_code")
                or mp_tx.get("qr_code")
                or raw.get("qr_code")  # <-- importante p/ versões antigas
                or get_nested(raw, "transaction_data.qr_code")
            )

            # Link opcional
            pay_link = (
                raw.get("payment_link")
                or raw.get("checkout_url")
                or get_nested(raw, "point_of_interaction.transaction_data.ticket_url")
                or get_nested(raw, "point_of_interaction.transaction_data.url")
                or raw.get("init_point")
            )

            payment_id = (
                raw.get("payment_id")
                or raw.get("id")
                or raw.get("preference_id")
                or get_nested(raw, "transaction_data.external_reference")
            )

            # Heurística de sucesso: se veio QUALQUER um dos 3, já consideramos OK
            success = bool(qr_b64 or copia_cola or pay_link)

            # Loga chaves para diagnóstico (sem expor valores)
            try:
                if isinstance(raw, dict):
                    logger.info(f"[PIX] keys={list(raw.keys())}")
                    if isinstance(mp_tx, dict):
                        logger.info(f"[PIX] mp_tx.keys={list(mp_tx.keys())}")
            except Exception:
                pass

            if success:
                # Monta texto
                text = [
                    "💳 **Pagamento da Assinatura (PIX)**",
                    f"💰 Valor: **R$ {amount:.2f}**",
                    "",
                    "🧾 Pague usando **uma** das opções abaixo:"
                ]
                if pay_link:
                    text.append(f"🔗 Link de pagamento:\n{pay_link}")
                if copia_cola:
                    text.extend(["", "📋 **Copia e Cola PIX:**", f"`{copia_cola}`"])

                keyboard = [[InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]]

                # Envia QR se base64 presente
                if qr_b64:
                    try:
                        import base64, io
                        if isinstance(qr_b64, str) and qr_b64.startswith("data:image"):
                            qr_b64 = qr_b64.split(",")[1]
                        qr_bytes = base64.b64decode(qr_b64) if isinstance(qr_b64, str) else qr_b64
                        qr_photo = io.BytesIO(qr_bytes)
                        qr_photo.name = "pix_qr_code.png"
                        await context.bot.send_photo(
                            chat_id=query.message.chat_id,
                            photo=qr_photo,
                            caption="📲 **QR Code PIX**\nEscaneie para pagar.",
                            parse_mode="Markdown"
                        )
                    except Exception as e:
                        logger.error(f"Erro ao enviar QR: {e}")

                await query.edit_message_text("\n".join(text), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
                return

            # ---- Fallback (nada veio)
            fallback_text = (
    "⚠️ **Pagamento PIX indisponível no momento.**\n\n"
    "Verifique se o método `create_subscription_payment` está sendo chamado e se o token do Mercado Pago está definido.\n"
    "Toque em **Menu Principal** e tente novamente mais tarde."
)
            await query.edit_message_text(
                fallback_text,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]]),
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"subscribe_now_callback error: {e}")
        await update.callback_query.edit_message_text("❌ Erro ao iniciar pagamento. Tente novamente.")

async def check_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """(Opcional) Verificar status do pagamento se suportado."""
    if not update.callback_query:
        return
    query = update.callback_query
    await query.answer()

    payment_id = None
    try:
        data = query.data or ""
        if data.startswith("check_payment_"):
            payment_id = data.replace("check_payment_", "").strip()

        status = None
        if payment_id and hasattr(payment_service, "check_payment_status"):
            status = payment_service.check_payment_status(payment_id)

        if status and status.get("paid"):
            await query.edit_message_text(
                "✅ **Pagamento confirmado!** Sua assinatura foi ativada.\n\n"
                "Toque em **Menu Principal** para continuar.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]]),
                parse_mode="Markdown"
            )
        else:
            await query.edit_message_text(
                "⏳ Pagamento ainda **não confirmado**.\n\nTente novamente em alguns instantes.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Tentar novamente", callback_data=query.data)],
                    [InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")],
                ]),
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"check_payment_callback error: {e}")
        await query.edit_message_text("❌ Erro ao verificar pagamento.")



async def whatsapp_status_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle WhatsApp status callback and show QR code if needed"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    try:
        # Get user info
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(update.effective_user.id)).first()
            if not db_user:
                await query.edit_message_text("❌ Usuário não encontrado. Use /start para se registrar.")
                return
            
            status = whatsapp_service.check_instance_status(db_user.id)
            
            if status.get('success') and status.get('connected'):
                # Connected - show connected status with QR option always available
                verification_method = status.get('verification_method', 'individual')
                status_text = f"""✅ **WhatsApp Conectado**

🟢 Status: Conectado e funcionando
📱 Pronto para enviar mensagens automáticas
⏰ Sistema de lembretes ativo
🔍 Verificação: {verification_method}"""
                
                keyboard = [
                    [InlineKeyboardButton("📱 Novo QR Code", callback_data="whatsapp_reconnect")],
                    [InlineKeyboardButton("🔄 Atualizar", callback_data="whatsapp_status"), InlineKeyboardButton("🔌 Desconectar", callback_data="whatsapp_disconnect")],
                    [InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]
                ]
                
            elif status.get('success') and status.get('qrCode'):
                # Not connected but has QR - show QR status
                status_text = """📱 **WhatsApp - Aguardando Conexão**

🔄 Escaneie o QR Code para conectar
📲 Use o WhatsApp do seu celular"""
                
                keyboard = [
                    [InlineKeyboardButton("🔄 Novo QR", callback_data="whatsapp_status")],
                    [InlineKeyboardButton("🔌 Reconectar", callback_data="whatsapp_reconnect")],
                    [InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]
                ]
                
                # Send QR image if available
                try:
                    qr_code = status.get('qrCode')
                    if qr_code.startswith('data:image'):
                        qr_data = qr_code.split(',')[1]
                    else:
                        qr_data = qr_code
                    
                    import base64, io
                    qr_bytes = base64.b64decode(qr_data)
                    qr_photo = io.BytesIO(qr_bytes)
                    qr_photo.name = 'whatsapp_qr.png'
                    
                    await query.edit_message_text(status_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
                    await context.bot.send_photo(
                        chat_id=query.message.chat_id,
                        photo=qr_photo,
                        caption="📲 **QR Code WhatsApp**\n\nEscaneie para conectar"
                    )
                    return
                    
                except Exception as qr_error:
                    logger.error(f"Error sending QR: {qr_error}")
                    
            else:
                # Disconnected or error
                status_text = """❌ **WhatsApp Desconectado**

🔴 Status: Desconectado
📱 Escolha como conectar:"""
                
                keyboard = [
                    [InlineKeyboardButton("📱 QR Code", callback_data="whatsapp_reconnect")],

                    [InlineKeyboardButton("🔄 Atualizar", callback_data="whatsapp_status")],
                    [InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]
                ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(status_text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error in whatsapp_status_callback: {e}")
        await query.edit_message_text("❌ Erro ao verificar status do WhatsApp.")

async def whatsapp_disconnect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle WhatsApp disconnect"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    try:
        # Get user info
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(update.effective_user.id)).first()
            if not db_user:
                await query.edit_message_text("❌ Usuário não encontrado. Use /start para se registrar.")
                return
            
            result = whatsapp_service.disconnect_whatsapp(db_user.id)
            
            if result.get('success'):
                status_text = """🔌 **WhatsApp Desconectado**

✅ Desconectado com sucesso
🔴 Status: Offline"""
            else:
                status_text = f"""❌ **Erro ao Desconectar**

🔧 Erro: {result.get('error', 'Desconhecido')}"""
            
            keyboard = [
                [InlineKeyboardButton("📱 QR Code", callback_data="whatsapp_reconnect")],
                [InlineKeyboardButton("🔄 Verificar Status", callback_data="whatsapp_status")],
                [InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(status_text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error disconnecting WhatsApp: {e}")
        await query.edit_message_text("❌ Erro ao desconectar WhatsApp.")

async def whatsapp_reconnect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle WhatsApp reconnect and generate new QR code"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    logger.info("🔄 WhatsApp reconnect requested - generating new QR code")
    
    # Show reconnecting message first
    await query.edit_message_text("🔄 **Gerando Novo QR Code...**\n\n⏳ Aguarde alguns segundos...", parse_mode='Markdown')
    
    try:
        import asyncio
        
        # Get user info
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(update.effective_user.id)).first()
            if not db_user:
                await query.edit_message_text("❌ Usuário não encontrado. Use /start para se registrar.")
                return
            
            user_id = db_user.id  # Get the ID while inside the session
            
        # FORCE GENERATE NEW QR CODE - GUARANTEED TO WORK
        logger.info("🚀 FORCING NEW QR CODE GENERATION...")
        result = whatsapp_service.force_new_qr(user_id)
        logger.info(f"Force QR result: {result}")
        
        qr_code = None
        if result.get('success') and result.get('qrCode'):
            qr_code = result.get('qrCode')
            logger.info(f"✅ QR Code forcefully generated! Length: {len(qr_code)}")
        else:
            logger.error(f"❌ Force QR failed: {result.get('error', 'Unknown error')}")
            # Fallback to old method if force QR fails
            logger.info("Trying fallback reconnect method...")
            fallback_result = whatsapp_service.reconnect_whatsapp(user_id)
            if fallback_result.get('success'):
                await asyncio.sleep(5)
                status = whatsapp_service.check_instance_status(user_id)
                if status.get('qrCode'):
                    qr_code = status.get('qrCode')
                    logger.info(f"✅ Fallback QR Code found! Length: {len(qr_code)}")
        
        # Process QR code if found (either immediate or after reconnect)
        if qr_code:
            logger.info(f"✅ Processing QR Code! Length: {len(qr_code)}")
            
            try:
                # Send QR code as photo immediately
                import base64
                import io
                
                logger.info("Converting QR Code to image...")
                
                # Convert base64 QR code to bytes
                if qr_code.startswith('data:image'):
                    qr_data = qr_code.split(',')[1]
                    logger.info("✅ Removed data URL prefix")
                else:
                    qr_data = qr_code
                    
                qr_bytes = base64.b64decode(qr_data)
                qr_photo = io.BytesIO(qr_bytes)
                qr_photo.name = 'whatsapp_qr_fresh.png'
                
                logger.info(f"✅ QR code image prepared: {len(qr_bytes)} bytes")
                
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=qr_photo,
                    caption="""📲 **QR Code WhatsApp Atualizado**

✅ QR Code gerado com sucesso!
📱 Escaneie este código com seu WhatsApp para conectar.

**Instruções:**
1. Abra WhatsApp no celular
2. Toque nos 3 pontos (⋮)
3. Toque em "Dispositivos conectados"
4. Toque em "Conectar um dispositivo"
5. Escaneie este QR Code""",
                    parse_mode='Markdown'
                )
                
                logger.info("🎉 QR code sent successfully!")
                
                # Update message to success
                success_text = """✅ **QR Code Gerado!**

📲 O QR Code foi enviado como imagem acima.
📱 Escaneie com seu WhatsApp para conectar."""
                
                success_keyboard = [
                    [InlineKeyboardButton("🔄 Gerar Novo QR", callback_data="whatsapp_reconnect")],
                    [InlineKeyboardButton("🔄 Verificar Status", callback_data="whatsapp_status")],
                    [InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]
                ]
                
                success_markup = InlineKeyboardMarkup(success_keyboard)
                await query.edit_message_text(success_text, reply_markup=success_markup, parse_mode='Markdown')
                
            except Exception as qr_error:
                logger.error(f"❌ Error sending QR code: {qr_error}")
                await query.edit_message_text(
                    f"❌ **Erro ao enviar QR Code**\n\nErro: {str(qr_error)}",
                    parse_mode='Markdown'
                )
        else:
            logger.warning("❌ No QR code available")
            error_text = """❌ **QR Code não disponível**

O servidor WhatsApp pode estar reiniciando.
Tente novamente em alguns segundos."""
            
            error_keyboard = [
                [InlineKeyboardButton("🔄 Tentar Novamente", callback_data="whatsapp_reconnect")],
                [InlineKeyboardButton("🔄 Verificar Status", callback_data="whatsapp_status")],
                [InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]
            ]
            
            error_markup = InlineKeyboardMarkup(error_keyboard)
            await query.edit_message_text(error_text, reply_markup=error_markup, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"❌ Error in whatsapp_reconnect_callback: {e}")
        await query.edit_message_text("❌ Erro ao reconectar WhatsApp.")

# Pairing code functionality completely removed - caused WhatsApp connection conflicts

# All pairing code functionality has been completely removed

# Pairing code cancellation function also removed

async def schedule_settings_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show schedule settings menu"""
    if not update.effective_user:
        return
        
    user = update.effective_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await update.message.reply_text("❌ Conta inativa.")
                return
            
            # Get current schedule settings
            from models import UserScheduleSettings
            schedule_settings = session.query(UserScheduleSettings).filter_by(
                user_id=db_user.id
            ).first()
            
            if not schedule_settings:
                # Create default settings
                schedule_settings = UserScheduleSettings(
                    user_id=db_user.id,
                    morning_reminder_time='09:00',
                    daily_report_time='08:00'
                )
                session.add(schedule_settings)
                session.commit()
            
            text = f"""⏰ **Configurações de Horários**

📅 **Horários Atuais:**
• 🌅 Lembretes matinais: **{schedule_settings.morning_reminder_time}**
• 📊 Relatório diário: **{schedule_settings.daily_report_time}**

⚙️ **O que você deseja fazer?**"""
            
            keyboard = [
                [InlineKeyboardButton("🌅 Alterar Horário Matinal", callback_data="set_morning_time")],
                [InlineKeyboardButton("📊 Alterar Horário Relatório", callback_data="set_report_time")],
                [InlineKeyboardButton("📋 Ver Fila de Envios", callback_data="view_sending_queue")],
                [InlineKeyboardButton("❌ Cancelar Envio Específico", callback_data="cancel_specific_sending")],
                [InlineKeyboardButton("🔄 Resetar para Padrão", callback_data="reset_schedule")],
                [InlineKeyboardButton("🏠 Menu Principal", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error showing schedule settings: {e}")
        await update.message.reply_text("❌ Erro ao carregar configurações de horários.")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle help command"""
    help_text = """
❓ **Ajuda - Bot WhatsApp**

🤖 **Como usar:**
• Digite /start para começar
• Use os botões do menu para navegar
• Cadastre clientes e configure lembretes

📋 **Comandos disponíveis:**
• /start - Iniciar ou voltar ao menu
• /help - Mostrar esta ajuda

✨ **Funcionalidades:**
• 👥 Gestão de clientes
• 📅 Controle de vencimentos
• 📱 Lembretes automáticos via WhatsApp
• 💰 Sistema de pagamentos PIX

🎁 **Teste grátis:** 7 dias
💎 **Plano Premium:** R$ 20,00/mês

📞 **Suporte:** @seunick_suporte
"""
    
    help_text += "\n\n📲 Use o teclado abaixo para navegar"
    
    reply_markup = get_main_keyboard()
    
    if update.message:
        await update.message.reply_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.message.reply_text(help_text, reply_markup=reply_markup, parse_mode='Markdown')

async def send_welcome_message_with_session(session, client, user_id):
    """Send welcome message to new client using existing session"""
    try:
        template = session.query(MessageTemplate).filter_by(
            template_type='welcome',
            is_active=True
        ).first()
        
        if template:
            from templates.message_templates import format_welcome_message
            message_content = format_welcome_message(
                template.content,
                client_name=client.name,
                plan_name=client.plan_name,
                plan_price=client.plan_price,
                due_date=client.due_date.strftime('%d/%m/%Y')
            )
            
            # Send via WhatsApp
            result = whatsapp_service.send_message(client.phone_number, message_content, user_id)
            
            if result.get('success'):
                logger.info(f"Welcome message sent to {client.name}")
            else:
                logger.error(f"Failed to send welcome message to {client.name}: {result.get('error')}")
    
    except Exception as e:
        logger.error(f"Error sending welcome message: {e}")

async def send_welcome_message(client, user_id):
    """Send welcome message to new client"""
    try:
        with db_service.get_session() as session:
            await send_welcome_message_with_session(session, client, user_id)
    
    except Exception as e:
        logger.error(f"Error sending welcome message: {e}")

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle main menu callback"""
    if not update.callback_query:
        return
    query = update.callback_query
    await query.answer()
    await show_main_menu(update, context)

async def unknown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle unknown callback queries"""
    if not update.callback_query:
        return
    query = update.callback_query
    await query.answer("❌ Comando não reconhecido.")

# Keyboard button handlers
async def handle_keyboard_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle persistent keyboard button presses"""
    if not update.message or not update.message.text:
        return
        
    text = update.message.text.strip()
    
    # Check if user is creating a template (new step-by-step system)
    creating_step = context.user_data.get('creating_template_step')
    
    if creating_step:
        await process_template_creation(update, context, text)
        return
    
    # Check if user is editing a template
    if context.user_data.get('editing_template'):
        await process_template_edit(update, context, text)
        return
    
    # Check if user is searching for a client
    if context.user_data.get('searching_client'):
        # Clear the search state first to avoid loops
        del context.user_data['searching_client']
        await process_client_search(update, context, text)
        return
    
    
    # Debug all button presses
    logger.info(f"handle_keyboard_buttons: Received text '{text}' from user {update.effective_user.id if update.effective_user else 'None'}")
    
    # Main menu buttons
    if text == "👥 Clientes":
        await manage_clients_message(update, context)
    elif text == "📊 Dashboard":
        await dashboard_message(update, context)
    elif text == "📱 WhatsApp":
        await whatsapp_status_message(update, context)
    elif text == "💳 Assinatura":
        await subscription_info_message(update, context)
    elif text == "📋 Ver Templates":
        await templates_list_message(update, context)
    elif text == "⏰ Horários":
        await schedule_settings_message(update, context)
    elif text == "➕ Adicionar Cliente":
        await add_client_message(update, context)
    elif text == "❓ Ajuda":
        await help_command(update, context)
    elif text == "🏠 Menu Principal":
        await show_main_menu(update, context)
    elif text == "📋 Ver Clientes":
        await manage_clients_message(update, context)
    elif text == "🚀 PAGAMENTO ANTECIPADO":
        logger.info(f"🚀 PAGAMENTO ANTECIPADO button pressed by user {update.effective_user.id}")
        await early_payment_message(update, context)
    elif text == "🚀 Forçar Hoje":
        await force_process_reminders_today(update, context)
    else:
        # Log unknown button presses
        logger.warning(f"handle_keyboard_buttons: Unknown button pressed: '{text}' by user {update.effective_user.id if update.effective_user else 'None'}")

async def early_payment_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle early payment for trial users - Direct to payment"""
    logger.info(f"early_payment_message called by user {update.effective_user.id if update.effective_user else 'None'}")
    
    if not update.effective_user:
        logger.error("early_payment_message: No effective_user found")
        return
        
    user = update.effective_user
    logger.info(f"Processing early payment for user {user.id} ({user.first_name})")
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user:
                logger.error(f"early_payment_message: User {user.id} not found in database")
                await update.message.reply_text("❌ Usuário não encontrado.")
                return
                
            logger.info(f"early_payment_message: User {user.id} found, is_trial={db_user.is_trial}, is_active={db_user.is_active}")
                
            if not db_user.is_trial:
                logger.warning(f"early_payment_message: User {user.id} is not in trial mode")
                await update.message.reply_text("❌ Esta opção está disponível apenas para usuários em teste.")
                return
            
            # Calculate trial days left
            trial_end = db_user.created_at.date() + timedelta(days=7)
            trial_days_left = max(0, (trial_end - datetime.utcnow().date()).days)
            
            message = f"""
🚀 **PAGAMENTO ANTECIPADO**

🎁 Você ainda tem **{trial_days_left} dias** de teste restantes!

✅ **Vantagens de pagar agora:**
• Garante continuidade sem interrupções
• Evita perder acesso às funcionalidades
• Seus dados ficam sempre salvos

💰 **Valor:** R$ 20,00/mês via PIX
📅 **Duração:** 30 dias a partir do pagamento

Deseja continuar com o pagamento antecipado?
"""
            
            keyboard = [
                [InlineKeyboardButton("💳 SIM, PAGAR AGORA!", callback_data="subscribe_now")],
                [InlineKeyboardButton("🔙 Voltar", callback_data="main_menu")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            logger.info(f"early_payment_message: Sending early payment message to user {user.id}")
            await update.message.reply_text(
                message,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            logger.info(f"early_payment_message: Early payment message sent successfully to user {user.id}")
            
    except Exception as e:
        logger.error(f"Error showing early payment: {e}")
        await update.message.reply_text("❌ Erro ao carregar opções de pagamento.")

async def manage_clients_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle manage clients from keyboard - Show client list with inline buttons"""
    if not update.effective_user:
        return
        
    user = update.effective_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user:
                await update.message.reply_text("❌ Usuário não encontrado.")
                return
            
            if not db_user.is_active:
                await update.message.reply_text("⚠️ Conta inativa. Assine o plano para continuar.")
                return
            
            # Get clients ordered by due date (descending - most urgent first)
            clients = session.query(Client).filter_by(user_id=db_user.id).order_by(Client.due_date.desc()).all()
            
            if not clients:
                text = """
👥 **Lista de Clientes**

📋 Nenhum cliente cadastrado ainda.

Comece adicionando seu primeiro cliente!
"""
                keyboard = [
                    [InlineKeyboardButton("➕ Adicionar Cliente", callback_data="add_client")],
                    [InlineKeyboardButton("🔍 Buscar Cliente", callback_data="search_client")],
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
                return
            
            # Create client list with inline buttons
            from datetime import date
            today = date.today()
            
            text = f"👥 **Lista de Clientes** ({len(clients)} total)\n\n📋 Selecione um cliente para gerenciar:"
            
            keyboard = []
            for client in clients:
                # Status indicator
                if client.status == 'active':
                    if client.due_date < today:
                        status = "🔴"  # Overdue
                    elif (client.due_date - today).days <= 7:
                        status = "🟡"  # Due soon
                    else:
                        status = "🟢"  # Active
                else:
                    status = "⚫"  # Inactive
                
                # Format button text
                due_str = client.due_date.strftime('%d/%m')
                button_text = f"{status} {client.name} - {due_str}"
                
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"client_{client.id}")])
            
            # Add navigation buttons
            keyboard.extend([
                [InlineKeyboardButton("➕ Adicionar Cliente", callback_data="add_client")],
                [InlineKeyboardButton("🔍 Buscar Cliente", callback_data="search_client")],
                [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error managing clients: {e}")
        await update.message.reply_text("❌ Erro ao carregar clientes.")

async def client_details_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show client details and submenu"""
    if not update.callback_query or not update.callback_query.from_user:
        return
        
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    try:
        # Extract client ID from callback data
        client_id = int(query.data.split('_')[1])
        
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await query.edit_message_text("❌ Conta inativa. Assine o plano para continuar.")
                return
            
            # Get client details
            client = session.query(Client).filter_by(id=client_id, user_id=db_user.id).first()
            
            if not client:
                await query.edit_message_text("❌ Cliente não encontrado.")
                return
            
            # Format client details
            from datetime import date
            today = date.today()
            
            # Status indicator and text
            if client.status == 'active':
                if client.due_date < today:
                    status_icon = "🔴"
                    status_text = "Em atraso"
                elif (client.due_date - today).days <= 7:
                    status_icon = "🟡"
                    status_text = "Vence em breve"
                else:
                    status_icon = "🟢"
                    status_text = "Ativo"
            else:
                status_icon = "⚫"
                status_text = "Inativo"
            
            # Build client info text
            other_info_display = f"\n📝 {client.other_info}" if client.other_info else ""
            
            # Auto reminders status
            auto_reminders_status = getattr(client, 'auto_reminders_enabled', True)
            reminders_emoji = "✅" if auto_reminders_status else "❌"
            reminders_text = "Ativados" if auto_reminders_status else "Desativados"
            
            text = f"""
{status_icon} **{client.name}**

📱 {client.phone_number}
📦 {client.plan_name}
🖥️ {client.server or 'Não definido'}
💰 R$ {client.plan_price:.2f}
📅 Vence: {client.due_date.strftime('%d/%m/%Y')}
📊 Status: {status_text}
🤖 Lembretes: {reminders_emoji} {reminders_text}{other_info_display}

🔧 **Escolha uma ação:**
"""
            
            # Create submenu buttons
            # Dynamic button for auto reminders toggle
            reminders_button_text = "❌ Desativar Lembretes" if auto_reminders_status else "✅ Ativar Lembretes"
            reminders_callback = f"toggle_reminders_{client.id}"
            
            keyboard = [
                [
                    InlineKeyboardButton("✏️ Editar", callback_data=f"edit_{client.id}"),
                    InlineKeyboardButton("🔄 Renovar", callback_data=f"renew_{client.id}")
                ],
                [
                    InlineKeyboardButton("💬 Mensagem", callback_data=f"message_{client.id}"),
                    InlineKeyboardButton("🗑️ Excluir", callback_data=f"delete_{client.id}")
                ],
                [
                    InlineKeyboardButton(reminders_button_text, callback_data=reminders_callback)
                ],
                [
                    InlineKeyboardButton("📦 Arquivar", callback_data=f"archive_{client.id}"),
                    InlineKeyboardButton("🔙 Voltar", callback_data="back_to_clients")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error showing client details: {e}")
        await query.edit_message_text("❌ Erro ao carregar detalhes do cliente.")

async def back_to_clients_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Go back to client list"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    # Simulate the original manage_clients_message but for callback
    user = query.from_user
    
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await query.edit_message_text("❌ Conta inativa.")
                return
            
            # Get clients ordered by due date (descending)
            clients = session.query(Client).filter_by(user_id=db_user.id).order_by(Client.due_date.desc()).all()
            
            if not clients:
                text = """
👥 **Lista de Clientes**

📋 Nenhum cliente cadastrado ainda.

Comece adicionando seu primeiro cliente!
"""
                keyboard = [
                    [InlineKeyboardButton("➕ Adicionar Cliente", callback_data="add_client")],
                    [InlineKeyboardButton("🔍 Buscar Cliente", callback_data="search_client")],
                    [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
                return
            
            # Create client list with inline buttons
            from datetime import date
            today = date.today()
            
            text = f"👥 **Lista de Clientes** ({len(clients)} total)\n\n📋 Selecione um cliente para gerenciar:"
            
            keyboard = []
            for client in clients:
                # Status indicator
                if client.status == 'active':
                    if client.due_date < today:
                        status = "🔴"  # Overdue
                    elif (client.due_date - today).days <= 7:
                        status = "🟡"  # Due soon
                    else:
                        status = "🟢"  # Active
                else:
                    status = "⚫"  # Inactive
                
                # Format button text
                due_str = client.due_date.strftime('%d/%m')
                button_text = f"{status} {client.name} - {due_str}"
                
                keyboard.append([InlineKeyboardButton(button_text, callback_data=f"client_{client.id}")])
            
            # Add navigation buttons
            keyboard.extend([
                [InlineKeyboardButton("➕ Adicionar Cliente", callback_data="add_client")],
                [InlineKeyboardButton("🔍 Buscar Cliente", callback_data="search_client")],
                [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error returning to client list: {e}")
        await query.edit_message_text("❌ Erro ao carregar lista de clientes.")

async def delete_client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client deletion"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    try:
        # Extract client ID from callback data
        client_id = int(query.data.split('_')[1])
        
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await query.edit_message_text("❌ Conta inativa.")
                return
            
            # Get client
            client = session.query(Client).filter_by(id=client_id, user_id=db_user.id).first()
            
            if not client:
                await query.edit_message_text("❌ Cliente não encontrado.")
                return
            
            # Delete client
            session.delete(client)
            session.commit()
            
            await query.edit_message_text(f"✅ Cliente **{client.name}** foi excluído com sucesso.", parse_mode='Markdown')
            
            # Auto return to client list after 2 seconds
            import asyncio
            await asyncio.sleep(2)
            await back_to_clients_callback(update, context)
            
    except Exception as e:
        logger.error(f"Error deleting client: {e}")
        await query.edit_message_text("❌ Erro ao excluir cliente.")

async def archive_client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client archiving"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    try:
        # Extract client ID from callback data
        client_id = int(query.data.split('_')[1])
        
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await query.edit_message_text("❌ Conta inativa.")
                return
            
            # Get client
            client = session.query(Client).filter_by(id=client_id, user_id=db_user.id).first()
            
            if not client:
                await query.edit_message_text("❌ Cliente não encontrado.")
                return
            
            # Archive client (change status to inactive)
            old_status = client.status
            client.status = 'inactive' if client.status == 'active' else 'active'
            session.commit()
            
            action = "arquivado" if client.status == 'inactive' else "reativado"
            await query.edit_message_text(f"✅ Cliente **{client.name}** foi {action} com sucesso.", parse_mode='Markdown')
            
            # Auto return to client list after 2 seconds
            import asyncio
            await asyncio.sleep(2)
            await back_to_clients_callback(update, context)
            
    except Exception as e:
        logger.error(f"Error archiving client: {e}")
        await query.edit_message_text("❌ Erro ao arquivar cliente.")

async def edit_client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle client editing - show edit options menu"""
    if not update.callback_query:
        return
        
    query = update.callback_query
    await query.answer()
    
    user = query.from_user
    
    try:
        # Extract client ID from callback data
        client_id = int(query.data.split('_')[1])
        
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            
            if not db_user or not db_user.is_active:
                await query.edit_message_text("❌ Conta inativa.")
                return
            
            # Get client details
            client = session.query(Client).filter_by(id=client_id, user_id=db_user.id).first()
            
            if not client:
                await query.edit_message_text("❌ Cliente não encontrado.")
                return
            
            # Store client ID in context for editing
            context.user_data['edit_client_id'] = client_id
            
            text = f"""
✏️ **Editar Cliente: {client.name}**

📋 Escolha o que deseja editar:
"""
            
            # Create edit options menu
            keyboard = [
                [InlineKeyboardButton("👤 Nome", callback_data=f"edit_field_name_{client_id}")],
                [InlineKeyboardButton("📱 Telefone", callback_data=f"edit_field_phone_{client_id}")],
                [InlineKeyboardButton("📦 Plano", callback_data=f"edit_field_package_{client_id}")],
                [InlineKeyboardButton("💰 Valor", callback_data=f"edit_field_price_{client_id}")],
                [InlineKeyboardButton("🖥️ Servidor", callback_data=f"edit_field_server_{client_id}")],
                [InlineKeyboardButton("📅 Vencimento", callback_data=f"edit_field_due_date_{client_id}")],
                [InlineKeyboardButton("📝 Informações Extras", callback_data=f"edit_field_other_info_{client_id}")],
                [InlineKeyboardButton("🔙 Voltar", callback_data=f"client_{client_id}")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error showing edit menu: {e}")
        await query.edit_message_text("❌ Erro ao carregar menu de edição.")

# Template management functions
