from urllib.parse import urlparse, urlunparse

import httpx

from audits.net_guard import safe_get
from bs4 import BeautifulSoup

from audits.ai_bot_check import AI_BOTS
from audits.robots_rules import parse_robots

# Боты, для которых проверяем доступ отдельно: правила могут закрывать
# не всех (`User-agent: *`), а конкретного робота — такой запрет легко упустить.
MAIN_BOTS = [("Googlebot", "Google"), ("YandexBot", "Яндекс")]


def _evaluate_robots_txt(content: str, http_status: int | None) -> list[dict]:
    """
    Чистая функция: по содержимому robots.txt и HTTP-статусу возвращает
    результаты проверок. Без сети — тестируется напрямую.
    """
    if http_status is None:
        return [{
            "check": "robots_txt",
            "status": "warning",
            "message": "Не удалось получить robots.txt — сайт не ответил.",
        }]

    if http_status in (401, 403):
        # Per RFC 9309: 401/403 на robots.txt = полный запрет обхода
        return [{
            "check": "robots_txt",
            "status": "fail",
            "message": f"robots.txt отвечает {http_status} — по стандарту это "
                       "запрет на обход всего сайта.",
        }]

    if http_status >= 500:
        return [{
            "check": "robots_txt",
            "status": "warning",
            "message": f"robots.txt отвечает {http_status} — ошибка сервера. "
                       "Пока она держится, поисковики приостанавливают обход.",
        }]

    if http_status >= 400:
        return [{
            "check": "robots_txt",
            "status": "warning",
            "message": f"robots.txt не найден (статус {http_status}). Сайт открыт "
                       "для обхода, но правила и ссылка на Sitemap не заданы.",
        }]

    robots = parse_robots(content, http_status)
    results = []

    allowed, rule = robots.check("*", "/")
    if not allowed:
        results.append({
            "check": "robots_txt",
            "status": "fail",
            "message": "robots.txt закрывает от обхода весь сайт: правило "
                       f"«Disallow: {rule.pattern}» для User-agent: *.",
        })
    else:
        blocked = [name for token, name in MAIN_BOTS if not robots.check(token, "/")[0]]
        if blocked:
            results.append({
                "check": "robots_txt",
                "status": "fail",
                "message": "robots.txt закрывает весь сайт для отдельных роботов: "
                           f"{', '.join(blocked)}.",
            })
        else:
            results.append({
                "check": "robots_txt",
                "status": "ok",
                "message": "robots.txt найден, запрета на обход сайта нет.",
            })

    sitemaps = robots.sitemaps
    if sitemaps:
        results.append({
            "check": "robots_sitemap",
            "status": "ok",
            "message": "В robots.txt указан Sitemap: " + ", ".join(sitemaps[:3]),
        })
    else:
        results.append({
            "check": "robots_sitemap",
            "status": "warning",
            "message": "В robots.txt не указана ссылка на Sitemap — поисковикам "
                       "сложнее найти все страницы сайта.",
        })

    return results


def _evaluate_ai_bots(content: str, http_status: int | None, path: str = "/") -> list[dict]:
    """
    Допускает ли robots.txt AI-ботов к path. Критичные боты — те, чья блокировка
    напрямую выбивает сайт из соответствующей AI-выдачи.
    """
    if http_status is None or http_status >= 400:
        return []  # robots.txt нет или недоступен — про ботов сказать нечего

    robots = parse_robots(content, http_status)
    bots = []
    for bot in AI_BOTS:
        allowed, _ = robots.check(bot["token"], path)
        bots.append({
            "label": bot["label"],
            "critical": bot["critical"],
            "allowed": allowed,
        })

    blocked_critical = [b["label"] for b in bots if b["critical"] and not b["allowed"]]
    blocked_minor = [b["label"] for b in bots if not b["critical"] and not b["allowed"]]
    target = "к сайту" if path == "/" else "к странице"

    if blocked_critical:
        status = "fail"
        message = (f"Закрыт доступ ключевым AI-ботам: {', '.join(blocked_critical)}. "
                   "Без обхода бренд не попадёт в их ответы.")
    elif blocked_minor:
        status = "warning"
        message = (f"Ограничен доступ AI-ботам: {', '.join(blocked_minor)}. "
                   "На выдачу влияет слабее, но охват в AI-системах сужается.")
    else:
        status = "ok"
        message = f"Все ключевые AI-боты имеют доступ {target}."

    return [{
        "check": "ai_bots",
        "status": status,
        "message": message,
        "bots": bots,
    }]


