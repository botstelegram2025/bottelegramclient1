
import logging
import json
import requests
import re as _re

logger = logging.getLogger(__name__)

# --- Imports expected from your project ---
try:
    from services.payment_service import payment_service  # expects the fixed version (with metadata/external_reference)
except Exception as e:
    logger.error(f"[WEBHOOK] Falha ao importar payment_service: {e}")
    payment_service = None

try:
    from services.database_service import db_service
    from models import User  # ajuste se o seu User estiver em outro módulo
except Exception as e:
    logger.error(f"[WEBHOOK] Falha ao importar DB/User: {e}")
    db_service = None
    User = None

from flask import Flask, Blueprint, request, jsonify
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ===== Helpers compartilhados =====
def _extract_tg_id_from_payment(payment: dict) -> str:
    if not isinstance(payment, dict):
        return ""
    ext = payment.get("external_reference") or payment.get("external_reference_id") or ""
    m = _re.search(r"telegram_bot_(\d+)_", str(ext))
    if m:
        return m.group(1)
    md = payment.get("metadata") or {}
    for k in ("telegram_id", "telegram_user_id", "tg_id"):
        v = md.get(k)
        if v:
            return str(v)
    desc = payment.get("description") or ""
    m2 = _re.search(r"telegram[_\s-]?id[:\s-]?(\d+)", str(desc), flags=_re.I)
    if m2:
        return m2.group(1)
    return ""

def _activate_user_subscription(session, db_user, payment_id: str):
    from datetime import datetime, timedelta
    expires = datetime.now() + timedelta(days=30)
    if hasattr(db_user, "is_trial"):
        db_user.is_trial = False
    if hasattr(db_user, "is_active"):
        db_user.is_active = True
    for field in ["subscription_expires_at", "subscription_until", "premium_until", "paid_until", "expires_at"]:
        if hasattr(db_user, field):
            setattr(db_user, field, expires)
    if hasattr(db_user, "last_payment_id"):
        db_user.last_payment_id = str(payment_id)
    session.commit()
    return expires

def _notify_user_paid_http(tg_id: int, expires):
    try:
        from config import Config as _Cfg
        token = getattr(_Cfg, "TELEGRAM_BOT_TOKEN", None) or getattr(_Cfg, "BOT_TOKEN", None)
        if not token:
            logger.error("[WEBHOOK] TELEGRAM_BOT_TOKEN ausente; notificação não enviada.")
            return
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = {
            "chat_id": int(tg_id),
            "text": "✅ Pagamento confirmado automaticamente!\n" + f"📅 Assinatura válida até: {expires.strftime('%d/%m/%Y')}",
            "parse_mode": "HTML",
            "reply_markup": '{"inline_keyboard":[[{"text":"🏠 Menu Principal","callback_data":"main_menu"}]]}',
        }
        r = requests.post(url, json=data, timeout=10)
        logger.info(f"[WEBHOOK] telegram notify status={r.status_code}")
    except Exception as e:
        logger.error(f"[WEBHOOK] erro ao notificar por HTTP: {e}")

# ===== Blueprint Webhook Mercado Pago =====
mp_webhook_bp = Blueprint("mp_webhook", __name__)

