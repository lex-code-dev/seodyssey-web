from celery import shared_task
from django.utils import timezone

from audits.checks.ai_geo import check_ai_geo
from landing.models import GeoLead


STATUS_EMOJI = {"ok": "✅", "warning": "⚠️", "fail": "❌"}


def build_geo_report(url: str, results: list[dict]) -> str:
    """Собирает читаемый отчёт для Telegram (HTML parse_mode)."""
    ok = sum(1 for r in results if r.get("status") == "ok")
    warn = sum(1 for r in results if r.get("status") == "warning")
    fail = sum(1 for r in results if r.get("status") == "fail")
    total = len(results) or 1

    percent = round(ok / total * 100)
    if percent >= 80:
        verdict = "Отлично"
    elif percent >= 55:
        verdict = "Хорошо, но есть резерв"
    else:
        verdict = "Есть над чем работать"

    lines = [
        "📊 <b>GEO-аудит страницы</b>",
        f"<code>{url}</code>",
        "",
        f"<b>Готовность к GEO: {percent}%</b> — {verdict}",
        f"✅ {ok}   ⚠️ {warn}   ❌ {fail}",
    ]

    # Критичные проблемы — с пояснениями
    fails = [r for r in results if r.get("status") == "fail"]
    if fails:
        lines.append("")
        lines.append("❌ <b>Критично</b>")
        for r in fails:
            lines.append(f"• {r.get('message', '')}")

    # Стоит улучшить — с пояснениями
    warns = [r for r in results if r.get("status") == "warning"]
    if warns:
        lines.append("")
        lines.append("⚠️ <b>Стоит улучшить</b>")
        for r in warns:
            lines.append(f"• {r.get('message', '')}")

    # Пройденное — свёрнуто в одну строку
    if ok:
        lines.append("")
        lines.append(f"✅ <b>В порядке:</b> {ok} {_plural_checks(ok)}")

    lines.append("")
    lines.append("Полный аудит с рекомендациями — на seodyssey.ru")
    return "\n".join(lines)


def _plural_checks(n: int) -> str:
    n100 = abs(n) % 100
    if 11 <= n100 <= 14:
        return "проверок"
    n10 = n100 % 10
    if n10 == 1:
        return "проверка"
    if 2 <= n10 <= 4:
        return "проверки"
    return "проверок"


def _ai_bots_to_checks(url: str) -> list[dict]:
    """Проверка robots.txt на доступ AI-ботов → результаты в формате проверок GEO."""
    from audits.ai_bot_check import check_ai_bots

    r = check_ai_bots(url)
    status = r.get("status")

    if status == "blocked_site":
        return [{
            "status": "fail",
            "message": "robots.txt закрывает сайт целиком (ответ 401/403) — "
                       "AI-боты не могут получить доступ к страницам.",
        }]

    if status in ("unknown", "error"):
        return [{
            "status": "warning",
            "message": "Не удалось проверить robots.txt на доступ AI-ботов "
                       f"({r.get('error') or 'сервер недоступен'}).",
        }]

    blocked_critical = [b for b in r["bots"] if b["critical"] and not b["allowed"]]
    blocked_minor = [b for b in r["bots"] if not b["critical"] and not b["allowed"]]

    checks = []
    if blocked_critical:
        names = ", ".join(b["label"] for b in blocked_critical)
        checks.append({
            "status": "fail",
            "message": f"В robots.txt заблокированы ключевые AI-боты: {names}. "
                       "Из-за этого сайт не попадёт в соответствующие AI-ответы.",
        })
    if blocked_minor:
        names = ", ".join(b["label"] for b in blocked_minor)
        checks.append({
            "status": "warning",
            "message": f"В robots.txt ограничены AI-боты: {names}. "
                       "Это сужает охват в AI-системах.",
        })
    if not checks:
        checks.append({
            "status": "ok",
            "message": "robots.txt не блокирует AI-ботов — доступ к контенту открыт.",
        })
    return checks

