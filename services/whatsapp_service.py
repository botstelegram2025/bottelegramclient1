import logging
import time
import os
from typing import Dict, Any

import requests

logger = logging.getLogger(__name__)

class WhatsAppService:
    def __init__(self):
        # Usa exatamente o que vier no env; se não vier, usa localhost:3001
        base = os.getenv("WHATSAPP_SERVICE_URL", "").strip()
        if not base:
            # em dev local
            base = "http://127.0.0.1:3001"
        # remove somente barra final (não mexe em protocolo/porta)
        self.baileys_url = base.rstrip("/")
        self.headers = {"Content-Type": "application/json"}
        logger.info(f"WhatsApp Service initialized with URL: {self.baileys_url}")

    def _url(self, path: str) -> str:
        return f"{self.baileys_url}{path}"

    def _wait_for_service(self, tries: int = 6, delay: float = 2.5) -> bool:
        for i in range(1, tries + 1):
            try:
                r = requests.get(self._url("/health"), timeout=6)
                if r.ok:
                    return True
            except Exception as e:
                logger.info(f"[WA] tentativa {i}/{tries} falhou: {e}")
            time.sleep(delay)
        return False

    def get_health_status(self) -> Dict[str, Any]:
        try:
            r = requests.get(self._url("/health"), headers=self.headers, timeout=8)
            return r.json() if r.ok else {"success": False, "error": f"HTTP Error: {r.status_code}", "details": r.text}
        except Exception as e:
            return {"success": False, "error": "Health check failed", "details": str(e)}

    def check_instance_status(self, user_id: int) -> Dict[str, Any]:
        try:
            r = requests.get(self._url(f"/status/{user_id}"), headers=self.headers, timeout=10)
            if r.ok:
                j = r.json()
                return {
                    "success": True,
                    "connected": bool(j.get("connected")),
                    "state": j.get("state", "unknown"),
                    "qrCode": j.get("qrCode"),
                    "response": j,
                }
            return {"success": False, "error": f"HTTP Error: {r.status_code}", "details": r.text}
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Baileys server not running", "details": "Start the server"}
        except Exception as e:
            return {"success": False, "error": "Status check failed", "details": str(e)}

    def force_new_qr(self, user_id: int) -> Dict[str, Any]:
        try:
            if not self._wait_for_service():
                return {"success": False, "error": "WhatsApp service unavailable"}
            r = requests.post(self._url(f"/force-qr/{user_id}"), headers=self.headers, timeout=10)
            return r.json() if r.ok else {"success": False, "error": f"HTTP Error: {r.status_code}", "details": r.text}
        except Exception as e:
            return {"success": False, "error": "Force QR failed", "details": str(e)}

    def reconnect_whatsapp(self, user_id: int) -> Dict[str, Any]:
        try:
            if not self._wait_for_service():
                return {"success": False, "error": "WhatsApp service unavailable"}
            r = requests.post(self._url(f"/reconnect/{user_id}"), headers=self.headers, timeout=10)
            return r.json() if r.ok else {"success": False, "error": f"HTTP Error: {r.status_code}", "details": r.text}
        except Exception as e:
            return {"success": False, "error": "Reconnect failed", "details": str(e)}

    def get_qr_code(self, user_id: int) -> Dict[str, Any]:
        try:
            # tenta QR dedicado
            r = requests.get(self._url(f"/qr/{user_id}"), headers=self.headers, timeout=10)
            if r.ok:
                j = r.json()
                if j.get("success") and j.get("qrCode"):
                    return {"success": True, "qrCode": j["qrCode"], "lastQrAt": j.get("lastQrAt")}
            # fallback: status
            rs = requests.get(self._url(f"/status/{user_id}"), headers=self.headers, timeout=10)
            if rs.ok:
                sj = rs.json()
                if sj.get("qrCode"):
                    return {"success": True, "qrCode": sj["qrCode"], "state": sj.get("state")}
            return {"success": False, "error": "QR Code not available"}
        except Exception as e:
            return {"success": False, "error": "QR code fetch failed", "details": str(e)}

    def send_message(self, phone_number: str, message: str, user_id: int) -> Dict[str, Any]:
        try:
            if not self._wait_for_service():
                return {"success": False, "error": "WhatsApp service unavailable"}
            clean = "".join(filter(str.isdigit, phone_number or ""))
            if not clean.startswith("55"):
                clean = "55" + clean
            r = requests.post(
                self._url(f"/send/{user_id}"),
                json={"number": clean, "message": message},
                headers=self.headers,
                timeout=20,
            )
            if r.ok:
                j = r.json()
                if j.get("success"):
                    return {"success": True, "message_id": j.get("messageId"), "response": j}
                err = (j.get("error") or "").lower()
                if "not connected" in err or "não conectado" in err:
                    self.reconnect_whatsapp(user_id)
                return {"success": False, "error": j.get("error", "Unknown error"), "details": j}
            return {"success": False, "error": f"HTTP Error: {r.status_code}", "details": r.text}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout", "details": "API request timed out"}
        except Exception as e:
            return {"success": False, "error": "Unexpected error", "details": str(e)}

    def disconnect_whatsapp(self, user_id: int) -> Dict[str, Any]:
        try:
            r = requests.post(self._url(f"/disconnect/{user_id}"), headers=self.headers, timeout=10)
            return r.json() if r.ok else {"success": False, "error": f"HTTP Error: {r.status_code}", "details": r.text}
        except Exception as e:
            return {"success": False, "error": "Disconnect failed", "details": str(e)}

# Instância global
whatsapp_service = WhatsAppService()