def check_robots_txt(domain: str) -> list[dict]:
    base_url = f"https://{domain}"

    try:
        response = safe_get(f"{base_url}/robots.txt", timeout=10)
    except httpx.RequestError as e:
        return [{
            "check": "robots_txt",
            "status": "warning",
            "message": f"Не удалось проверить robots.txt: {str(e)}",
        }]

    return (_evaluate_robots_txt(response.text, response.status_code)
            + _evaluate_ai_bots(response.text, response.status_code))


def _normalize_page_url(raw: str) -> str:
    """example.ru/page → https://example.ru/page; путь пустой → /"""
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw.lstrip("/")
    parsed = urlparse(raw)
    return urlunparse(parsed._replace(path=parsed.path or "/", fragment=""))


def _evaluate_page_robots(robots_content, robots_status, path) -> list[dict]:
    """
    Закрыта ли конкретная страница в robots.txt и каким именно правилом.
    Отдельно смотрим Googlebot и YandexBot: запрет может быть адресным.
    """
    robots = parse_robots(robots_content, robots_status)

    if robots.disallow_all:
        return [{
            "check": "page_robots",
            "status": "fail",
            "message": f"robots.txt отвечает {robots_status} — по стандарту закрыт "
                       "обход всего сайта, включая эту страницу.",
        }]

    allowed, rule = robots.check("*", path)
    if not allowed:
        return [{
            "check": "page_robots",
            "status": "fail",
            "message": f"Страница закрыта в robots.txt правилом «Disallow: "
                       f"{rule.pattern}» в блоке User-agent: *.",
        }]

    blocked = []
    for token, name in MAIN_BOTS:
        bot_allowed, bot_rule = robots.check(token, path)
        if not bot_allowed:
            blocked.append(f"{name} — «Disallow: {bot_rule.pattern}»")
    if blocked:
        return [{
            "check": "page_robots",
            "status": "fail",
            "message": "Страница закрыта в robots.txt для отдельных роботов: "
                       + "; ".join(blocked) + ".",
        }]

    if rule is not None and rule.allow:
        return [{
            "check": "page_robots",
            "status": "ok",
            "message": f"robots.txt обход разрешает: правило «Allow: {rule.pattern}» "
                       "перекрывает более общий запрет.",
        }]

    return [{
        "check": "page_robots",
        "status": "ok",
        "message": "robots.txt обход страницы не запрещает.",
    }]


