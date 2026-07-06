import json
import re
import httpx
from bs4 import BeautifulSoup
from urllib.robotparser import RobotFileParser

def _iter_jsonld_nodes(json_ld_tags):
    """Обходит все узлы JSON-LD, разворачивая массивы и @graph."""
    for tag in json_ld_tags:
        try:
            data = json.loads(tag.string or "")
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                graph = node.get("@graph")
                if isinstance(graph, list):
                    stack.extend(graph)
                yield node


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Согласование существительного с числом: 1 заголовок, 2 заголовка, 5 заголовков."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def check_ai_geo(domain: str) -> list[dict]:
    results = []
    # Принимаем и голый домен (из аудита), и полный URL (из GEO-чекера на лендинге)
    if domain.startswith(("http://", "https://")):
        base_url = domain
    else:
        base_url = f"https://{domain}"
    # Корень домена — для robots.txt (он всегда лежит в корне, не в пути страницы)
    from urllib.parse import urlparse
    _parsed = urlparse(base_url)
    root_url = f"{_parsed.scheme}://{_parsed.netloc}"

    try:
        response = httpx.get(base_url, follow_redirects=True, timeout=10)
        soup = BeautifulSoup(response.text, "lxml")
        text = soup.get_text(separator=" ", strip=True).lower()
        html = response.text

        # --- Доступ AI-ботов в robots.txt ---
        ai_bots = [
            "GPTBot",  # OpenAI (обучение)
            "OAI-SearchBot",  # OpenAI (поиск ChatGPT)
            "ChatGPT-User",  # OpenAI (заход по ссылке из ответа)
            "ClaudeBot",  # Anthropic
            "PerplexityBot",  # Perplexity
            "Google-Extended",  # Google Gemini / AI Overviews
            "YandexBot",  # Яндекс / Нейро
            "Bingbot",  # Microsoft Copilot
            "CCBot",  # Common Crawl (кормит многие LLM)
        ]
        try:
            robots_resp = httpx.get(f"{root_url}/robots.txt", follow_redirects=True, timeout=10)
            if robots_resp.status_code == 200 and robots_resp.text.strip():
                rp = RobotFileParser()
                rp.parse(robots_resp.text.splitlines())
                blocked = [bot for bot in ai_bots if not rp.can_fetch(bot, base_url)]
                if blocked:
                    results.append({
                        "check": "robots_ai_bots",
                        "status": "warning",
                        "message": f"В robots.txt закрыт доступ для AI-ботов: {', '.join(blocked)}. Эти системы не прочитают сайт и не процитируют его в ответах.",
                    })
                else:
                    results.append({
                        "check": "robots_ai_bots",
                        "status": "ok",
                        "message": "AI-боты (GPTBot, ClaudeBot, PerplexityBot, Google-Extended, YandexBot и др.) не заблокированы в robots.txt.",
                    })
            else:
                results.append({
                    "check": "robots_ai_bots",
                    "status": "ok",
                    "message": "robots.txt не найден или пуст — доступ для ботов открыт по умолчанию.",
                })
        except httpx.RequestError:
            results.append({
                "check": "robots_ai_bots",
                "status": "warning",
                "message": "Не удалось загрузить robots.txt для проверки доступа AI-ботов.",
            })

        # --- Schema.org JSON-LD ---
        json_ld_tags = soup.find_all("script", attrs={"type": "application/ld+json"})
        if not json_ld_tags:
            results.append({
                "check": "schema_org",
                "status": "warning",
                "message": "Schema.org разметка (JSON-LD) не найдена.",
            })
        else:
            valid = True
            found_types = []
            # проверяем валидность JSON во всех тегах
            for tag in json_ld_tags:
                try:
                    json.loads(tag.string or "")
                except (json.JSONDecodeError, AttributeError, TypeError):
                    valid = False
            # типы собираем из всех узлов (включая массивы и @graph)
            for node in _iter_jsonld_nodes(json_ld_tags):
                node_type = node.get("@type")
                if isinstance(node_type, str):
                    found_types.append(node_type)
                elif isinstance(node_type, list):
                    found_types.extend(t for t in node_type if isinstance(t, str))

            if not valid:
                results.append({
                    "check": "schema_org",
                    "status": "fail",
                    "message": "Найдена JSON-LD разметка с ошибками. Рекомендуется проверить и исправить.",
                })
            elif found_types:
                results.append({
                    "check": "schema_org",
                    "status": "ok",
                    "message": f"Schema.org разметка найдена. Типы: {', '.join(found_types)}.",
                })
            else:
                results.append({
                    "check": "schema_org",
                    "status": "warning",
                    "message": "JSON-LD найден, но тип разметки не определён.",
                })

            # Конкретные типы Schema.org
            valuable_types = {
                "Organization": "Organization",
                "LocalBusiness": "LocalBusiness",
                "WebSite": "WebSite",
                "FAQPage": "FAQPage",
                "BreadcrumbList": "BreadcrumbList",
                "Article": "Article",
                "Product": "Product",
            }
            for type_key, type_label in valuable_types.items():
                if type_key in found_types:
                    results.append({
                        "check": f"schema_{type_key.lower()}",
                        "status": "ok",
                        "message": f"Найдена разметка {type_label} — поисковые системы и AI лучше поймут сущности сайта.",
                    })

            if "FAQPage" not in found_types:
                results.append({
                    "check": "schema_faqpage",
                    "status": "warning",
                    "message": "Разметка FAQPage не найдена. FAQ помогает AI-системам понимать, в каких ситуациях рекомендовать сайт.",
                })

            if "Organization" not in found_types and "LocalBusiness" not in found_types:
                results.append({
                    "check": "schema_organization",
                    "status": "warning",
                    "message": "Разметка Organization или LocalBusiness не найдена. Без неё AI-системам сложнее идентифицировать компанию.",
                })

                # --- sameAs и @id (entity-привязка) ---
                if json_ld_tags:
                    same_as_found = any(node.get("sameAs") for node in _iter_jsonld_nodes(json_ld_tags))
                    id_found = any(node.get("@id") for node in _iter_jsonld_nodes(json_ld_tags))

                    if same_as_found:
                        results.append({
                            "check": "schema_sameas",
                            "status": "ok",
                            "message": "В разметке есть sameAs — бренд связан с внешними профилями (Wikidata, соцсети). AI-системам проще идентифицировать сущность.",
                        })
                    else:
                        results.append({
                            "check": "schema_sameas",
                            "status": "warning",
                            "message": "В Schema.org нет атрибута sameAs. Добавьте ссылки на внешние профили (Wikidata, соцсети) — это привязывает бренд к узлу графа знаний.",
                        })

                    if id_found:
                        results.append({
                            "check": "schema_id",
                            "status": "ok",
                            "message": "В разметке есть @id — у сущностей стабильные идентификаторы, AI не путает их между страницами.",
                        })
                    else:
                        results.append({
                            "check": "schema_id",
                            "status": "warning",
                            "message": "В Schema.org нет атрибута @id. Стабильные идентификаторы помогают AI однозначно связывать страницы с одной сущностью.",
                        })

        # --- Иерархия заголовков H1–H6 ---
        headings = soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
        h1_tags = soup.find_all("h1")

        if not headings:
            results.append({
                "check": "heading_hierarchy",
                "status": "warning",
                "message": "Заголовки H1–H6 не найдены. Структура текста не размечена — AI сложно нарезать страницу на смысловые блоки.",
            })
        else:
            problems = []

            # H1: должен быть ровно один
            if len(h1_tags) == 0:
                problems.append("нет H1")
            elif len(h1_tags) > 1:
                problems.append(f"несколько H1 ({len(h1_tags)} шт.)")

            # Пропуски уровней (скачок больше чем на 1 вниз)
            levels = [int(tag.name[1]) for tag in headings]
            skips = []
            for prev, cur in zip(levels, levels[1:]):
                if cur - prev > 1:
                    skips.append(f"H{prev}→H{cur}")
            if skips:
                problems.append(f"пропуск уровней ({', '.join(skips[:3])})")

            if problems:
                results.append({
                    "check": "heading_hierarchy",
                    "status": "warning",
                    "message": f"Иерархия заголовков нарушена: {'; '.join(problems)}. Последовательная вложенность H1→H6 помогает AI извлекать структуру контента.",
                })
            else:
                results.append({
                    "check": "heading_hierarchy",
                    "status": "ok",
                    "message": f"Иерархия заголовков корректна: один H1, последовательная вложенность ({len(headings)} {_plural_ru(len(headings), 'заголовок', 'заголовка', 'заголовков')}).",
                })

        # --- Прямой ответ в первом абзаце (перевёрнутая пирамида) ---
        # AI-движки нарезают страницу на чанки и теряют середину контекста
        # (эффект "Lost in the Middle"). Прямой содержательный ответ сразу после
        # первого заголовка повышает шанс попасть в цитируемый фрагмент.
        anchor = soup.find("h1") or soup.find("h2")
        if not anchor:
            results.append({
                "check": "inverted_pyramid",
                "status": "warning",
                "message": "Не найден H1/H2, от которого можно оценить «прямой ответ в первом абзаце».",
            })
        else:
            first_para_words = 0
            buried_by = None
            # Ищем первый содержательный абзац в начале статьи.
            # Заголовки (citable-блок, H2) НЕ обрывают поиск — пропускаем их,
            # но ограничиваем окно первыми блоками, чтобы не зацепить середину.
            for sib in anchor.find_all_next(limit=40):
                if sib.name == "p":
                    words = len(sib.get_text(strip=True).split())
                    if words >= 10:
                        first_para_words = words
                        break
                if sib.name in ("img", "figure", "video", "iframe", "form") and buried_by is None:
                    buried_by = sib.name

            if first_para_words >= 40:
                results.append({
                    "check": "inverted_pyramid",
                    "status": "ok",
                    "message": f"После первого заголовка идёт содержательный абзац ({first_para_words} слов). "
                               f"Прямой ответ в начале раздела повышает шанс попасть в AI-цитату.",
                })
            elif first_para_words > 0:
                results.append({
                    "check": "inverted_pyramid",
                    "status": "warning",
                    "message": f"Первый абзац после заголовка короткий ({first_para_words} слов). "
                               f"AI-движки ценят прямой развёрнутый ответ в начале раздела (принцип перевёрнутой пирамиды).",
                })
            else:
                hint = f" Перед текстом идёт <{buried_by}>." if buried_by else ""
                results.append({
                    "check": "inverted_pyramid",
                    "status": "warning",
                    "message": f"Сразу после первого заголовка нет содержательного абзаца.{hint} "
                               f"Дайте прямой ответ текстом в первом абзаце — его проще извлечь в AI-ответ.",
                })

        # --- Фактологическая плотность (эвристика) ---
        word_count = len(text.split())

        # Числа: целые/дробные, проценты, годы. Игнорируем «номерной шум» вроде дат в коде.
        numbers = re.findall(r"\b\d[\d\s.,]*\s?(?:%|процент)?\b", text)
        numbers = [n for n in numbers if any(ch.isdigit() for ch in n)]
        numbers_per_100w = (len(numbers) / word_count * 100) if word_count else 0

        # Внешние ссылки (на другие домены), исключая соцсети-«заглушки»
        external_links = 0
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and domain not in href:
                external_links += 1

        if word_count < 100:
            # Слишком мало текста, чтобы судить о плотности
            results.append({
                "check": "factual_density",
                "status": "warning",
                "message": "На странице мало текста для оценки фактологической плотности. AI-системы предпочитают контент с проверяемыми фактами.",
            })
        else:
            signals = []
            if numbers_per_100w >= 1.5:
                signals.append(f"цифры и статистика ({len(numbers)} шт.)")
            if external_links >= 2:
                signals.append(f"ссылки на внешние источники ({external_links} шт.)")

            if len(signals) == 2:
                results.append({
                    "check": "factual_density",
                    "status": "ok",
                    "message": f"Высокая фактологическая плотность: {', '.join(signals)}. AI-системы охотнее цитируют контент с проверяемыми фактами.",
                })
            elif signals:
                results.append({
                    "check": "factual_density",
                    "status": "warning",
                    "message": f"Фактология частичная (есть {', '.join(signals)}). Добавьте статистику и ссылки на первоисточники — это повышает шанс цитирования в AI-ответах.",
                })
            else:
                results.append({
                    "check": "factual_density",
                    "status": "warning",
                    "message": "Мало проверяемых фактов: нет заметной статистики и ссылок на внешние источники. AI-системы предпочитают плотную фактологию «воде».",
                })

        # --- Open Graph ---
        og_title = soup.find("meta", property="og:title")
        og_description = soup.find("meta", property="og:description")
        og_image = soup.find("meta", property="og:image")
        og_url = soup.find("meta", property="og:url")
        og_type = soup.find("meta", property="og:type")

        og_score = sum([
            bool(og_title and og_title.get("content")),
            bool(og_description and og_description.get("content")),
            bool(og_image and og_image.get("content")),
        ])

        if og_score == 3:
            results.append({
                "check": "open_graph",
                "status": "ok",
                "message": "Open Graph разметка заполнена полностью (title, description, image).",
            })
        elif og_score > 0:
            missing = []
            if not og_title or not og_title.get("content"):
                missing.append("og:title")
            if not og_description or not og_description.get("content"):
                missing.append("og:description")
            if not og_image or not og_image.get("content"):
                missing.append("og:image")
            results.append({
                "check": "open_graph",
                "status": "warning",
                "message": f"Open Graph заполнен частично. Не найдено: {', '.join(missing)}.",
            })
        else:
            results.append({
                "check": "open_graph",
                "status": "warning",
                "message": "Open Graph разметка не найдена. Ссылка может отображаться без превью в мессенджерах.",
            })

        if not og_url or not og_url.get("content"):
            results.append({
                "check": "og_url",
                "status": "warning",
                "message": "Тег og:url не найден. Рекомендуется явно указывать канонический URL страницы.",
            })

        # --- Twitter Card ---
        twitter_card = soup.find("meta", attrs={"name": "twitter:card"})
        if twitter_card:
            results.append({
                "check": "twitter_card",
                "status": "ok",
                "message": "Twitter Card найден — ссылки корректно отображаются в X и других платформах.",
            })
        else:
            results.append({
                "check": "twitter_card",
                "status": "warning",
                "message": "Twitter Card не найден. Ссылки могут отображаться без превью в X и Telegram.",
            })

        # --- Favicon ---
        favicon = (
            soup.find("link", rel="icon") or
            soup.find("link", rel="shortcut icon") or
            soup.find("link", rel=lambda r: r and "icon" in r)
        )
        if favicon:
            results.append({
                "check": "favicon",
                "status": "ok",
                "message": "Favicon найден.",
            })
        else:
            results.append({
                "check": "favicon",
                "status": "warning",
                "message": "Favicon не найден.",
            })

        # --- FAQ на странице ---
        faq_keywords = ["faq", "часто задаваемые", "вопрос", "ответ"]
        if any(kw in text for kw in faq_keywords):
            results.append({
                "check": "faq",
                "status": "ok",
                "message": "На странице найдены признаки FAQ-контента.",
            })
        else:
            results.append({
                "check": "faq",
                "status": "warning",
                "message": "FAQ не найден. Это упущенная возможность для AI-поиска и сниппетов.",
            })

        # --- Описание компании ---
        about_keywords = ["о компании", "о нас", "наша команда", "кто мы", "about"]
        if any(kw in text for kw in about_keywords):
            results.append({
                "check": "about_company",
                "status": "ok",
                "message": "На странице найдено описание компании — AI-системы смогут лучше идентифицировать бренд.",
            })
        else:
            results.append({
                "check": "about_company",
                "status": "warning",
                "message": "Описание компании не найдено. Для AI-поиска важно явно описать, кто вы и чем занимаетесь.",
            })

        # --- Упоминание услуг ---
        service_keywords = ["услуги", "сервис", "решения", "продукт", "тариф", "цена", "стоимость"]
        if any(kw in text for kw in service_keywords):
            results.append({
                "check": "services_mentioned",
                "status": "ok",
                "message": "На странице упоминаются услуги или продукты — AI-системы смогут понять специализацию.",
            })
        else:
            results.append({
                "check": "services_mentioned",
                "status": "warning",
                "message": "Услуги или продукты явно не упомянуты. Добавьте описание того, что предлагает компания.",
            })

        # --- Упоминание региона ---
        region_keywords = ["москва", "санкт-петербург", "спб", "россия", "краснодар",
                           "екатеринбург", "новосибирск", "казань", "нижний новгород",
                           "по всей россии", "по всему миру", "вся россия"]
        if any(kw in text for kw in region_keywords):
            results.append({
                "check": "region_mentioned",
                "status": "ok",
                "message": "На странице упоминается регион — это помогает AI-системам в геолокационных запросах.",
            })
        else:
            results.append({
                "check": "region_mentioned",
                "status": "warning",
                "message": "Регион работы компании не упомянут. Для локального AI-поиска это важный сигнал.",
            })

        # --- Ссылки на соцсети ---
        social_patterns = ["vk.com", "t.me", "youtube.com", "instagram.com", "ok.ru"]
        social_found = [s for s in social_patterns if s in html.lower()]
        if social_found:
            results.append({
                "check": "social_links",
                "status": "ok",
                "message": f"Найдены ссылки на соцсети: {', '.join(social_found)}.",
            })
        else:
            results.append({
                "check": "social_links",
                "status": "warning",
                "message": "Ссылки на соцсети не найдены. Присутствие в соцсетях усиливает доверие AI-систем к бренду.",
            })

        # --- Определение CMS ---
        cms = None
        if "wp-content" in html or "wp-includes" in html:
            cms = "WordPress"
        elif "bitrix" in html.lower() or "1c-bitrix" in html.lower():
            cms = "1С-Битрикс"
        elif "tilda" in html.lower() or "tildacdn" in html.lower():
            cms = "Tilda"
        elif "amo" in html.lower() and "crm" in html.lower():
            cms = "amoCRM"
        elif "next" in html.lower() and "__next" in html:
            cms = "Next.js"
        elif "react" in html.lower() and "root" in html:
            cms = "React"

        if cms:
            results.append({
                "check": "cms_detected",
                "status": "ok",
                "message": f"Определена платформа сайта: {cms}. Это помогает давать точные рекомендации.",
            })
        else:
            results.append({
                "check": "cms_detected",
                "status": "ok",
                "message": "Платформа сайта не определена — возможно, кастомная разработка.",
            })

        # --- Featured Snippets / Быстрые ответы ---
        snippet_signals = []

        # Вопросительные заголовки
        question_headings = [
            tag.get_text(strip=True)
            for tag in soup.find_all(["h2", "h3"])
            if "?" in tag.get_text()
        ]
        if question_headings:
            snippet_signals.append(f"вопросительные заголовки ({len(question_headings)} шт.)")

        # Списки с 3+ пунктами
        rich_lists = [
            ul for ul in soup.find_all(["ul", "ol"])
            if len(ul.find_all("li")) >= 3
        ]
        if rich_lists:
            snippet_signals.append(f"списки ({len(rich_lists)} шт.)")

        # Таблицы
        tables = soup.find_all("table")
        if tables:
            snippet_signals.append(f"таблицы ({len(tables)} шт.)")

        # Теги определений
        dls = soup.find_all("dl")
        if dls:
            snippet_signals.append(f"блоки определений ({len(dls)} шт.)")

        if snippet_signals:
            results.append({
                "check": "featured_snippet_signals",
                "status": "ok",
                "message": f"Найдены элементы для быстрых ответов: {', '.join(snippet_signals)}. Это повышает шансы попасть в сниппеты.",
            })
        else:
            results.append({
                "check": "featured_snippet_signals",
                "status": "warning",
                "message": "Не найдено структур для быстрых ответов (вопросы, списки, таблицы). Добавьте их, чтобы попасть в сниппеты Google и Яндекса.",
            })

    except httpx.RequestError as e:
        results.append({
            "check": "ai_geo",
            "status": "fail",
            "message": f"Не удалось проверить страницу: {str(e)}",
        })

    return results