@shared_task
def run_geo_check(lead_id: int):
    try:
        lead = GeoLead.objects.get(pk=lead_id)
    except GeoLead.DoesNotExist:
        return

    # 1. Проверка (ходит по сети, поэтому в фоне)
    results = check_ai_geo(lead.url)
    results += _ai_bots_to_checks(lead.url)

    # SEO-скоринг title/description/H1 (переиспользуем готовый check_seo)
    from audits.checks.seo import check_seo

    try:
        results += check_seo(lead.url)
    except Exception:
        pass  # SEO-блок не должен ронять весь отчёт

    # 2. Сохраняем результат
    lead.result_json = results
    lead.status = GeoLead.STATUS_DONE
    lead.sent_at = timezone.now()
    lead.save(update_fields=["result_json", "status", "sent_at"])

    # Демо-сниппет платного разбора — синхронно, мы уже внутри Celery-воркера
    generate_demo_item(lead.pk)

    # 3. Доставка
    if lead.channel == GeoLead.CHANNEL_TELEGRAM:
        chat_id = (lead.tg_chat_id or "").strip()
        if chat_id:
            # Пользователь уже нажал Start — шлём отчёт сразу
            from notifications.telegram import send_telegram_message
            send_telegram_message(chat_id, build_geo_report(lead.url, results))
        # Если chat_id ещё нет — отправит webhook, когда придёт /start с токеном
    elif lead.channel == GeoLead.CHANNEL_EMAIL:
        to_email = (lead.email or "").strip()
        if to_email:
            from django.core.mail import send_mail
            ok = sum(1 for r in results if r.get("status") == "ok")
            warn = sum(1 for r in results if r.get("status") == "warning")
            fail = sum(1 for r in results if r.get("status") == "fail")
            total = len(results) or 1
            percent = round(ok / total * 100)
            report_url = f"https://seodyssey.ru/geo-check/report/{lead.access_token}/"
            body = (
                f"Готов GEO-аудит вашей страницы:\n{lead.url}\n\n"
                f"Готовность к GEO: {percent}%\n"
                f"Пройдено: {ok}   Замечания: {warn}   Критично: {fail}\n\n"
                f"Полный отчёт с рекомендациями:\n{report_url}\n\n"
                f"— SEOdyssey"
            )
            send_mail(
                subject="Ваш GEO-аудит готов",
                message=body,
                from_email=None,  # возьмётся DEFAULT_FROM_EMAIL
                recipient_list=[to_email],
                fail_silently=False,
            )


def generate_demo_item(lead_id: int):
    """
    Генерирует демо-сниппет платного разбора: AI-решение ОДНОЙ проблемы
    из бесплатной проверки (превью того, что даёт платный разбор).
    Не Celery-таска: вызывается синхронно из run_geo_check, который уже в фоне.
    При любой ошибке AI просто оставляет demo_item = None.
    """
    from ai_queries.services.llm import call_openai
    from audits.checks.ai_geo import extract_page_facts

    try:
        lead = GeoLead.objects.get(pk=lead_id)
    except GeoLead.DoesNotExist:
        return

    results = lead.result_json or []
    # Для демо берём самую показательную проблему: сначала fail, иначе warning
    problem = next((r for r in results if r.get("status") == "fail"), None)
    if problem is None:
        problem = next((r for r in results if r.get("status") == "warning"), None)
    if problem is None:
        return

    try:
        # Реальные данные страницы — чтобы AI подставлял их, а не плейсхолдеры
        facts = extract_page_facts(lead.url)
        facts_lines = []
        _labels = {
            "title": "Title", "description": "Description", "h1": "H1",
            "phone": "Телефон", "email": "Email", "region": "Регион",
            "og_title": "og:title", "og_description": "og:description",
            "og_image": "og:image", "og_url": "og:url",
        }
        for key, label in _labels.items():
            if facts.get(key):
                facts_lines.append(f"{label}: {facts[key]}")
        if facts.get("socials"):
            facts_lines.append("Соцсети: " + ", ".join(facts["socials"]))
        if facts.get("first_section_heading"):
            facts_lines.append(
                f"Заголовок первого раздела: {facts['first_section_heading']}"
            )
            if facts.get("first_para_text"):
                facts_lines.append(
                    f"Первый абзац после этого заголовка "
                    f"({facts.get('first_para_words', 0)} слов): {facts['first_para_text']}"
                )
        facts_text = "\n".join(facts_lines) if facts_lines else "данные со страницы извлечь не удалось"

        prompt = (
            f"Страница: {lead.url}\n\n"
            f"РЕАЛЬНЫЕ ДАННЫЕ СО СТРАНИЦЫ (используй их в коде вместо плейсхолдеров):\n{facts_text}\n\n"
            f"Аудит готовности к попаданию в AI-ответы нашёл проблему:\n"
            f"[{problem.get('check')}] ({problem.get('status')}) {problem.get('message')}\n\n"
            "Дай практический разбор ТОЛЬКО этой одной проблемы. Верни строго JSON-объект вида:\n"
            '{"title": "<краткий заголовок проблемы по-русски>", '
            '"explanation": "<2-3 предложения: почему это мешает попасть в AI-ответы>", '
            '"code": "<готовый код для вставки или пустая строка, если код не нужен>", '
            '"rewrite": "<готовый переписанный текст для вставки на страницу, или пустая строка>"}\n\n'
            "Требования:\n"
            "- code заполняй ТОЛЬКО реальным рабочим кодом (JSON-LD, мета-теги, HTML), "
            "готовым к вставке без правок; если код не применим — верни пустую строку;\n"
            "- в коде ОБЯЗАТЕЛЬНО подставляй реальные данные страницы из блока выше "
            "(название, телефон, email, регион, URL). Плейсхолдеры используй ТОЛЬКО "
            "для полей, которых в данных страницы нет;\n"
            "- JSON-LD оборачивай в <script type=\"application/ld+json\">...</script>;\n"
            "- rewrite заполняй, если решение — переписанный текст, а не код: возьми реальный "
            "первый абзац из данных страницы и перепиши его (прямой ответ в начале, 40-60 слов, "
            "без воды и без markdown). Иначе rewrite — пустая строка;\n"
            "- пиши по-русски, конкретно, без воды."
        )

        data = call_openai(prompt)
    except Exception:
        return

    if not isinstance(data, dict) or not data.get("title"):
        return

    lead.demo_item = {
        "check": problem.get("check", ""),
        "title": data.get("title", ""),
        "explanation": data.get("explanation", ""),
        "code": data.get("code", "") or "",
        "rewrite": data.get("rewrite", "") or "",
    }
    lead.save(update_fields=["demo_item"])


