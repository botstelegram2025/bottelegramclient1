# services/payment_service.py
"""
Payment Service - Mercado Pago (PIX)
------------------------------------
Implementação robusta e autocontida para criação de cobranças PIX no Mercado Pago,
com normalização de resposta e verificação de status.

✅ Métodos expostos:
- create_pix_payment(user_id, amount, description, **kwargs) -> dict
- create_pix_subscription(user_id, amount, description, **kwargs) -> dict  (alias p/ PIX avulso)
- create_payment(user_id, amount, description, method="pix", **kwargs) -> dict
- check_payment_status(payment_id) -> dict

🔧 Variáveis de ambiente esperadas:
- MP_ACCESS_TOKEN           (obrigatória)
- MP_INTEGRATOR_ID          (opcional, só para tracking/testes)
- MP_API_BASE               (opcional; padrão "https://api.mercadopago.com")

Observações:
- Para PIX, o endpoint é: POST /v1/payments com {"payment_method_id": "pix"}
- O Mercado Pago retorna os dados úteis dentro de point_of_interaction.transaction_data
  (qr_code, qr_code_base64, ticket_url, etc.).
- Esta implementação **normaliza** a saída para o formato esperado pelos callbacks do bot:
    {
        "success": True/False,
        "payment_id": "...",
        "qr_code_base64": "...",   # QR como base64 (quando vier)
        "copy_paste": "...",       # Código copia-e-cola (quando vier)
        "payment_link": "...",     # Link opcional de pagamento
        "raw": {...}               # Payload bruto retornado pelo MP (p/ debug)
    }
- Inclui logging defensivo, com mascaramento do token.
"""

from __future__ import annotations

import os
import time
import logging
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return token[:4] + "*" * (len(token) - 8) + token[-4:]


