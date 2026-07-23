"""
Проверка цели для проверок, которые ходят по чужому адресу.

И бесплатные инструменты, и аудиты кабинета ходят по адресу, который ввёл
человек, — поэтому адрес надо нормализовать и убедиться, что он ведёт наружу,
а не внутрь нашей инфраструктуры (localhost, 10.0.0.0/8, 169.254.169.254
и прочее).

Проверить один раз на входе недостаточно: публичный хост может ответить
редиректом на внутренний адрес. Поэтому ходить в сеть надо только через
safe_get() — он проверяет каждый хоп, а не только первый.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

# Сколько редиректов проходим, прежде чем считать это петлёй.
MAX_REDIRECTS = 5


class TargetError(ValueError):
    """Адрес нельзя проверять: не разобрался или ведёт во внутреннюю сеть."""


class BlockedTargetError(TargetError, httpx.RequestError):
    """
    Адрес отклонён guard'ом.

    Наследуемся сразу от двух исключений намеренно:
    * TargetError — его ловят публичные инструменты и показывают текст человеку;
    * httpx.RequestError — его уже ловят все чекеры аудита, поэтому
      заблокированный адрес превращается в обычный «сайт не ответил»,
      а не роняет Celery-задачу.
    """

    def __init__(self, message: str):
        httpx.RequestError.__init__(self, message)


def _parse(raw: str):
    raw = (raw or "").strip()
    if not raw:
        raise TargetError("Укажите адрес сайта.")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise TargetError("Не удалось разобрать адрес. Пример: example.ru")
    return parsed


def assert_public_host(host: str) -> None:
    """
    Резолвим имя и проверяем все полученные адреса: одно имя может указывать
    и на внешний, и на внутренний IP.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise BlockedTargetError(f"Домен {host} не резолвится — проверьте написание.")

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise BlockedTargetError(
                "Этот адрес ведёт во внутреннюю сеть — проверять его нельзя."
            )


def assert_public_url(url: str) -> None:
    """Схема http/https и публичный хост. Для каждого хопа редиректа."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise BlockedTargetError(
            f"Переход на «{parsed.scheme}:» не поддерживается — только http и https."
        )
    if not parsed.hostname:
        raise BlockedTargetError("В адресе не указан домен.")
    assert_public_host(parsed.hostname)


def safe_get(
    url: str,
    *,
    timeout: float = 10,
    follow_redirects: bool = True,
    headers: dict | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> httpx.Response:
    """
    httpx.get, но каждый хоп проверяется на публичность адреса.

    httpx с follow_redirects=True ходит по Location сам, и guard видит только
    первый адрес — публичный хост может увести запрос на 169.254.169.254.
    Поэтому редиректы разматываем вручную и проверяем каждый.

    Заблокированный адрес поднимает BlockedTargetError — он же
    httpx.RequestError, так что чекеры обрабатывают его как недоступный сайт.
    """
    history: list[httpx.Response] = []
    current = url

    for _ in range(max_redirects + 1):
        assert_public_url(current)
        response = httpx.get(
            current, follow_redirects=False, timeout=timeout, headers=headers
        )

        if not follow_redirects or not response.has_redirect_location:
            # history пустой при follow_redirects=False — как у httpx
            response.history = history
            return response

        history.append(response)
        # Location бывает относительным — приводим к абсолютному сами,
        # чтобы не зависеть от внутренностей httpx.
        current = urljoin(current, response.headers["location"])

    raise BlockedTargetError(
        f"Больше {max_redirects} редиректов подряд — похоже на петлю."
    )


def normalize_domain(raw: str) -> str:
    """example.ru/any/path?x=1 → example.ru (для проверок уровня сайта)."""
    parsed = _parse(raw)
    assert_public_host(parsed.hostname)
    return parsed.netloc


def normalize_url(raw: str) -> str:
    """example.ru/page → https://example.ru/page (для проверок уровня страницы)."""
    parsed = _parse(raw)
    assert_public_host(parsed.hostname)
    return urlunparse(parsed._replace(path=parsed.path or "/", fragment=""))
