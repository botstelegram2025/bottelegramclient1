# utils de URL — PRESERVA esquema e porta
from urllib.parse import urlparse, urlunparse

def _normalize_base_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    # se não vier esquema, assume http
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    p = urlparse(url)
    # remonta, preservando hostname, porta e esquema
    normalized = urlunparse((p.scheme, p.netloc, "", "", "", ""))
    return normalized.rstrip("/")
