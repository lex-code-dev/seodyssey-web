"""
Шифрование секретов, которые лежат в базе.

OAuth-токены Яндекса дают доступ к Метрике и Вебмастеру пользователя.
Хранились они открытым текстом рядом с кодом, в db.sqlite3 — утёк файл
или бэкап, и вместе с ним утекли доступы ко всем подключённым аккаунтам.

Ключ живёт в .env, отдельно от базы: копия базы без .env бесполезна.
"""

import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

logger = logging.getLogger(__name__)

_fernet = None


def get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = getattr(settings, "CREDENTIALS_ENCRYPTION_KEY", "")
        if not key:
            raise ImproperlyConfigured(
                "Не задан CREDENTIALS_ENCRYPTION_KEY — токены нечем шифровать. "
                "Сгенерировать: python -c "
                "\"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    return _fernet


def encrypt(value: str) -> str:
    return get_fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    return get_fernet().decrypt(value.encode()).decode()


class EncryptedTextField(models.TextField):
    """
    TextField, который шифруется по дороге в базу и расшифровывается обратно.

    Прозрачен для кода: obj.access_token остаётся обычной строкой, поэтому
    все места, где токен просто читают, менять не пришлось.

    Фильтровать по такому полю нельзя — Fernet добавляет случайный вектор,
    и одно и то же значение каждый раз шифруется по-разному. Для токенов
    это неважно: их только читают через связь с пользователем.
    """

    def get_prep_value(self, value):
        if value is None or value == "":
            return value
        return encrypt(str(value))

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        try:
            return decrypt(value)
        except InvalidToken:
            # Строка записана до перехода на шифрование. Отдаём как есть,
            # чтобы интеграции не отвалились до прогона миграции данных.
            logger.warning(
                "Незашифрованное значение в %s — выполните migrate", self.name
            )
            return value
