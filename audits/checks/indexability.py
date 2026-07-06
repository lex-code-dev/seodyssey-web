import httpx
from bs4 import BeautifulSoup


def check_robots_txt(domain: str) -> list[dict]:
    results = []
    base_url = f"https://{domain}"

    try:
        robots_url = f"{base_url}/robots.txt"
        response = httpx.get(robots_url, follow_redirects=True, timeout=10)

        if response.status_code == 200:
            robots_text = response.text

            if "Disallow: /" in robots_text and "Disallow: /\n" in robots_text:
                results.append({
                    "check": "robots_txt",
                    "status": "fail",
                    "message": "В robots.txt найден запрет на обход всего сайта (Disallow: /).",
                })
            else:
                results.append({
                    "check": "robots_txt",
                    "status": "ok",
                    "message": "robots.txt найден, критических запретов не обнаружено.",
                })

            if "Sitemap:" in robots_text:
                results.append({
                    "check": "robots_sitemap",
                    "status": "ok",
                    "message": "В robots.txt указан Sitemap.",
                })
            else:
                results.append({
                    "check": "robots_sitemap",
                    "status": "warning",
                    "message": "В robots.txt не указан Sitemap.",
                })
        else:
            results.append({
                "check": "robots_txt",
                "status": "warning",
                "message": f"robots.txt недоступен (статус {response.status_code}).",
            })

    except httpx.RequestError as e:
        results.append({
            "check": "robots_txt",
            "status": "warning",
            "message": f"Не удалось проверить robots.txt: {str(e)}",
        })

    return results


def check_indexability(domain: str) -> list[dict]:
    results = []
    base_url = f"https://{domain}"

    # --- robots.txt ---
    try:
        robots_url = f"{base_url}/robots.txt"
        response = httpx.get(robots_url, follow_redirects=True, timeout=10)

        if response.status_code == 200:
            robots_text = response.text

            if "Disallow: /" in robots_text and "Disallow: /\n" in robots_text:
                results.append({
                    "check": "robots_txt",
                    "status": "fail",
                    "message": "В robots.txt найден запрет на обход всего сайта (Disallow: /).",
                })
            else:
                results.append({
                    "check": "robots_txt",
                    "status": "ok",
                    "message": "robots.txt найден, критических запретов не обнаружено.",
                })

            if "Sitemap:" in robots_text:
                results.append({
                    "check": "robots_sitemap",
                    "status": "ok",
                    "message": "В robots.txt указан Sitemap.",
                })
            else:
                results.append({
                    "check": "robots_sitemap",
                    "status": "warning",
                    "message": "В robots.txt не указан Sitemap.",
                })
        else:
            results.append({
                "check": "robots_txt",
                "status": "warning",
                "message": f"robots.txt недоступен (статус {response.status_code}).",
            })

    except httpx.RequestError as e:
        results.append({
            "check": "robots_txt",
            "status": "warning",
            "message": f"Не удалось проверить robots.txt: {str(e)}",
        })

    # --- sitemap.xml ---
    try:
        sitemap_url = f"{base_url}/sitemap.xml"
        response = httpx.get(sitemap_url, follow_redirects=True, timeout=10)

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
        response = httpx.get(base_url, follow_redirects=True, timeout=10)
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
        www_response = httpx.get(www_url, follow_redirects=False, timeout=10)
        non_www_response = httpx.get(base_url, follow_redirects=False, timeout=10)

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
                r = httpx.get(url, follow_redirects=False, timeout=8)
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