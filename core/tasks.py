"""Фоновые задачи кабинета."""

import logging

from celery import shared_task
from django.core.cache import cache
from django.core.management import call_command

logger = logging.getLogger(__name__)

# Пока идёт разбор очереди, второй такой же задаче делать нечего: run_checks
# сам выгребает все CheckRun со статусом queued. Лок держим с запасом
# по времени, но снимаем в finally — падение задачи не должно блокировать
# следующий запуск на весь срок.
_QUEUE_LOCK_KEY = "core:run_checks:lock"
_QUEUE_LOCK_TTL = 30 * 60


@shared_task(name="core.run_queued_checks")
def run_queued_checks():
    """
    Разбирает очередь CheckRun.

    Раньше вьюха форкала `manage.py run_checks` через subprocess.Popen на
    каждый запрос — процессы копились без всякого предела. Теперь этим
    занимается воркер, у которого есть и concurrency, и очередь.
    """
    try:
        got_lock = cache.add(_QUEUE_LOCK_KEY, 1, _QUEUE_LOCK_TTL)
    except Exception:
        # Кэш недоступен — работаем без лока. Проверки важнее защиты
        # от параллельного запуска: худшее здесь — лишний прогон.
        logger.warning("Лок недоступен — запускаем run_checks без него")
        got_lock = None

    if got_lock is False:
        logger.info("run_checks уже выполняется — пропускаем запуск")
        return {"skipped": True}

    try:
        call_command("run_checks")
        return {"ok": True}
    finally:
        if got_lock:
            try:
                cache.delete(_QUEUE_LOCK_KEY)
            except Exception:
                pass  # протухнет сам по TTL