@shared_task
def generate_geo_ai_report(lead_id: int):
    """
    Генерирует платный AI-разбор по уже найденным проблемам GEO-отчёта.
    Вызывается из вебхука YuKassa после успешной оплаты.
    Кеширует результат в lead.ai_report (один вызов на отчёт).
    """
    from ai_queries.services.llm import call_openai
    from audits.checks.ai_geo import extract_page_facts

    try:
        lead = GeoLead.objects.get(pk=lead_id)
    except GeoLead.DoesNotExist:
        return

    # Защита от повторного вызова: если разбор уже готов — не тратим API
    if isinstance(lead.ai_report, dict) and lead.ai_report.get("status") == "done":
        return

    results = lead.result_json or []
    # В разбор берём только проблемы — пройденные проверки объяснять не нужно
    problems = [r for r in results if r.get("status") in ("warning", "fail")]

    if not problems:
        lead.ai_report = {"status": "done", "items": []}
        lead.save(update_fields=["ai_report"])
        return

    # Компактный список проблем для модели (check-код + статус + текст)
    problems_text = "\n".join(
        f"- [{r.get('check')}] ({r.get('status')}) {r.get('message')}"
        for r in problems
    )

    # Реальные данные страницы — чтобы AI подставлял их, а не плейсхолдеры
    facts = extract_page_facts(lead.url)
    facts_lines = []
    _labels = {
        "title": "Title", "description": "Description", "h1": "H1",
        "phone": "Телефон", "email": "Email", "region": "Регион",
        "og_title": "og:title", "og_description": "og:description",
        "og_image": "og:image", "og_url": "og:url",
    }
    for key, label in _labels.items():
        if facts.get(key):
            facts_lines.append(f"{label}: {facts[key]}")
    if facts.get("socials"):
        facts_lines.append("Соцсети: " + ", ".join(facts["socials"]))
    # Первый абзац после заголовка — для решения по проблеме inverted_pyramid.
    # Даём AI реальный заголовок и текст абзаца, чтобы он переписал ИМЕННО его.
    if facts.get("first_section_heading"):
        words = facts.get("first_para_words", 0)
        buried = facts.get("first_para_buried_by")
        facts_lines.append(f"Заголовок первого раздела: {facts['first_section_heading']}")
        if facts.get("first_para_text"):
            facts_lines.append(
                f"Первый абзац после этого заголовка ({words} слов): {facts['first_para_text']}"
            )
        elif buried:
            facts_lines.append(
                f"Сразу после заголовка нет текста — первым идёт <{buried}>, "
                f"прямой ответ отсутствует."
            )
    facts_text = "\n".join(facts_lines) if facts_lines else "данные со страницы извлечь не удалось"

    prompt = (
        f"Страница: {lead.url}\n\n"
        f"РЕАЛЬНЫЕ ДАННЫЕ СО СТРАНИЦЫ (используй их в коде вместо плейсхолдеров):\n{facts_text}\n\n"
        f"Аудит готовности к попаданию в AI-ответы (Яндекс Нейро, ChatGPT, Google AI Overviews) "
        f"нашёл следующие проблемы:\n{problems_text}\n\n"
        "Для КАЖДОЙ проблемы дай практический разбор. Верни строго JSON-объект вида:\n"
        '{"items": [{"check": "<код проблемы как в списке>", '
        '"title": "<краткий заголовок проблемы по-русски>", '
        '"explanation": "<2-3 предложения: почему это мешает попасть в AI-ответы>", '
        '"steps": ["<шаг 1>", "<шаг 2>", "..."], '
        '"code": "<готовый код для вставки или пустая строка, если код не нужен>", '
        '"rewrite": "<готовый переписанный текст для вставки на страницу, или пустая строка>"}]}\n\n'
        "Требования:\n"
        "- items должен содержать ровно по одному объекту на каждую проблему из списка;\n"
        "- поле check копируй точь-в-точь из списка (значение в квадратных скобках);\n"
        "- code заполняй ТОЛЬКО реальным рабочим кодом (JSON-LD, мета-теги, HTML), "
        "готовым к вставке без правок; если для проблемы код не применим — верни пустую строку;\n"
        "- в коде ОБЯЗАТЕЛЬНО подставляй реальные данные страницы из блока выше "
        "(название, телефон, email, регион, URL). Плейсхолдеры типа «Название отеля» или "
        "«+7 123 456-78-90» используй ТОЛЬКО для полей, которых в данных страницы нет;\n"
        "- JSON-LD оборачивай в <script type=\"application/ld+json\">...</script>;\n"
        "- пиши по-русски, конкретно, без воды."
        "- поле rewrite заполняй для проблем, где решение — это переписанный текст, "
        "а не код (например inverted_pyramid — короткий первый абзац). Возьми реальный "
        "«Первый абзац после заголовка» из данных страницы и перепиши его: прямой "
        "развёрнутый ответ в начале, 40-60 слов, сохрани смысл и факты бренда, без воды "
        "и без markdown. Это готовый текст, который пользователь вставит как есть. "
        "Для проблем, решаемых кодом, rewrite оставляй пустым;\n"
    )

    data = call_openai(prompt)

    if not data or not isinstance(data.get("items"), list):
        # API упал или вернул мусор — помечаем как error, чтобы можно было перезапустить.
        # Деньги взяты, разбор обязателен — не оставляем в подвешенном состоянии.
        lead.ai_report = {"status": "error"}
        lead.save(update_fields=["ai_report"])
        return

    # Модель иногда копирует код проблемы вместе с квадратными скобками
    # из подачи "- [code] (...)". Нормализуем, чтобы check совпадал с кодами проверок.
    items = data["items"]
    for it in items:
        if isinstance(it.get("check"), str):
            it["check"] = it["check"].strip("[] ")

    lead.ai_report = {"status": "done", "items": items}
    lead.save(update_fields=["ai_report"])
    # Письмо о готовности платного AI-разбора — если у лида есть email
    to_email = (lead.email or "").strip()
    if to_email:
        from django.core.mail import send_mail
        from django.template.loader import render_to_string
        report_url = f"https://seodyssey.ru/geo-check/report/{lead.access_token}/"
        items_count = len(items)
        text_body = (
            f"Ваш AI-разбор готов.\n\n"
            f"Мы разобрали {items_count} проблем(ы) вашей страницы с готовыми решениями:\n{lead.url}\n\n"
            f"Открыть разбор:\n{report_url}\n\n"
            f"— SEOdyssey"
        )
        html_body = render_to_string("landing/email/geo_paid_email.html", {
            "url": lead.url,
            "items_count": items_count,
            "report_url": report_url,
        })
        try:
            send_mail(
                subject="Ваш AI-разбор готов",
                message=text_body,
                from_email=None,
                recipient_list=[to_email],
                fail_silently=True,
                html_message=html_body,
            )
        except Exception:
            pass