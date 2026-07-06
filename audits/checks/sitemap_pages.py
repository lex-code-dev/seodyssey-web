import random
import httpx
from bs4 import BeautifulSoup

def _detect_author_date(page_soup) -> tuple[bool, bool]:
    """Возвращает (есть_автор, есть_дата) по машиночитаемым сигналам E-E-A-T."""
    # --- Автор ---
    author_found = False
    meta_author = page_soup.find("meta", attrs={"name": "author"})
    if meta_author and meta_author.get("content", "").strip():
        author_found = True
    if not author_found and page_soup.find("meta", attrs={"property": "article:author"}):
        author_found = True
    if not author_found and page_soup.find(attrs={"itemprop": "author"}):
        author_found = True
    if not author_found and page_soup.find(attrs={"rel": "author"}):
        author_found = True
    if not author_found:
        for tag in page_soup.find_all("script", attrs={"type": "application/ld+json"}):
            if tag.string and '"author"' in tag.string:
                author_found = True
                break

    # --- Дата ---
    date_found = False
    for prop in ("article:published_time", "article:modified_time"):
        if page_soup.find("meta", attrs={"property": prop}):
            date_found = True
            break
    if not date_found and page_soup.find("meta", attrs={"name": "date"}):
        date_found = True
    if not date_found and page_soup.find("time", attrs={"datetime": True}):
        date_found = True
    if not date_found and page_soup.find(attrs={"itemprop": "datePublished"}):
        date_found = True
    if not date_found:
        for tag in page_soup.find_all("script", attrs={"type": "application/ld+json"}):
            if tag.string and '"datePublished"' in tag.string:
                date_found = True
                break

    return author_found, date_found


