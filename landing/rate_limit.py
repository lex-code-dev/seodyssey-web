"""
Ограничение частоты для публичных инструментов.

Инструменты бесплатные и без регистрации, поэтому единственный ключ — IP.
Счётчик живёт в Redis: он общий для всех воркеров gunicorn, в отличие от
локального кэша процесса.
"""

from django.core.cache import cache

# Одна проверка = один-два внешних запроса, так что лимит щадящий:
# живому человеку хватает, автоматическому перебору — нет.
TOOL_LIMIT = 10
TOOL_WINDOW = 60  # секунд

RATE_LIMIT_MESSAGE = (
    "Слишком много проверок с вашего адреса. Подождите минуту и попробуйте снова."
)

# GEO-проверка дороже инструментов: письмо, задача Celery и запросы к нейросетям
# на каждую отправку. Поэтому лимит жёстче и окно длиннее.
GEO_LIMIT = 5
GEO_WINDOW = 3600  # секунд

GEO_RATE_LIMIT_MESSAGE = (
    "С вашего адреса уже запущено несколько проверок. "
    "Следующую можно будет запустить через час — отчёты по прошлым проверкам "
    "останутся доступны по своим ссылкам."
)


def client_ip(request) -> str:
    """
    IP берём из X-Real-IP: его проставляет наш nginx, снаружи до gunicorn
    не достучаться. REMOTE_ADDR — запасной вариант для локального запуска.
    """
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR") or "unknown"


def is_rate_limited(request, scope: str, limit: int = TOOL_LIMIT,
                    window: int = TOOL_WINDOW) -> bool:
    """
    Возвращает True, если лимит исчерпан. При недоступном Redis пропускаем
    запрос: инструмент важнее лимита, а падать из-за кэша он не должен.
    """
    key = f"ratelimit:{scope}:{client_ip(request)}"
    try:
        if cache.add(key, 1, window):  # первый запрос в окне
            return False
        return cache.incr(key) > limit
    except ValueError:
        # Ключ истёк между add и incr — считаем это началом нового окна
        return False
    except Exception:
        return False
