
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

# ---------------- Logging ----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ---------------- Utils ----------------
def normalize_brazilian_phone(phone_number: str) -> str:
    """
    Normalize Brazilian phone numbers for Baileys compatibility.
    Removes 9th digit from mobile numbers to match old format.
    """
    if not phone_number:
        return ''
    clean_phone = ''.join(filter(str.isdigit, phone_number))
    if clean_phone.startswith('55'):
        clean_phone = clean_phone[2:]
    if len(clean_phone) == 11:  # DDD + 9 + 8
        ddd = clean_phone[:2]
        remaining = clean_phone[3:]
        clean_phone = ddd + remaining
    elif len(clean_phone) == 10:  # ok
        pass
    elif len(clean_phone) == 9:   # missing DDD, default 11
        clean_phone = '11' + clean_phone[1:]
    elif len(clean_phone) == 8:   # missing DDD and 9
        clean_phone = '11' + clean_phone
    if len(clean_phone) != 10:
        return ''.join(filter(str.isdigit, phone_number))
    return clean_phone

# ---------------- External modules ----------------
from config import Config
from services.database_service import db_service
from services.scheduler_service import scheduler_service
from services.whatsapp_service import whatsapp_service
from services.payment_service import payment_service
from models import User, Client, Subscription, MessageTemplate, MessageLog

# ---------------- Conversation States ----------------
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

