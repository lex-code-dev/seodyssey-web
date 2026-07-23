import httpx

from audits.net_guard import safe_get
from bs4 import BeautifulSoup


GENERIC_TITLES = {"главная", "home", "index", "untitled", "добро пожаловать", "welcome", "страница"}


def _check_title_quality(title_text: str) -> list[dict]:
    issues = []
    lower = title_text.lower().strip()

    # Слишком общий
    if lower in GENERIC_TITLES:
        issues.append({
            "check": "title_quality_generic",
            "status": "warning",
            "message": f"Title слишком общий («{title_text}»). Он не описывает содержимое страницы.",
        })

    # ALL CAPS
    words = title_text.split()
    if len(words) >= 2 and all(w.isupper() for w in words if w.isalpha()):
        issues.append({
            "check": "title_quality_caps",
            "status": "warning",
            "message": "Title написан заглавными буквами. Это выглядит как спам для поисковиков.",
        })

    # Keyword stuffing — слово повторяется 3+ раз
    word_counts = {}
    for w in words:
        w_clean = w.lower().strip("«».,!?-—")
        if len(w_clean) > 3:
            word_counts[w_clean] = word_counts.get(w_clean, 0) + 1
    stuffed = [w for w, cnt in word_counts.items() if cnt >= 3]
    if stuffed:
        issues.append({
            "check": "title_quality_stuffing",
            "status": "warning",
            "message": f"В title слово «{stuffed[0]}» повторяется {word_counts[stuffed[0]]} раза. Возможен переспам.",
        })

    return issues


def _check_description_quality(desc_text: str) -> list[dict]:
    issues = []
    words = desc_text.split()

    # ALL CAPS
    if len(words) >= 3 and all(w.isupper() for w in words if w.isalpha()):
        issues.append({
            "check": "description_quality_caps",
            "status": "warning",
            "message": "Description написан заглавными буквами. Это негативно влияет на CTR.",
        })

    # Keyword stuffing — слово повторяется 3+ раз
    word_counts = {}
    for w in words:
        w_clean = w.lower().strip("«».,!?-—")
        if len(w_clean) > 3:
            word_counts[w_clean] = word_counts.get(w_clean, 0) + 1
    stuffed = [w for w, cnt in word_counts.items() if cnt >= 3]
    if stuffed:
        issues.append({
            "check": "description_quality_stuffing",
            "status": "warning",
            "message": f"В description слово «{stuffed[0]}» повторяется {word_counts[stuffed[0]]} раза. Возможен переспам.",
        })

    # Выглядит как список ключевых слов: много запятых
    comma_count = desc_text.count(",")
    if comma_count >= 5 and comma_count / max(len(words), 1) > 0.25:
        issues.append({
            "check": "description_quality_keywords_list",
            "status": "warning",
            "message": "Description похож на список ключевых слов через запятую. Лучше написать читаемый текст.",
        })

    return issues

# Служебные слова — не считаем их ключевыми
_DENSITY_STOPWORDS = {
    "этот", "эта", "это", "эти", "который", "которая", "которые",
    "также", "более", "очень", "можно", "нужно", "будет", "если",
    "чтобы", "когда", "после", "перед", "через", "между", "только",
    "весь", "вся", "все", "всё", "наш", "наша", "наши", "ваш", "ваша",
    "ваши", "свой", "своя", "свои", "для", "что", "как", "при",
}


def _check_keyword_density(body_text: str) -> list[dict]:
    """Переоптимизация текста (классическая «тошнота»).

    Считаем долю самого частого значимого слова в тексте. Аномально высокая
    доля — сигнал переспама ключами: BM25 видит это как рост tf и почти
    обнуляет прирост релевантности.
    """
    issues = []

    words = []
    for w in body_text.split():
        w_clean = w.lower().strip("«».,!?:;()\"'—–-")
        if len(w_clean) > 3 and w_clean.isalpha() and w_clean not in _DENSITY_STOPWORDS:
            words.append(w_clean)

    total = len(words)
    # На коротком тексте плотность недостоверна — пропускаем
    if total < 200:
        return issues

    counts = {}
    for w in words:
        counts[w] = counts.get(w, 0) + 1

    top_word, top_count = max(counts.items(), key=lambda kv: kv[1])
    density = top_count / total * 100

    if density >= 5.0:
        issues.append({
            "check": "content_keyword_density",
            "status": "warning",
            "message": (
                f"Возможен переспам: слово «{top_word}» — {density:.1f}% "
                f"значимого текста ({top_count} из {total} слов). "
                f"Переоптимизированный текст ранжируется хуже."
            ),
        })
    else:
        issues.append({
            "check": "content_keyword_density",
            "status": "ok",
            "message": (
                f"Плотность ключевых слов в норме (самое частое слово "
                f"«{top_word}» — {density:.1f}%)."
            ),
        })

    return issues