def _evaluate_page_html(url, status_code, headers, html) -> list[dict]:
    """Мета-теги, заголовки и canonical конкретной страницы. Без сети."""
    results = []

    if status_code >= 400:
        results.append({
            "check": "page_status",
            "status": "fail",
            "message": f"Страница отвечает {status_code} — в индекс она не попадёт.",
        })
        return results

    results.append({
        "check": "page_status",
        "status": "ok",
        "message": f"Страница отвечает {status_code} — доступна для обхода.",
    })

    x_robots = (headers.get("x-robots-tag") or "").lower()
    if "noindex" in x_robots:
        results.append({
            "check": "page_x_robots",
            "status": "fail",
            "message": f"HTTP-заголовок X-Robots-Tag: {x_robots} — страница закрыта "
                       "от индексации на уровне сервера.",
        })
    elif "nofollow" in x_robots:
        results.append({
            "check": "page_x_robots",
            "status": "warning",
            "message": f"HTTP-заголовок X-Robots-Tag: {x_robots} — ссылки со "
                       "страницы не передают вес.",
        })

    soup = BeautifulSoup(html, "lxml")

    meta = soup.find("meta", attrs={"name": lambda v: v and v.lower() in ("robots", "googlebot", "yandex")})
    content = (meta.get("content", "") if meta else "").lower()
    if "noindex" in content:
        results.append({
            "check": "page_meta_robots",
            "status": "fail",
            "message": f"На странице стоит мета-тег robots: {content} — она "
                       "исключена из индекса.",
        })
    elif "none" in content:
        results.append({
            "check": "page_meta_robots",
            "status": "fail",
            "message": "На странице стоит мета-тег robots: none — это то же самое, "
                       "что noindex, nofollow.",
        })
    elif "nofollow" in content:
        results.append({
            "check": "page_meta_robots",
            "status": "warning",
            "message": f"На странице стоит мета-тег robots: {content} — индексация "
                       "разрешена, но ссылки не передают вес.",
        })
    else:
        results.append({
            "check": "page_meta_robots",
            "status": "ok",
            "message": "Мета-тег robots индексацию не запрещает.",
        })

    canonical = soup.find("link", attrs={"rel": lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])]})
    href = (canonical.get("href") or "").strip() if canonical else ""
    if href:
        if _same_page(href, url):
            results.append({
                "check": "page_canonical",
                "status": "ok",
                "message": f"Canonical указывает на саму страницу: {href}",
            })
        else:
            results.append({
                "check": "page_canonical",
                "status": "warning",
                "message": f"Canonical указывает на другой адрес — {href}. "
                           "В индекс поисковик поставит именно его.",
            })
    else:
        results.append({
            "check": "page_canonical",
            "status": "warning",
            "message": "Canonical не указан — при дублях поисковик выберет "
                       "основной адрес сам.",
        })

    return results


def _same_page(href: str, url: str) -> bool:
    """Canonical может быть относительным и с www — сравниваем по сути."""
    a, b = urlparse(href), urlparse(url)
    host_a = (a.netloc or b.netloc).removeprefix("www.")
    return host_a == b.netloc.removeprefix("www.") and a.path.rstrip("/") == b.path.rstrip("/")


def check_page(raw_url: str) -> list[dict]:
    """Проверяет одну страницу: закрыта ли она от индексации и почему."""
    url = _normalize_page_url(raw_url)
    parsed = urlparse(url)
    path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

    robots_content, robots_status = "", None
    try:
        robots_response = safe_get(
            f"{parsed.scheme}://{parsed.netloc}/robots.txt", timeout=10
        )
        robots_content, robots_status = robots_response.text, robots_response.status_code
    except httpx.RequestError:
        pass

    results = []
    if robots_status is None:
        results.append({
            "check": "page_robots",
            "status": "warning",
            "message": "robots.txt получить не удалось — проверить запрет обхода "
                       "не вышло.",
        })
    else:
        results.extend(_evaluate_page_robots(robots_content, robots_status, path))
        results.extend(_evaluate_ai_bots(robots_content, robots_status, path))

    try:
        response = safe_get(url, timeout=15)
    except httpx.RequestError as e:
        results.append({
            "check": "page_status",
            "status": "fail",
            "message": f"Страница не открылась: {str(e)}",
        })
        return results

    if str(response.url) != url:
        results.append({
            "check": "page_redirect",
            "status": "warning",
            "message": f"Адрес редиректит на {response.url} — проверяем конечную "
                       "страницу.",
        })

    results.extend(_evaluate_page_html(str(response.url), response.status_code,
                                       response.headers, response.text))
    return results