# ---------------- Keyboards ----------------
def get_main_keyboard(db_user=None):
    keyboard = [
        [KeyboardButton("👥 Clientes"), KeyboardButton("📊 Dashboard")],
        [KeyboardButton("📋 Ver Templates"), KeyboardButton("⏰ Horários")],
        [KeyboardButton("💳 Assinatura"), KeyboardButton("🚀 Forçar Hoje")],
        [KeyboardButton("📱 WhatsApp"), KeyboardButton("❓ Ajuda")]
    ]
    if db_user and db_user.is_trial and db_user.is_active:
        keyboard.insert(-1, [KeyboardButton("🚀 PAGAMENTO ANTECIPADO")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_client_keyboard():
    keyboard = [
        [KeyboardButton("➕ Adicionar Cliente"), KeyboardButton("📋 Ver Clientes")],
        [KeyboardButton("📊 Dashboard"), KeyboardButton("🏠 Menu Principal")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_price_selection_keyboard():
    keyboard = [
        [KeyboardButton("💰 R$ 25"), KeyboardButton("💰 R$ 30"), KeyboardButton("💰 R$ 35")],
        [KeyboardButton("💰 R$ 40"), KeyboardButton("💰 R$ 45"), KeyboardButton("💰 R$ 50")],
        [KeyboardButton("💰 R$ 60"), KeyboardButton("💰 R$ 70"), KeyboardButton("💰 R$ 90")],
        [KeyboardButton("💸 Outro valor")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_server_keyboard():
    keyboard = [
        [KeyboardButton("🖥️ FAST TV"), KeyboardButton("🖥️ EITV"), KeyboardButton("🖥️ ZTECH")],
        [KeyboardButton("🖥️ UNITV"), KeyboardButton("🖥️ GENIAL"), KeyboardButton("🖥️ SLIM PLAY")],
        [KeyboardButton("🖥️ LIVE 21"), KeyboardButton("🖥️ X SERVER")],
        [KeyboardButton("📦 OUTRO SERVIDOR")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_name_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Cancelar")]], resize_keyboard=True, one_time_keyboard=False)

def get_add_client_phone_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Cancelar")]], resize_keyboard=True, one_time_keyboard=False)

def get_add_client_package_keyboard():
    keyboard = [
        [KeyboardButton("📅 MENSAL"), KeyboardButton("📅 TRIMESTRAL")],
        [KeyboardButton("📅 SEMESTRAL"), KeyboardButton("📅 ANUAL")],
        [KeyboardButton("📦 Outros pacotes")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_add_client_plan_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Cancelar")]], resize_keyboard=True, one_time_keyboard=False)

def get_add_client_custom_price_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Cancelar")]], resize_keyboard=True, one_time_keyboard=False)

def get_add_client_due_date_keyboard():
    return ReplyKeyboardMarkup([[KeyboardButton("🔙 Cancelar")]], resize_keyboard=True, one_time_keyboard=False)

def get_add_client_other_info_keyboard():
    keyboard = [
        [KeyboardButton("Pular")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_due_date_keyboard(months):
    from datetime import datetime, timedelta
    today = datetime.now()
    if months == 1:
        date1, date2 = today + timedelta(days=30), today + timedelta(days=31)
        label1, label2 = f"📅 {date1.strftime('%d/%m/%Y')} (30 dias)", f"📅 {date2.strftime('%d/%m/%Y')} (31 dias)"
    elif months == 3:
        date1, date2 = today + timedelta(days=90), today + timedelta(days=91)
        label1, label2 = f"📅 {date1.strftime('%d/%m/%Y')} (3 meses)", f"📅 {date2.strftime('%d/%m/%Y')} (3 meses +1)"
    elif months == 6:
        date1, date2 = today + timedelta(days=180), today + timedelta(days=181)
        label1, label2 = f"📅 {date1.strftime('%d/%m/%Y')} (6 meses)", f"📅 {date2.strftime('%d/%m/%Y')} (6 meses +1)"
    elif months == 12:
        date1, date2 = today + timedelta(days=365), today + timedelta(days=366)
        label1, label2 = f"📅 {date1.strftime('%d/%m/%Y')} (1 ano)", f"📅 {date2.strftime('%d/%m/%Y')} (1 ano +1)"
    else:
        date1, date2 = today + timedelta(days=30), today + timedelta(days=31)
        label1, label2 = f"📅 {date1.strftime('%d/%m/%Y')} (30 dias)", f"📅 {date2.strftime('%d/%m/%Y')} (31 dias)"
    keyboard = [
        [KeyboardButton(label1)],
        [KeyboardButton(label2)],
        [KeyboardButton("📝 Outra data")],
        [KeyboardButton("🔙 Cancelar")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# ---------------- Handlers ----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user:
        return
    user = update.effective_user
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            if db_user:
                if db_user.is_active:
                    await show_main_menu(update, context)
                else:
                    await show_reactivation_screen(update, context)
            else:
                state = await start_registration(update, context)
                return state
    except Exception as e:
        logger.error(f"Error in start command: {e}")
        if update.message:
            await update.message.reply_text("❌ Erro interno. Tente novamente.")

async def show_reactivation_screen(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode='Markdown')

async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
Digite seu número com DDD (ex: 11999999999) **ou toque em Anexar ➜ Contato** e compartilhe seu contato:
"""
    if update.message:
        # Oferece botão de compartilhamento de contato no teclado opcional
        share_contact_kb = ReplyKeyboardMarkup(
            [[KeyboardButton("📲 Compartilhar meu contato", request_contact=True)]],
            resize_keyboard=True, one_time_keyboard=True
        )
        await update.message.reply_text(welcome_message, reply_markup=share_contact_kb, parse_mode='Markdown')
    return WAITING_FOR_PHONE

async def create_default_templates_in_db(user_id: int) -> bool:
    """Create a minimal set of default templates if none exist (safe-guard)."""
    try:
        with db_service.get_session() as session:
            exists = session.query(MessageTemplate).filter_by(user_id=user_id).first()
            if exists:
                return True
            defaults = [
                MessageTemplate(user_id=user_id, name="Boas-vindas", template_type="welcome", is_active=True,
                                content="Olá {client_name}! Seu plano {plan_name} foi ativado por R$ {plan_price:.2f}. Vence em {due_date}. Qualquer dúvida, chame!"),
            ]
            for t in defaults:
                session.add(t)
            session.commit()
            return True
    except Exception as e:
        logger.error(f"create_default_templates_in_db error: {e}")
        return False

# --- NEW: accept Telegram contact in registration ---
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

    # Prepare locals to avoid accessing ORM instance after session closes
    trial_end_dt = datetime.utcnow() + timedelta(days=7)
    trial_end_str = trial_end_dt.strftime('%d/%m/%Y às %H:%M')
    new_user_id = None

    try:
        logger.info(f"Starting user registration for telegram_id: {user.id}")
        # OPEN SESSION
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
                trial_start_date=datetime.utcnow(),
                trial_end_date=trial_end_dt,
                is_trial=True,
                is_active=True
            )
            session.add(new_user)
            session.flush()  # assign PK
            new_user_id = new_user.id
            session.commit()

        # Create defaults in a fresh session to avoid stale bindings
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

        # Call menu AFTER session is closed to avoid nested session issues
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


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            trial_days_left = 0
            if db_user.is_trial:
                trial_end = db_user.created_at.date() + timedelta(days=7)
                trial_days_left = max(0, (trial_end - datetime.utcnow().date()).days)
            status_text = "🎁 Teste" if db_user.is_trial else "💎 Premium"
            if db_user.is_trial:
                status_text += f" ({trial_days_left} dias restantes)"
            menu_text = f"""
🏠 **Menu Principal**

👋 Olá, {user.first_name}!

📊 **Status:** {status_text}
{'⚠️ Conta inativa' if not db_user.is_active else '✅ Conta ativa'}

O que deseja fazer?
"""
            reply_markup = get_main_keyboard(db_user)
            if update.message:
                await update.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')
            elif update.callback_query:
                await update.callback_query.message.reply_text(menu_text, reply_markup=reply_markup, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")
        if update.message:
            await update.message.reply_text("❌ Erro ao carregar menu.")
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text("❌ Erro ao carregar menu.")

async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data.clear()
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
            if update.effective_user:
                await show_main_menu_message(update.callback_query.message, context)
        logger.info(f"Conversation cancelled by user {update.effective_user.id if update.effective_user else 'Unknown'}")
    except Exception as e:
        logger.error(f"Error cancelling conversation: {e}")
    return ConversationHandler.END

async def show_main_menu_message(message, context):
    try:
        keyboard = get_main_keyboard()
        await message.reply_text(
            "🏠 **Menu Principal**\n\nEscolha uma opção:",
            reply_markup=keyboard,
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Error showing main menu: {e}")


# ---------------- Main Menu Text Router ----------------
async def send_simple_notice(update: Update, text: str, keyboard=None):
    try:
        if update.message:
            await update.message.reply_text(text, reply_markup=keyboard or get_main_keyboard(), parse_mode='Markdown')
        elif update.callback_query:
            await update.callback_query.message.reply_text(text, reply_markup=keyboard or get_main_keyboard(), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"send_simple_notice error: {e}")

async def show_clients_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Adicionar Cliente", callback_data="add_client")],
        [InlineKeyboardButton("📋 Ver Clientes (lista)", callback_data="manage_clients")],
        [InlineKeyboardButton("🔙 Menu Principal", callback_data="main_menu")]
    ])
    await send_simple_notice(update, "👥 *Clientes*\n\nEscolha uma opção:", kb)

async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_simple_notice(update, "📊 *Dashboard*\n\n(Em breve: gráficos de vencimentos, status de mensagens e receita mensal.)")

async def show_templates_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_simple_notice(update, "📋 *Templates*\n\n(Em breve: gerenciar modelos, criar/editar e aplicar a clientes.)")

async def show_schedule_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_simple_notice(update, "⏰ *Horários*\n\n(Em breve: configurar hora de verificação e envio diário.)")

async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_simple_notice(update, "💳 *Assinatura*\n\nPlano atual: *Teste (7 dias)*.\n(Em breve: link PIX para ativar mensalidade.)")

async def force_today_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Placeholder: se houver um método real no scheduler_service, chame aqui.
    try:
        # if hasattr(scheduler_service, "run_now"):
        #     scheduler_service.run_now()
        await send_simple_notice(update, "🚀 *Forçar Hoje*\n\nFila de envios marcada para execução imediata (modo demonstração).")
    except Exception as e:
        logger.error(f"force_today_jobs error: {e}")
        await send_simple_notice(update, "❌ Não foi possível disparar agora. Verifique os logs.")

async def show_whatsapp_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "📱 *WhatsApp*\n\n"
        "• Gere QR: `/qr/{seuUserId}`\n"
        "• Status: `/status/{seuUserId}`\n"
        "• Enviar: `POST /send/{seuUserId}`\n\n"
        "_(Em breve: painel dentro do Telegram.)_"
    )
    if update.message:
        await update.message.reply_text(txt, reply_markup=get_main_keyboard(), parse_mode='Markdown')
    else:
        await send_simple_notice(update, txt)

async def handle_main_menu_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()

    if text in ("🏠 Menu Principal", "🔙 Menu Principal"):
        await show_main_menu(update, context)
        return

    if text.startswith("👥"):
        await show_clients_menu(update, context)
        return

    if text.startswith("📊"):
        await show_dashboard(update, context)
        return

    if text.startswith("📋"):
        await show_templates_menu(update, context)
        return

    if text.startswith("⏰"):
        await show_schedule_menu(update, context)
        return

    if text.startswith("💳"):
        await show_subscription(update, context)
        return

    if text.startswith("🚀"):
        await force_today_jobs(update, context)
        return

    if text.startswith("📱"):
        await show_whatsapp_menu(update, context)
        return

    if text.startswith("❓"):
        await help_command(update, context)
        return

    # Default fallback on main menu
    await send_simple_notice(update, "🤔 Não entendi. Escolha uma opção do *Menu Principal*.")


# --------- Client creation flow (kept) ---------
async def add_client_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await query.message.reply_text(
        "📝 **Digite o nome do cliente:**",
        reply_markup=get_add_client_name_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_NAME

async def handle_client_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    client_name = (update.message.text or "").strip()
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
    context.user_data['client_name'] = client_name
    await update.message.reply_text(
        f"✅ Nome: **{client_name}**\n\n📱 Agora digite o número de telefone (com DDD):\n**Exemplo:** 11999999999",
        reply_markup=get_add_client_phone_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_PHONE

async def handle_client_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    phone_number = (update.message.text or "").strip()
    if phone_number in ["🔙 Cancelar", "🏠 Menu Principal", "Cancelar", "cancelar", "CANCELAR"]:
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    normalized_phone = normalize_brazilian_phone(phone_number)
    if len(normalized_phone) < 10 or len(normalized_phone) > 11:
        await update.message.reply_text(
            "❌ Número inválido. Digite apenas números com DDD.\n**Exemplo:** 11999999999",
            reply_markup=get_add_client_phone_keyboard(),
            parse_mode='Markdown'
        )
        return WAITING_CLIENT_PHONE
    context.user_data['client_phone'] = normalized_phone
    await update.message.reply_text(
        f"✅ Telefone: **{normalized_phone}**\n\n📦 Agora escolha o pacote:",
        reply_markup=get_add_client_package_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_PACKAGE

async def handle_client_package(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    package_text = (update.message.text or "").strip()
    if package_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    package_options = {
        "📅 MENSAL": ("Plano Mensal", 1),
        "📅 TRIMESTRAL": ("Plano Trimestral", 3),
        "📅 SEMESTRAL": ("Plano Semestral", 6),
        "📅 ANUAL": ("Plano Anual", 12),
        "📦 Outros pacotes": ("Outro", 0)
    }
    if package_text in package_options:
        plan_name, months = package_options[package_text]
        context.user_data['client_package'] = package_text
        context.user_data['client_plan'] = plan_name
        context.user_data['client_months'] = months
        if package_text == "📦 Outros pacotes":
            await update.message.reply_text(
                f"✅ Pacote: **{package_text}**\n\n📦 Digite o nome do plano personalizado:\n**Exemplo:** Plano Básico",
                reply_markup=get_add_client_plan_keyboard(),
                parse_mode='Markdown'
            )
            return WAITING_CLIENT_PLAN
        else:
            await update.message.reply_text(
                f"✅ Pacote: **{plan_name}**\n\n💰 Escolha o valor:",
                reply_markup=get_price_selection_keyboard(),
                parse_mode='Markdown'
            )
            return WAITING_CLIENT_PRICE_SELECTION
    else:
        await update.message.reply_text(
            "❌ Opção inválida. Escolha uma das opções do teclado:",
            reply_markup=get_add_client_package_keyboard()
        )
        return WAITING_CLIENT_PACKAGE

async def handle_client_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    plan_name = (update.message.text or "").strip()
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
    context.user_data['client_plan'] = plan_name
    await update.message.reply_text(
        f"✅ Plano: **{plan_name}**\n\n💰 Escolha o valor:",
        reply_markup=get_price_selection_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_PRICE_SELECTION

async def handle_client_price_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    price_text = (update.message.text or "").strip()
    if price_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
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
            await update.message.reply_text(
                f"✅ Opção: **{price_text}**\n\n💰 Digite o valor personalizado:\n**Exemplo:** 75.00",
                reply_markup=get_add_client_custom_price_keyboard(),
                parse_mode='Markdown'
            )
            return WAITING_CLIENT_PRICE
        else:
            price = price_options[price_text]
            context.user_data['client_price'] = price
            await update.message.reply_text(
                f"✅ Valor: **R$ {price:.2f}**\n\n🖥️ Agora escolha o servidor:",
                reply_markup=get_server_keyboard(),
                parse_mode='Markdown'
            )
            return WAITING_CLIENT_SERVER
    else:
        await update.message.reply_text(
            "❌ Opção inválida. Escolha uma das opções do teclado:",
            reply_markup=get_price_selection_keyboard()
        )
        return WAITING_CLIENT_PRICE_SELECTION

async def handle_client_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    price_text = (update.message.text or "").strip().replace(',', '.')
    if price_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    import re
    clean_price_text = re.sub(r'[^\d,.]', '', price_text).replace(',', '.')
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
    context.user_data['client_price'] = price
    await update.message.reply_text(
        f"✅ Valor: **R$ {price:.2f}**\n\n🖥️ Agora escolha o servidor:",
        reply_markup=get_server_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_SERVER

async def handle_client_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    text = (update.message.text or "").strip()
    if text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    if text.startswith("🖥️"):
        server = text.replace("🖥️ ", "")
    elif "OUTRO SERVIDOR" in text:
        await update.message.reply_text(
            "📦 Digite o nome do servidor:",
            reply_markup=get_add_client_plan_keyboard()
        )
        return WAITING_CLIENT_SERVER
    else:
        server = text
    context.user_data['client_server'] = server
    months = context.user_data.get('client_months', 1)
    await update.message.reply_text(
        f"✅ Servidor: **{server}**\n\n📅 Escolha a data de vencimento:",
        reply_markup=get_due_date_keyboard(months),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_DUE_DATE_SELECTION

async def handle_client_due_date_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    date_text = (update.message.text or "").strip()
    if date_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    if date_text == "📝 Outra data":
        await update.message.reply_text(
            f"✅ Opção: **{date_text}**\n\n📅 Digite a data de vencimento (DD/MM/AAAA):\n**Exemplo:** 25/12/2024",
            reply_markup=get_add_client_due_date_keyboard(),
            parse_mode='Markdown'
        )
        return WAITING_CLIENT_DUE_DATE
    elif date_text.startswith("📅"):
        import re
        from datetime import datetime as dt
        date_match = re.search(r'(\d{2}/\d{2}/\d{4})', date_text)
        if date_match:
            try:
                date_str = date_match.group(1)
                due_date = dt.strptime(date_str, '%d/%m/%Y').date()
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
    await update.message.reply_text(
        "❌ Opção inválida. Escolha uma das opções do teclado:",
        reply_markup=get_due_date_keyboard(context.user_data.get('client_months', 1))
    )
    return WAITING_CLIENT_DUE_DATE_SELECTION

async def handle_client_due_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    date_text = (update.message.text or "").strip()
    if date_text == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    try:
        due_date = datetime.strptime(date_text, '%d/%m/%Y').date()
    except ValueError:
        await update.message.reply_text(
            "❌ Data inválida. Use o formato DD/MM/AAAA.\n**Exemplo:** 25/12/2024",
            reply_markup=get_add_client_due_date_keyboard()
        )
        return WAITING_CLIENT_DUE_DATE
    context.user_data['client_due_date'] = due_date
    await update.message.reply_text(
        f"✅ Data: **{due_date.strftime('%d/%m/%Y')}**\n\n📝 Digite outras informações (MAC, OTP, chaves, etc.):",
        reply_markup=get_add_client_other_info_keyboard(),
        parse_mode='Markdown'
    )
    return WAITING_CLIENT_OTHER_INFO

async def send_welcome_message_with_session(session, client, user_id):
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
            result = whatsapp_service.send_message(client.phone_number, message_content, user_id)
            if result.get('success'):
                logger.info(f"Welcome message sent to {client.name}")
            else:
                logger.error(f"Failed to send welcome message to {client.name}: {result.get('error')}")
    except Exception as e:
        logger.error(f"Error sending welcome message: {e}")

async def save_client_to_database(update: Update, context: ContextTypes.DEFAULT_TYPE, due_date):
    user = update.effective_user
    try:
        with db_service.get_session() as session:
            db_user = session.query(User).filter_by(telegram_id=str(user.id)).first()
            if not db_user or not db_user.is_active:
                await update.message.reply_text("❌ Conta inativa. Assine o plano para continuar.")
                return ConversationHandler.END
            client_name = context.user_data.get('client_name', '')
            client_phone = context.user_data.get('client_phone', '')
            client_plan = context.user_data.get('client_plan', '')
            client_price = context.user_data.get('client_price', 0)
            client_server = context.user_data.get('client_server', '')
            client_other_info = context.user_data.get('client_other_info', '')
            if not client_name or not client_phone or not client_plan or not client_price or not client_server:
                await update.message.reply_text("❌ Dados incompletos. Tente novamente.")
                return ConversationHandler.END
            client = Client(
                user_id=db_user.id,
                name=client_name,
                phone_number=client_phone,
                plan_name=client_plan,
                plan_price=client_price,
                reminder_status='pending',
                server=client_server,
                other_info=client_other_info,
                due_date=due_date,
                status='active'
            )
            session.add(client)
            session.commit()
            session.refresh(client)
            await send_welcome_message_with_session(session, client, db_user.id)
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
            context.user_data.clear()
    except Exception as e:
        logger.error(f"Error saving client: {e}")
        await update.message.reply_text("❌ Erro ao cadastrar cliente. Tente novamente.")
        return ConversationHandler.END

async def handle_client_other_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
    other_info = (update.message.text or "").strip()
    if other_info == "🔙 Cancelar":
        await update.message.reply_text("❌ Operação cancelada.")
        await show_main_menu(update, context)
        return ConversationHandler.END
    if other_info.lower() in ['pular', 'skip', ''] or other_info == "Pular":
        other_info = ""
    context.user_data['client_other_info'] = other_info
    due_date = context.user_data.get('client_due_date')
    await save_client_to_database(update, context, due_date)
    return ConversationHandler.END

# ---------------- Simple callbacks ----------------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# ---------------- Error handler ----------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Exception while handling an update:", exc_info=context.error)

# ---------------- Application bootstrap ----------------
def build_application() -> Application:
    token = getattr(Config, 'TELEGRAM_BOT_TOKEN', os.getenv('TELEGRAM_BOT_TOKEN'))
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set")
    application = Application.builder().token(token).build()

    # Conversation for registration + client flow
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start_command)],
        states={
            # ✅ FIX: include both TEXT and CONTACT for phone collection
            WAITING_FOR_PHONE: [
                MessageHandler(filters.CONTACT, handle_phone_contact),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone_number),
            ],
            WAITING_CLIENT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_name)
            ],
            WAITING_CLIENT_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_phone)
            ],
            WAITING_CLIENT_PACKAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_package)
            ],
            WAITING_CLIENT_PLAN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_plan)
            ],
            WAITING_CLIENT_PRICE_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_price_selection)
            ],
            WAITING_CLIENT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_price)
            ],
            WAITING_CLIENT_SERVER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_server)
            ],
            WAITING_CLIENT_DUE_DATE_SELECTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_due_date_selection)
            ],
            WAITING_CLIENT_DUE_DATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_due_date)
            ],
            WAITING_CLIENT_OTHER_INFO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_client_other_info)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation), CommandHandler("start", start_command)],
        allow_reentry=True,
    )

    # Basic commands
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))
    # Router para botões/textos do Menu Principal
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_main_menu_choice))

    # Inline callbacks basics
    application.add_handler(CallbackQueryHandler(add_client_callback, pattern="^add_client$"))
    application.add_handler(CallbackQueryHandler(lambda u,c: send_simple_notice(u, "📋 *Lista de clientes*: (em breve)", None), pattern="^manage_clients$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: asyncio.create_task(show_main_menu(u, c)), pattern="^main_menu$"))

    # Error handler
    application.add_error_handler(error_handler)
    return application

def main():
    app = build_application()
    logger.info("✅ Telegram Bot service started")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()