def _handle_mp_webhook_request():
    """Lógica compartilhada do webhook (usada por rotas abaixo)."""
    try:
        # Extrai payment_id de JSON e/ou querystring
        payment_id = None
        payload = request.get_json(silent=True) or {}

        if isinstance(payload, dict):
            if payload.get("type") in {"payment", "payments"} and isinstance(payload.get("data"), dict):
                payment_id = payload["data"].get("id") or payment_id
            res = payload.get("resource")
            if not payment_id and isinstance(res, str) and "/payments/" in res:
                m = _re.search(r"/payments/(\d+)", res)
                if m:
                    payment_id = m.group(1)

        if not payment_id:
            payment_id = request.args.get("id") or request.args.get("data.id")

        if not payment_id and request.form:
            payment_id = request.form.get("id") or request.form.get("data.id")

        if not payment_id:
            logger.error(f"[WEBHOOK] sem payment_id - payload={payload} args={dict(request.args)} form={dict(request.form)}")
            return {"ok": False, "error": "missing_payment_id"}, 200

        logger.info(f"[WEBHOOK] recebido payment_id={payment_id}")

        if not payment_service:
            return {"ok": False, "error": "payment_service_unavailable"}, 200

        status = payment_service.check_payment_status(str(payment_id))
        if not status.get("success"):
            logger.error(f"[WEBHOOK] status lookup falhou: {status}")
            return {"ok": False, "error": "status_lookup_failed"}, 200

        paid = bool(status.get("paid")) or (status.get("status") == "approved")
        payment_raw = status.get("raw") or {}
        tg_id = status.get("telegram_id") or _extract_tg_id_from_payment(payment_raw)

        if paid and tg_id and db_service and User:
            try:
                with db_service.get_session() as session:
                    db_user = session.query(User).filter_by(telegram_id=str(tg_id)).first()
                    if not db_user:
                        logger.error(f"[WEBHOOK] usuário não encontrado para telegram_id={tg_id}")
                        return {"ok": True, "warn": "user_not_found"}, 200
                    expires = _activate_user_subscription(session, db_user, str(payment_id))

                _notify_user_paid_http(int(tg_id), expires)
                return {"ok": True, "status": "approved"}, 200

            except Exception as e:
                logger.error(f"[WEBHOOK] erro ao ativar assinatura: {e}")
                return {"ok": False, "error": "db_error"}, 200

        return {"ok": True, "status": status.get("status")}, 200

    except Exception as e:
        logger.error(f"[WEBHOOK] erro inesperado: {e}")
        return {"ok": False, "error": "unexpected"}, 200

@mp_webhook_bp.route("/mercadopago", methods=["POST", "GET", "HEAD"])
def mercadopago_webhook_bp():
    if request.method == "GET" or request.method == "HEAD":
        return "OK", 200
    body, code = _handle_mp_webhook_request()
    return jsonify(body), code

# ===== Injeção no app já existente =====
def register_mp_webhook(app: Flask):
    """Registra blueprint em /webhook e também adiciona rota direta /webhook/mercadopago no próprio app."""
    # Monta o blueprint em /webhook
    app.register_blueprint(mp_webhook_bp, url_prefix="/webhook")
    # Também adiciona a rota direta (sem depender do blueprint)
    app.add_url_rule("/webhook/mercadopago", view_func=mercadopago_webhook_bp, methods=["POST", "GET", "HEAD"])
    app.add_url_rule("/webhook/mercadopago/", view_func=mercadopago_webhook_bp, methods=["POST", "GET", "HEAD"])
    logger.info("[WEBHOOK] Mercado Pago webhook registrado nas rotas: /webhook/mercadopago e /webhook/mercadopago/")

# ===== Auto-detecção e registro =====
def _auto_attach_to_existing_app():
    """Procura um objeto Flask já existente e registra o webhook nele.
       Isso evita 404 mesmo que a app principal já esteja rodando com outras rotas (ex.: WhatsApp /qr, /send, /status)."""
    try:
        # Procura qualquer Flask app em globals()
        candidates = []
        for name, obj in globals().items():
            try:
                if isinstance(obj, Flask):
                    candidates.append(obj)
            except Exception:
                pass

        if candidates:
            app = candidates[0]
            # Evita duplicar rotas
            existing = [str(r) for r in app.url_map.iter_rules()]
            if "/webhook/mercadopago" not in "".join(existing):
                register_mp_webhook(app)
                logger.info(f"[WEBHOOK] Anexado ao app existente via globals() -> {app.import_name}")
            return True
        else:
            logger.warning("[WEBHOOK] Nenhum Flask app existente encontrado em globals().")

    except Exception as e:
        logger.error(f"[WEBHOOK] auto attach falhou: {e}")
    return False

# Executa a injeção imediatamente ao importar este arquivo
_auto_attach_to_existing_app()

# Caso o projeto importe este arquivo antes de criar o app, exponha utilitário para registrar depois:
# No bootstrap principal, após criar o 'app', chame: register_mp_webhook(app)
