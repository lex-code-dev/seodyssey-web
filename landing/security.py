import ipaddress
import socket
from urllib.parse import urlparse


def validate_public_url(raw_url: str) -> tuple[bool, str]:
    """
    Проверяет, что URL безопасен для серверной загрузки (анти-SSRF).
    Возвращает (True, "") если ок, иначе (False, "причина").
    """
    if not raw_url or len(raw_url) > 2000:
        return False, "Пустой или слишком длинный URL."

    try:
        parsed = urlparse(raw_url.strip())
    except Exception:
        return False, "Не удалось разобрать URL."

    # Только http/https
    if parsed.scheme not in ("http", "https"):
        return False, "Поддерживаются только адреса http:// и https://."

    hostname = parsed.hostname
    if not hostname:
        return False, "В адресе не указан домен."

    # Отсекаем localhost и служебные зоны
    lowered = hostname.lower()
    if lowered == "localhost" or lowered.endswith(".local") or lowered.endswith(".internal"):
        return False, "Локальные и служебные адреса проверять нельзя."

    # Резолвим хост во все IP и проверяем каждый
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError):
        return False, "Не удалось определить IP-адрес домена."

    for info in addr_info:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, "Адрес указывает на внутренний или зарезервированный IP."

    return True, ""