def check_indexability(domain: str) -> list[dict]:
    results = []
    base_url = f"https://{domain}"

    # --- robots.txt --- (та же проверка, что и в бесплатном инструменте)
    results.extend(check_robots_txt(domain))

    # --- sitemap.xml ---
    try:
        sitemap_url = f"{base_url}/sitemap.xml"
        response = safe_get(sitemap_url, timeout=10)

        if response.status_code == 200:
            results.append({
                "check": "sitemap_xml",
                "status": "ok",
                "message": "sitemap.xml найден и доступен.",
            })
        else:
            results.append({
                "check": "sitemap_xml",
                "status": "fail",
                "message": f"sitemap.xml недоступен (статус {response.status_code}).",
            })

    except httpx.RequestError as e:
        results.append({
            "check": "sitemap_xml",
            "status": "fail",
            "message": f"Не удалось проверить sitemap.xml: {str(e)}",
        })

    # --- Главная страница: noindex, canonical, X-Robots-Tag ---
    try:
        response = safe_get(base_url, timeout=10)
        soup = BeautifulSoup(response.text, "lxml")

        meta_robots = soup.find("meta", attrs={"name": "robots"})
        if meta_robots:
            content = meta_robots.get("content", "").lower()
            if "noindex" in content:
                results.append({
                    "check": "meta_robots",
                    "status": "fail",
                    "message": "Главная страница закрыта от индексации через meta robots noindex.",
                })
            else:
                results.append({
                    "check": "meta_robots",
                    "status": "ok",
                    "message": "Meta robots настроен корректно, noindex не найден.",
                })
        else:
            results.append({
                "check": "meta_robots",
                "status": "ok",
                "message": "Meta robots не задан — страница открыта для индексации.",
            })

        x_robots = response.headers.get("x-robots-tag", "").lower()
        if "noindex" in x_robots:
            results.append({
                "check": "x_robots_tag",
                "status": "fail",
                "message": "В HTTP-заголовках найден X-Robots-Tag: noindex.",
            })
        else:
            results.append({
                "check": "x_robots_tag",
                "status": "ok",
                "message": "X-Robots-Tag не блокирует индексацию.",
            })

        canonical = soup.find("link", attrs={"rel": "canonical"})
        if canonical:
            href = canonical.get("href", "")
            if domain in href:
                results.append({
                    "check": "canonical",
                    "status": "ok",
                    "message": f"Canonical указан корректно: {href}",
                })
            else:
                results.append({
                    "check": "canonical",
                    "status": "warning",
                    "message": f"Canonical указывает на другой URL: {href}",
                })
        else:
            results.append({
                "check": "canonical",
                "status": "warning",
                "message": "Canonical не найден на главной странице.",
            })

    except httpx.RequestError as e:
        results.append({
            "check": "main_page_indexability",
            "status": "fail",
            "message": f"Не удалось проверить главную страницу: {str(e)}",
        })

    # --- www/non-www редирект ---
    try:
        www_url = f"https://www.{domain}"
        www_response = safe_get(www_url, follow_redirects=False, timeout=10)
        non_www_response = safe_get(base_url, follow_redirects=False, timeout=10)

        www_redirects = www_response.status_code in (301, 302)
        non_www_redirects = non_www_response.status_code in (301, 302)

        if www_redirects or non_www_redirects:
            results.append({
                "check": "www_redirect",
                "status": "ok",
                "message": "Настроен редирект между www и без-www версиями сайта.",
            })
        else:
            results.append({
                "check": "www_redirect",
                "status": "warning",
                "message": "Редирект между www и без-www версиями сайта не настроен. Возможны дубли страниц.",
            })
    except httpx.RequestError:
        results.append({
            "check": "www_redirect",
            "status": "warning",
            "message": "Не удалось проверить www/без-www редирект.",
        })

    # --- Дубли главной страницы (www / без-www / со слешем) ---
    try:
        variants = [
            f"https://{domain}",
            f"https://{domain}/",
            f"https://www.{domain}",
            f"https://www.{domain}/",
        ]
        accessible = []
        for url in variants:
            try:
                r = safe_get(url, follow_redirects=False, timeout=8)
                if r.status_code == 200:
                    accessible.append(url)
            except httpx.RequestError:
                pass

        if len(accessible) > 1:
            results.append({
                "check": "homepage_duplicates",
                "status": "warning",
                "message": f"Несколько вариантов главной доступны без редиректа: {', '.join(accessible)}. Возможно дублирование для поисковиков.",
            })
        else:
            results.append({
                "check": "homepage_duplicates",
                "status": "ok",
                "message": "Дублей главной страницы не обнаружено — все варианты редиректят на один.",
            })
    except Exception:
        pass

    return results