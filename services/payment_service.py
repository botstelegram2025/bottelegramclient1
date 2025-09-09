import mercadopago
import logging
import time
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger(__name__)

class PaymentService:
    def __init__(self):
        self.sdk = mercadopago.SDK(Config.MERCADO_PAGO_ACCESS_TOKEN)

    # ---------------- Public API ----------------

    def create_subscription_payment(self, user_telegram_id: str, amount: Optional[float] = None, method: str = "pix") -> Dict[str, Any]:
        """
        Cria pagamento PIX para assinatura (alias do método PIX avulso).
        """
        try:
            if amount is None:
                amount = getattr(Config, "MONTHLY_SUBSCRIPTION_PRICE", 20.0) or 20.0
            return self._create_pix_payment(user_telegram_id, float(amount))
        except Exception as e:
            logger.error(f"[MP] Error creating subscription payment: {e}")
            return {"success": False, "error": "Payment service error", "details": str(e)}

    def check_payment_status(self, payment_id: str) -> Dict[str, Any]:
        """
        Verifica status do pagamento no Mercado Pago
        """
        try:
            resp = self.sdk.payment().get(payment_id)
            payment = resp.get("response", {}) or {}
            if resp.get("status") == 200:
                paid = payment.get("status") == "approved"
                return {
                    "success": True,
                    "payment_id": payment.get("id"),
                    "status": payment.get("status"),
                    "status_detail": payment.get("status_detail"),
                    "paid": paid,
                    "amount": payment.get("transaction_amount"),
                    "date_approved": payment.get("date_approved"),
                    "raw": payment,
                }
            else:
                logger.error(f"[MP] Failed to get payment status: {resp}")
                return {"success": False, "error": "Payment status check failed", "details": resp}
        except Exception as e:
            logger.error(f"[MP] Error checking payment status: {e}")
            return {"success": False, "error": "Payment service error", "details": str(e)}

    def process_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processa notificação (webhook) do Mercado Pago
        """
        try:
            if webhook_data.get("type") == "payment":
                payment_id = webhook_data.get("data", {}).get("id")
                if payment_id:
                    status = self.check_payment_status(str(payment_id))
                    if status.get("success"):
                        return {
                            "success": True,
                            "payment_id": payment_id,
                            "status": status["status"],
                            "action_required": status.get("paid", False),
                        }
            return {"success": False, "error": "Invalid webhook data", "details": webhook_data}
        except Exception as e:
            logger.error(f"[MP] Error processing webhook: {e}")
            return {"success": False, "error": "Webhook processing error", "details": str(e)}

    # ---------------- Internal ----------------

    def _create_pix_payment(self, user_telegram_id: str, amount: float) -> Dict[str, Any]:
        """
        Cria cobrança PIX no Mercado Pago e normaliza a resposta.
        Faz um pequeno retry de 2x no GET /payments/{id} caso o QR demore para aparecer.
        """
        try:
            payment_data = {
                "transaction_amount": round(float(amount), 2),
                "description": f"Assinatura Mensal - Bot Telegram - {user_telegram_id}",
                "payment_method_id": "pix",
                "payer": {
                    "email": f"user_{user_telegram_id}@telegram.bot",
                    "identification": {"type": "CPF", "number": "00000000000"},
                },
                "notification_url": f"{getattr(Config, 'WEBHOOK_BASE_URL', '').rstrip('/')}/webhook/mercadopago" if getattr(Config, 'WEBHOOK_BASE_URL', None) else None,
                "external_reference": f"telegram_bot_{user_telegram_id}_{int(datetime.now().timestamp())}",
                "date_of_expiration": (datetime.now() + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
            }
            # remove keys None
            payment_data = {k: v for k, v in payment_data.items() if v is not None}

            resp = self.sdk.payment().create(payment_data)
            payment = resp.get("response", {}) or {}

            logger.info(f"[MP] create payment status={resp.get('status')} id={payment.get('id')} keys={list(payment.keys())}")

            # às vezes o campo transaction_data demora a aparecer; fazer 2 tentativas de GET
            if resp.get("status") == 201:
                payment_id = payment.get("id")
                norm = self._normalize_pix(payment)
                if not (norm.get("qr_code_base64") or norm.get("copy_paste") or norm.get("payment_link")) and payment_id:
                    for i in range(2):
                        time.sleep(1.0)
                        check = self.sdk.payment().get(str(payment_id))
                        payment = check.get("response", {}) or {}
                        logger.info(f"[MP] retry GET {i+1} status={check.get('status')} id={payment.get('id')}")
                        norm = self._normalize_pix(payment)
                        if norm.get("qr_code_base64") or norm.get("copy_paste") or norm.get("payment_link"):
                            break
                return norm
            else:
                logger.error(f"[MP] PIX create failed: {resp}")
                return {"success": False, "error": "Payment creation failed", "details": resp}
        except Exception as e:
            logger.error(f"[MP] Error creating PIX payment: {e}")
            return {"success": False, "error": "Payment service error", "details": str(e)}

    def _normalize_pix(self, payment: Dict[str, Any]) -> Dict[str, Any]:
        tx = ((payment or {}).get("point_of_interaction") or {}).get("transaction_data", {}) or {}
        qr_b64 = tx.get("qr_code_base64") or tx.get("qr_code_base64_image")
        copy_paste = tx.get("qr_code")
        link = tx.get("ticket_url") or tx.get("url")

        norm = {
            "success": bool(qr_b64 or copy_paste or link),
            "payment_id": payment.get("id"),
            "status": payment.get("status"),
            "qr_code_base64": qr_b64,
            "copy_paste": copy_paste,
            "qr_code": copy_paste,        # alias útil para callbacks antigos
            "payment_link": link,
            "amount": payment.get("transaction_amount"),
            "expires_at": payment.get("date_of_expiration"),
            "raw": payment,
        }
        logger.info(f"[MP] normalized: success={norm['success']} has_qr_b64={bool(qr_b64)} has_copy={bool(copy_paste)} has_link={bool(link)}")
        return norm

# Instância global
payment_service = PaymentService()
