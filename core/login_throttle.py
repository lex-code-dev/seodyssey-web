"""
Ограничение попыток входа.

Ни на /login/, ни на /admin/ не было никакого предела — пароль можно было
подбирать бесконечно. Считаем неудачи в Redis по двум ключам сразу:

* по IP — один адрес перебирает пароли к разным аккаунтам;
* по логину — распределённый перебор одного аккаунта с разных адресов.

Счётчик живёт в кэше, а не в базе: пишется он на каждую неудачную попытку,
и складывать это в SQLite — лишняя нагрузка на единственный writer.
"""

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Порог по логину ниже: живой человек путается в пароле несколько раз,
# но не двадцать. По IP выше — за одним адресом бывает офис или NAT.
USERNAME_LIMIT = 5
IP_LIMIT = 20
WINDOW = 15 * 60  # секунд


def _client_ip(request) -> str:
    """IP проставляет nginx: снаружи до gunicorn не достучаться."""
    if request is None:
        return "unknown"
    return (
        request.META.get("HTTP_X_REAL_IP")
        or request.META.get("REMOTE_ADDR")
        or "unknown"
    )


def _keys(request, username):
    keys = [f"login-fail:ip:{_client_ip(request)}"]
    if username:
        keys.append(f"login-fail:user:{str(username).lower()[:150]}")
    return keys


def _limit_for(key: str) -> int:
    return USERNAME_LIMIT if key.startswith("login-fail:user:") else IP_LIMIT


def is_blocked(request, username) -> bool:
    """
    True, если попытки исчерпаны.

    Кэш недоступен — пускаем. Вход важнее счётчика: иначе падение Redis
    запирает всех, включая владельца.
    """
    try:
        for key in _keys(request, username):
            if (cache.get(key) or 0) >= _limit_for(key):
                return True
    except Exception:
        logger.warning("Счётчик попыток входа недоступен — пропускаем проверку")
    return False


def register_failure(request, username) -> None:
    try:
        for key in _keys(request, username):
            # add + incr, а не get + set: между чтением и записью успевают
            # проскочить параллельные попытки, и счётчик занижается.
            if not cache.add(key, 1, WINDOW):
                cache.incr(key)
    except ValueError:
        # Ключ истёк между add и incr — попытка попадёт в следующее окно
        pass
    except Exception:
        logger.warning("Не удалось записать неудачную попытку входа")


def reset_failures(request, username) -> None:
    try:
        cache.delete_many(_keys(request, username))
    except Exception:
        pass


class ThrottledModelBackend(ModelBackend):
    """
    ModelBackend со счётчиком неудач.

    Считаем именно в backend'е, а не во вьюхе логина: через authenticate()
    проходят все точки входа сразу — и форма кабинета, и админка.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            # Django зовёт backend и по имени поля модели, а не только username
            username = kwargs.get(get_user_model().USERNAME_FIELD)

        if is_blocked(request, username):
            logger.warning(
                "Вход заблокирован по лимиту попыток: ip=%s user=%s",
                _client_ip(request), username,
            )
            return None

        user = super().authenticate(request, username=username, password=password, **kwargs)

        if user is None:
            register_failure(request, username)
        else:
            reset_failures(request, username)
        return user