def extract_page_facts(domain: str) -> dict:
    """
    Извлекает реальные данные со страницы для персонализации AI-рекомендаций.
    Отдельная функция (не трогает check_ai_geo) — чтобы AI подставлял настоящие
    title/телефон/регион, а не плейсхолдеры. Вызывается только для платного разбора.
    """
    facts = {}
    if domain.startswith(("http://", "https://")):
        base_url = domain
    else:
        base_url = f"https://{domain}"

    try:
        response = httpx.get(base_url, follow_redirects=True, timeout=10)
        soup = BeautifulSoup(response.text, "lxml")
        html = response.text
        text = soup.get_text(separator=" ", strip=True)

        facts["url"] = base_url

        # title / description
        if soup.title and soup.title.string:
            facts["title"] = soup.title.string.strip()
        desc = soup.find("meta", attrs={"name": "description"})
        if desc and desc.get("content"):
            facts["description"] = desc["content"].strip()

        # H1 (первый)
        h1 = soup.find("h1")
        if h1:
            facts["h1"] = h1.get_text(strip=True)

        # Первый абзац после первого заголовка (для совета по «перевёрнутой пирамиде»).
        # Тот же алгоритм, что в check_ai_geo → inverted_pyramid, но результат идёт
        # в facts, чтобы AI давал решение под конкретный раздел этой страницы.
        anchor = h1 or soup.find("h2")
        if anchor:
            facts["first_section_heading"] = anchor.get_text(strip=True)
            first_para_text = ""
            buried_by = None
            for sib in anchor.find_all_next():
                if sib.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                    break
                if sib.name == "p":
                    para = sib.get_text(strip=True)
                    if len(para.split()) >= 10:
                        first_para_text = para
                        break
                if sib.name in ("img", "figure", "video", "iframe", "form") and buried_by is None:
                    buried_by = sib.name
            facts["first_para_words"] = len(first_para_text.split())
            facts["first_para_text"] = first_para_text[:300]  # обрезаем — в промпт хватит начала
            if buried_by:
                facts["first_para_buried_by"] = buried_by

        # OG-теги (то, что уже есть на странице)
        for prop in ("og:title", "og:description", "og:image", "og:url"):
            tag = soup.find("meta", property=prop)
            if tag and tag.get("content"):
                facts[prop.replace(":", "_")] = tag["content"].strip()

        # Телефон (российский формат)
        phone_match = re.search(
            r"(?:\+7|8)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}", text
        )
        if phone_match:
            facts["phone"] = phone_match.group(0).strip()

        # Email
        email_match = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", html)
        if email_match:
            facts["email"] = email_match.group(0)

        # Регион (тот же список, что в check_ai_geo)
        region_keywords = [
            "москва", "санкт-петербург", "спб", "краснодар", "екатеринбург",
            "новосибирск", "казань", "нижний новгород",
        ]
        text_low = text.lower()
        found_regions = [r for r in region_keywords if r in text_low]
        if found_regions:
            facts["region"] = found_regions[0]

        # Соцсети
        social_patterns = ["vk.com", "t.me", "youtube.com", "instagram.com", "ok.ru"]
        socials = [s for s in social_patterns if s in html.lower()]
        if socials:
            facts["socials"] = socials

    except httpx.RequestError:
        pass

    return facts