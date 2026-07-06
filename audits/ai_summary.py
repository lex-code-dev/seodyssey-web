import os
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

BLOCK_NAMES = {
    "technical": "Техническое здоровье",
    "indexability": "Индексируемость",
    "seo": "SEO-основа",
    "trust": "Доверие и аналитика",
    "ai_geo": "AI/GEO-готовность",
    "sitemap_pages": "Страницы из Sitemap",
}


def _generate_block_summary(domain: str, block_name: str, checks: list) -> str:
    lines = []
    for item in checks:
        if item["status"] in ("warning", "fail"):
            emoji = "❌" if item["status"] == "fail" else "⚠️"
            lines.append(f"{emoji} {item['message']}")

    if not lines:
        return f"В блоке «{block_name}» всё в порядке — замечаний нет."

    problems_text = "\n".join(lines)

    prompt = f"""Ты опытный SEO-консультант. Ты анализируешь сайт {domain}.

Раздел анализа: «{block_name}»
Найденные замечания:
{problems_text}

Напиши короткий вывод (2–3 предложения) для владельца сайта по этому разделу.
Требования:
- Тон спокойный и поддерживающий: есть замечания, но всё решаемо.
- Назови самую важную проблему из этого раздела и почему она важна.
- Заверши одним конкретным действием, которое стоит сделать первым.
- Без технического жаргона, без маркированных списков — только связный текст."""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Не удалось сгенерировать резюме: {str(e)}"


def generate_summary(domain: str, results: dict) -> tuple[str, dict]:
    """
    Возвращает (общее резюме, словарь резюме по блокам).
    """
    block_summaries = {}
    all_issues = []

    for block_key, checks in results.items():
        block_name = BLOCK_NAMES.get(block_key, block_key)
        block_summaries[block_key] = _generate_block_summary(domain, block_name, checks)

        for item in checks:
            if item["status"] in ("warning", "fail"):
                emoji = "❌" if item["status"] == "fail" else "⚠️"
                all_issues.append(f"{emoji} [{block_name}] {item['message']}")

    # Общее резюме
    total = len(all_issues)
    if not all_issues:
        overall = "Сайт в отличном состоянии — критических замечаний не найдено."
    else:
        problems_text = "\n".join(all_issues[:15])  # не перегружаем промпт
        prompt = f"""Ты опытный SEO-консультант. Ты провёл полный анализ сайта {domain} и нашёл {total} замечаний.

Вот основные проблемы:
{problems_text}

Напиши общее резюме (3–4 предложения) для владельца сайта.
Требования:
- Тон профессиональный, спокойный, поддерживающий. Дай понять что всё решаемо.
- Общая оценка состояния сайта.
- Топ-2 приоритета которые дадут наибольший эффект.
- Один шаг который можно сделать прямо сейчас.
- Без технического жаргона, без маркированных списков — только связный текст абзацами."""

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=350,
                temperature=0.7,
            )
            overall = response.choices[0].message.content.strip()
        except Exception as e:
            overall = f"Не удалось сгенерировать резюме: {str(e)}"

    return overall, block_summaries