def evaluate_seo_html(html: str) -> tuple[list[dict], dict]:
    """
    Чистый разбор HTML: список проверок + сами значения тегов (для превью
    сниппета). Без сети — тестируется напрямую.
    """
    results = []
    meta = {"title": "", "description": "", "h1": ""}
    soup = BeautifulSoup(html, "lxml")

    # --- title ---
    title_tag = soup.find("title")
    if not title_tag or not title_tag.text.strip():
        results.append({
            "check": "title",
            "status": "fail",
            "message": "Title отсутствует или пустой.",
        })
    else:
        title_text = title_tag.text.strip()
        length = len(title_text)
        if length < 30:
            results.append({
                "check": "title",
                "status": "warning",
                "message": f"Title слишком короткий ({length} символов): «{title_text}»",
            })
        elif length > 80:
            results.append({
                "check": "title",
                "status": "warning",
                "message": f"Title слишком длинный ({length} символов): «{title_text}»",
            })
        else:
            results.append({
                "check": "title",
                "status": "ok",
                "message": f"Title заполнен корректно ({length} символов): «{title_text}»",
            })

        # Качество title (независимо от длины)
        results.extend(_check_title_quality(title_text))
        meta["title"] = title_text

    # --- description ---
    desc_tag = soup.find("meta", attrs={"name": "description"})
    if not desc_tag or not desc_tag.get("content", "").strip():
        results.append({
            "check": "description",
            "status": "fail",
            "message": "Meta description отсутствует или пустой.",
        })
    else:
        desc_text = desc_tag.get("content", "").strip()
        length = len(desc_text)
        if length < 70:
            results.append({
                "check": "description",
                "status": "warning",
                "message": f"Description слишком короткий ({length} символов).",
            })
        elif length > 160:
            results.append({
                "check": "description",
                "status": "warning",
                "message": f"Description слишком длинный ({length} символов).",
            })
        else:
            results.append({
                "check": "description",
                "status": "ok",
                "message": f"Description заполнен корректно ({length} символов).",
            })

        # Качество description (независимо от длины)
        results.extend(_check_description_quality(desc_text))
        meta["description"] = desc_text

    # --- H1 ---
    h1_tags = soup.find_all("h1")
    if len(h1_tags) == 0:
        results.append({
            "check": "h1",
            "status": "fail",
            "message": "H1 отсутствует на странице.",
        })
    elif len(h1_tags) > 1:
        results.append({
            "check": "h1",
            "status": "warning",
            "message": f"На странице найдено несколько H1 ({len(h1_tags)} шт.). Рекомендуется оставить один.",
        })
    else:
        h1_text = h1_tags[0].text.strip()
        if not h1_text:
            results.append({
                "check": "h1",
                "status": "fail",
                "message": "H1 найден, но пустой.",
            })
        else:
            results.append({
                "check": "h1",
                "status": "ok",
                "message": f"H1 заполнен корректно: «{h1_text[:80]}»",
            })
            meta["h1"] = h1_text

    # --- Текст на странице ---
    body = soup.find("body")
    if body:
        text = body.get_text(separator=" ", strip=True)
        words = len(text.split())
        if words < 100:
            results.append({
                "check": "page_text",
                "status": "warning",
                "message": f"На странице мало текста ({words} слов). Поисковым системам сложнее понять тему страницы.",
            })
        else:
            results.append({
                "check": "page_text",
                "status": "ok",
                "message": f"На странице достаточно текстового контента ({words} слов).",
            })

        # Плотность ключевых слов («тошнота»)
        results.extend(_check_keyword_density(text))

    return results, meta


def fetch_and_evaluate_seo(url: str) -> tuple[list[dict], dict]:
    """Скачивает страницу и разбирает её. Возвращает (проверки, значения тегов)."""
    # Принимаем и голый домен (из аудита), и полный URL с путём (из GEO-чекера)
    base_url = url if url.startswith(("http://", "https://")) else f"https://{url}"
    try:
        response = safe_get(base_url, timeout=10)
    except httpx.RequestError as e:
        return [{
            "check": "seo_basic",
            "status": "fail",
            "message": f"Не удалось проверить страницу: {str(e)}",
        }], {}

    if response.status_code >= 400:
        return [{
            "check": "seo_basic",
            "status": "fail",
            "message": f"Страница отвечает {response.status_code} — разбирать нечего.",
        }], {}

    results, meta = evaluate_seo_html(response.text)
    meta["url"] = str(response.url)
    return results, meta


def check_seo(domain: str) -> list[dict]:
    return fetch_and_evaluate_seo(domain)[0]
