import mercadopago
import logging
from typing import Dict, Any
from datetime import datetime, timedelta
from config import Config

logger = logging.getLogger(__name__)

class PaymentService:
    def __init__(self):
        self.sdk = mercadopago.SDK(Config.MERCADO_PAGO_ACCESS_TOKEN)
    
    def create_subscription_payment(self, user_telegram_id: str, amount: float = None, method: str = "pix") -> Dict[str, Any]:
        """
        Cria pagamento PIX para assinatura (alias de _create_pix_payment)
        """
        try:
            if amount is None:
                amount = Config.MONTHLY_SUBSCRIPTION_PRICE
            
            return self._create_pix_payment(user_telegram_id, amount)
                
        except Exception as e:
            logger.error(f"Error creating subscription payment: {e}")
            return {
                'success': False,
                'error': 'Payment service error',
                'details': str(e)
            }
    
    def _create_pix_payment(self, user_telegram_id: str, amount: float) -> Dict[str, Any]:
        """
        Cria cobrança PIX no Mercado Pago
        """
        try:
            payment_data = {
                "transaction_amount": amount,
                "description": f"Assinatura Mensal - Bot Telegram - {user_telegram_id}",
                "payment_method_id": "pix",
                "payer": {
                    "email": f"user_{user_telegram_id}@telegram.bot",
                    "identification": {
                        "type": "CPF",
                        "number": "00000000000"
                    }
                },
                "notification_url": f"{Config.WEBHOOK_BASE_URL}/webhook/mercadopago",
                "external_reference": f"telegram_bot_{user_telegram_id}_{int(datetime.now().timestamp())}",
                "date_of_expiration": (datetime.now() + timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%S.000-03:00')
            }
            
            response = self.sdk.payment().create(payment_data)
            payment = response.get("response", {})
            
            if response.get("status") == 201:
                logger.info(f"PIX criado com sucesso para {user_telegram_id}")
                
                # Extrai dados de PIX
                tx_data = payment.get("point_of_interaction", {}).get("transaction_data", {})
                return {
                    'success': True,
                    'payment_id': payment.get("id"),
                    'status': payment.get("status"),
                    'copy_paste': tx_data.get("qr_code"),
                    'qr_code_base64': tx_data.get("qr_code_base64"),
                    'payment_link': tx_data.get("ticket_url"),
                    'amount': payment.get("transaction_amount"),
                    'expires_at': payment.get("date_of_expiration"),
                    'raw': payment
                }
            else:
                logger.error(f"Falha ao criar PIX: {response}")
                return {
                    'success': False,
                    'error': 'Payment creation failed',
                    'details': response
                }
                
        except Exception as e:
            logger.error(f"Error creating PIX payment: {e}")
            return {
                'success': False,
                'error': 'Payment service error',
                'details': str(e)
            }
    
    
    def check_payment_status(self, payment_id: str) -> Dict[str, Any]:
        """
        Verifica status do pagamento no Mercado Pago
        """
        try:
            response = self.sdk.payment().get(payment_id)
            payment = response.get("response", {})
            
            if response.get("status") == 200:
                return {
                    'success': True,
                    'payment_id': payment.get("id"),
                    'status': payment.get("status"),
                    'status_detail': payment.get("status_detail"),
                    'paid': payment.get("status") == "approved",
                    'amount': payment.get("transaction_amount"),
                    'date_approved': payment.get("date_approved"),
                    'raw': payment
                }
            else:
                logger.error(f"Falha ao consultar status do pagamento: {response}")
                return {
                    'success': False,
                    'error': 'Payment status check failed',
                    'details': response
                }
                
        except Exception as e:
            logger.error(f"Error checking payment status: {e}")
            return {
                'success': False,
                'error': 'Payment service error',
                'details': str(e)
            }
    
    def process_webhook(self, webhook_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Processa notificação (webhook) do Mercado Pago
        """
        try:
            if webhook_data.get("type") == "payment":
                payment_id = webhook_data.get("data", {}).get("id")
                
                if payment_id:
                    status = self.check_payment_status(str(payment_id))
                    if status.get('success'):
                        return {
                            'success': True,
                            'payment_id': payment_id,
                            'status': status['status'],
                            'action_required': status.get('paid', False)
                        }
            
            return {
                'success': False,
                'error': 'Invalid webhook data',
                'details': webhook_data
            }
            
        except Exception as e:
            logger.error(f"Error processing webhook: {e}")
            return {
                'success': False,
                'error': 'Webhook processing error',
                'details': str(e)
            }

# Instância global
payment_service = PaymentService()