class MercadoPagoClient:
    def __init__(self, access_token: Optional[str] = None, base_url: Optional[str] = None, integrator_id: Optional[str] = None):
        self.access_token = access_token or os.getenv("MP_ACCESS_TOKEN", "").strip()
        self.base_url = (base_url or os.getenv("MP_API_BASE", "https://api.mercadopago.com")).rstrip("/")
        self.integrator_id = integrator_id or os.getenv("MP_INTEGRATOR_ID", "").strip()

        if not self.access_token:
            logger.warning("⚠️ MP_ACCESS_TOKEN não configurado. Pagamentos PIX não funcionarão.")

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        if self.integrator_id:
            headers["x-integrator-id"] = self.integrator_id
        return headers

    def post(self, path: str, json_data: Dict[str, Any], timeout: int = 20) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            logger.info(f"MP POST {path} | base={self.base_url} | token={_mask_token(self.access_token)}")
            resp = requests.post(url, headers=self._headers(), json=json_data, timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            # Tentar extrair corpo de erro legível
            try:
                data = resp.json()
            except Exception:
                data = {"error": str(resp.text)}
            logger.error(f"HTTPError MP POST {path}: {e} | data={data}")
            return {"success": False, "error": str(e), "raw": data}
        except Exception as e:
            logger.error(f"Erro MP POST {path}: {e}")
            return {"success": False, "error": str(e)}

    def get(self, path: str, timeout: int = 15) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            logger.info(f"MP GET {path} | base={self.base_url} | token={_mask_token(self.access_token)}")
            resp = requests.get(url, headers=self._headers(), timeout=timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.HTTPError as e:
            try:
                data = resp.json()
            except Exception:
                data = {"error": str(resp.text)}
            logger.error(f"HTTPError MP GET {path}: {e} | data={data}")
            return {"success": False, "error": str(e), "raw": data}
        except Exception as e:
            logger.error(f"Erro MP GET {path}: {e}")
            return {"success": False, "error": str(e)}


def _get_nested(d: Dict[str, Any], path: str, default: Any = None) -> Any:
    cur = d
    for p in path.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur


def _normalize_pix_response(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrai e normaliza as informações de PIX do payload do Mercado Pago.
    """
    tx = _get_nested(raw, "point_of_interaction.transaction_data", {}) or {}

    qr_b64 = (
        raw.get("qr_code_base64")
        or raw.get("qrCodeBase64")
        or tx.get("qr_code_base64")
        or tx.get("qr_code_base64_image")
        or _get_nested(raw, "transaction_data.qr_code_base64")
    )

    copy_paste = (
        raw.get("copy_paste")
        or raw.get("pix_code")
        or raw.get("copia_cola")
        or raw.get("copyAndPaste")
        or tx.get("qr_code")
        or _get_nested(raw, "transaction_data.qr_code")
    )

    payment_link = (
        raw.get("payment_link")
        or raw.get("checkout_url")
        or _get_nested(raw, "point_of_interaction.transaction_data.ticket_url")
        or _get_nested(raw, "point_of_interaction.transaction_data.url")
        or raw.get("init_point")
    )

    payment_id = raw.get("id") or raw.get("payment_id") or raw.get("preference_id") or _get_nested(raw, "transaction_data.external_reference")

    success = bool(qr_b64 or copy_paste or payment_link)

    normalized = {
        "success": success,
        "payment_id": payment_id,
        "qr_code_base64": qr_b64,
        "copy_paste": copy_paste,
        "payment_link": payment_link,
        "raw": raw,
    }
    return normalized


class PaymentService:
    def __init__(self):
        self.client = MercadoPagoClient()

    # ----------------- Public API -----------------

    def create_pix_payment(self, user_id: Any, amount: float, description: str, **kwargs) -> Dict[str, Any]:
        """
        Cria uma cobrança PIX avulsa.
        Docs: https://www.mercadopago.com.br/developers/pt/reference/payments/_payments/post
        """
        if not self.client.access_token:
            return {"success": False, "error": "MP_ACCESS_TOKEN ausente"}

        external_reference = kwargs.get("external_reference") or f"user:{user_id}|ts:{int(time.time())}"

        payer = kwargs.get("payer") or {}
        # Se quiser garantir email fake padrão para testes:
        if "email" not in payer:
            payer["email"] = f"user{user_id}@example.com"

        payload = {
            "transaction_amount": round(float(amount), 2),
            "description": description or "Pagamento PIX",
            "payment_method_id": "pix",
            "payer": payer,
            "external_reference": external_reference,
        }

        # opcional: notification_url (webhook)
        notif_url = kwargs.get("notification_url") or os.getenv("MP_NOTIFICATION_URL")
        if notif_url:
            payload["notification_url"] = notif_url

        raw = self.client.post("/v1/payments", json_data=payload)
        # Se a chamada já retornou um dicionário com "success": False, preserve a mensagem
        if isinstance(raw, dict) and raw.get("success") is False and "raw" in raw:
            return raw

        norm = _normalize_pix_response(raw if isinstance(raw, dict) else {})
        # Adiciona status bruto (helpful)
        norm["status"] = raw.get("status") if isinstance(raw, dict) else None
        return norm

    def create_payment(self, user_id: Any, amount: float, description: str, method: str = "pix", **kwargs) -> Dict[str, Any]:
        """
        Wrapper genérico. Por enquanto, se method == 'pix', delega para create_pix_payment.
        (No futuro, pode-se adicionar cartão, boleto, etc.)
        """
        method = (method or "pix").lower()
        if method == "pix":
            return self.create_pix_payment(user_id=user_id, amount=amount, description=description, **kwargs)
        return {"success": False, "error": f"Método não suportado: {method}"}

    def create_pix_subscription(self, user_id: Any, amount: float, description: str, **kwargs) -> Dict[str, Any]:
        """
        Alias de PIX avulso. Mercado Pago não suporta assinatura recorrente em PIX pela API
        da mesma forma que cartão (preapproval). Portanto, criamos uma cobrança PIX avulsa.
        """
        return self.create_pix_payment(user_id=user_id, amount=amount, description=description, **kwargs)

    def check_payment_status(self, payment_id: Any) -> Dict[str, Any]:
        """
        Verifica status de um pagamento.
        Para PIX, quando confirmado, o status tende a ser 'approved'.
        Docs: https://www.mercadopago.com.br/developers/pt/reference/payments/_payments_id/get
        """
        if not self.client.access_token:
            return {"success": False, "error": "MP_ACCESS_TOKEN ausente"}

        pid = str(payment_id).strip()
        if not pid:
            return {"success": False, "error": "payment_id inválido"}

        raw = self.client.get(f"/v1/payments/{pid}")
        if isinstance(raw, dict) and raw.get("success") is False and "raw" in raw:
            return raw

        status = raw.get("status") if isinstance(raw, dict) else None
        status_detail = raw.get("status_detail") if isinstance(raw, dict) else None
        paid = (status == "approved")

        return {
            "success": True,
            "paid": paid,
            "status": status,
            "status_detail": status_detail,
            "raw": raw,
        }


# Instância única para ser importada no bot
payment_service = PaymentService()