def check_sitemap_pages(domain: str) -> list[dict]:
    results = []
    base_url = f"https://{domain}"

    # --- Получаем sitemap ---
    try:
        sitemap_url = f"{base_url}/sitemap.xml"
        response = httpx.get(sitemap_url, follow_redirects=True, timeout=10)
        if response.status_code != 200:
            return []
    except httpx.RequestError:
        return []

    # --- Парсим URL из sitemap ---
    try:
        soup = BeautifulSoup(response.text, "lxml-xml")
        urls = [loc.text.strip() for loc in soup.find_all("loc")]
    except Exception:
        return []

    if not urls:
        return []

    # Исключаем главную
    main_url_variants = {
        base_url,
        base_url + "/",
        f"https://www.{domain}",
        f"https://www.{domain}/",
    }
    urls = [u for u in urls if u not in main_url_variants]

    if not urls:
        return []

    # Случайные 10 (или все если меньше 10)
    sample_size = min(10, len(urls))
    urls_to_check = random.sample(urls, sample_size)
    total = len(urls)

    results.append({
        "check": "sitemap_pages_info",
        "status": "ok",
        "message": f"Проверено {sample_size} случайных страниц из {total} URL в Sitemap.",
    })

    # --- Проверяем каждый URL ---
    titles = []
    descriptions = []
    h1s = []
    issues = []
    pages_without_author = 0
    pages_without_date = 0
    pages_checked = 0

    for url in urls_to_check:
        short_url = url.replace(base_url, "") or "/"

        try:
            r = httpx.get(url, follow_redirects=False, timeout=10)
            status = r.status_code

            # Редирект
            if status in (301, 302):
                issues.append({
                    "check": "sitemap_page_redirect",
                    "status": "warning",
                    "message": f"{short_url} — редирект ({status}). В Sitemap лучше указывать конечные URL.",
                })
                continue

            # Ошибка
            if status != 200:
                issues.append({
                    "check": "sitemap_page_status",
                    "status": "fail",
                    "message": f"{short_url} — статус {status}. Страница недоступна.",
                })
                continue

            page_soup = BeautifulSoup(r.text, "lxml")

            # noindex
            meta_robots = page_soup.find("meta", attrs={"name": "robots"})
            if meta_robots and "noindex" in meta_robots.get("content", "").lower():
                issues.append({
                    "check": "sitemap_page_noindex",
                    "status": "warning",
                    "message": f"{short_url} — закрыта от индексации (noindex), но есть в Sitemap.",
                })

            # title
            title_tag = page_soup.find("title")
            title_text = title_tag.text.strip() if title_tag else ""
            if not title_text:
                issues.append({
                    "check": "sitemap_page_title",
                    "status": "fail",
                    "message": f"{short_url} — title отсутствует.",
                })
            else:
                titles.append(title_text)
                length = len(title_text)
                if length < 30:
                    issues.append({
                        "check": "sitemap_page_title_short",
                        "status": "warning",
                        "message": f"{short_url} — title слишком короткий ({length} символов).",
                    })
                elif length > 80:
                    issues.append({
                        "check": "sitemap_page_title_long",
                        "status": "warning",
                        "message": f"{short_url} — title слишком длинный ({length} символов).",
                    })

            # description
            desc_tag = page_soup.find("meta", attrs={"name": "description"})
            desc_text = desc_tag.get("content", "").strip() if desc_tag else ""
            if not desc_text:
                issues.append({
                    "check": "sitemap_page_description",
                    "status": "warning",
                    "message": f"{short_url} — description отсутствует.",
                })
            else:
                descriptions.append(desc_text)
                length = len(desc_text)
                if length < 70:
                    issues.append({
                        "check": "sitemap_page_desc_short",
                        "status": "warning",
                        "message": f"{short_url} — description слишком короткий ({length} символов).",
                    })
                elif length > 160:
                    issues.append({
                        "check": "sitemap_page_desc_long",
                        "status": "warning",
                        "message": f"{short_url} — description слишком длинный ({length} символов).",
                    })

            # H1
            h1_tags = page_soup.find_all("h1")
            if not h1_tags:
                issues.append({
                    "check": "sitemap_page_h1",
                    "status": "fail",
                    "message": f"{short_url} — H1 отсутствует.",
                })
            elif len(h1_tags) > 1:
                issues.append({
                    "check": "sitemap_page_h1_multiple",
                    "status": "warning",
                    "message": f"{short_url} — найдено несколько H1 ({len(h1_tags)} шт.).",
                })
            else:
                h1_text = h1_tags[0].text.strip()
                if not h1_text:
                    issues.append({
                        "check": "sitemap_page_h1_empty",
                        "status": "fail",
                        "message": f"{short_url} — H1 найден, но пустой.",
                    })
                else:
                    h1s.append(h1_text)

            # H2 (структура контента)
            h2_tags = page_soup.find_all("h2")
            if not h2_tags:
                issues.append({
                    "check": "sitemap_page_h2",
                    "status": "warning",
                    "message": f"{short_url} — H2 не найден. Структура контента может быть слабой.",
                })

            # Объём текста
            body = page_soup.find("body")
            if body:
                words = len(body.get_text(separator=" ", strip=True).split())
                if words < 100:
                    issues.append({
                        "check": "sitemap_page_text",
                        "status": "warning",
                        "message": f"{short_url} — мало текста ({words} слов). Страница может быть слабо оптимизирована.",
                    })

            # Автор и дата (E-E-A-T) — считаем только успешно загруженные 200-страницы
            pages_checked += 1
            has_author, has_date = _detect_author_date(page_soup)
            if not has_author:
                pages_without_author += 1
            if not has_date:
                pages_without_date += 1

        except httpx.RequestError:
            issues.append({
                "check": "sitemap_page_unavailable",
                "status": "fail",
                "message": f"{short_url} — страница недоступна.",
            })

    # --- Дубли title ---
    duplicate_titles = [t for t in set(titles) if titles.count(t) > 1]
    if duplicate_titles:
        results.append({
            "check": "sitemap_duplicate_titles",
            "status": "warning",
            "message": f"Найдены дубли title: «{duplicate_titles[0][:60]}».",
        })
    else:
        results.append({
            "check": "sitemap_duplicate_titles",
            "status": "ok",
            "message": "Дублей title на проверенных страницах не найдено.",
        })

    # --- Дубли H1 ---
    duplicate_h1s = [h for h in set(h1s) if h1s.count(h) > 1]
    if duplicate_h1s:
        results.append({
            "check": "sitemap_duplicate_h1",
            "status": "warning",
            "message": f"Найдены дубли H1: «{duplicate_h1s[0][:60]}».",
        })
    else:
        results.append({
            "check": "sitemap_duplicate_h1",
            "status": "ok",
            "message": "Дублей H1 на проверенных страницах не найдено.",
        })

    # --- Дубли description ---
    duplicate_descs = [d for d in set(descriptions) if descriptions.count(d) > 1]
    if duplicate_descs:
        results.append({
            "check": "sitemap_duplicate_descriptions",
            "status": "warning",
            "message": "Найдены дубли description на проверенных страницах.",
        })
    else:
        results.append({
            "check": "sitemap_duplicate_descriptions",
            "status": "ok",
            "message": "Дублей description на проверенных страницах не найдено.",
        })

    # --- Авторство и дата (агрегат по проверенным страницам) ---
    if pages_checked:
        if pages_without_author == 0:
            results.append({
                "check": "sitemap_author",
                "status": "ok",
                "message": "На всех проверенных страницах указан автор контента (E-E-A-T).",
            })
        else:
            results.append({
                "check": "sitemap_author",
                "status": "warning",
                "message": f"На {pages_without_author} из {pages_checked} проверенных страниц не указан автор. Для статей авторство усиливает экспертность.",
            })

        if pages_without_date == 0:
            results.append({
                "check": "sitemap_date",
                "status": "ok",
                "message": "На всех проверенных страницах есть дата публикации/обновления.",
            })
        else:
            results.append({
                "check": "sitemap_date",
                "status": "warning",
                "message": f"На {pages_without_date} из {pages_checked} проверенных страниц нет даты публикации. Дата помогает оценить актуальность.",
            })

    results.extend(issues)
    return results