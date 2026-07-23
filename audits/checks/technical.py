import ssl
import socket
import time
import httpx

from audits.net_guard import safe_get


def check_technical(domain: str) -> list[dict]:
    results = []
    url = f"https://{domain}"

    # --- Доступность и статус ---
    try:
        start = time.time()
        response = safe_get(url, timeout=10)
        elapsed = round(time.time() - start, 2)
        status = response.status_code

        if status == 200:
            results.append({
                "check": "site_available",
                "status": "ok",
                "message": f"Сайт доступен, статус {status}",
            })
        else:
            results.append({
                "check": "site_available",
                "status": "fail",
                "message": f"Сайт вернул статус {status}",
            })

        # --- Скорость ответа ---
        if elapsed <= 1.5:
            results.append({
                "check": "response_time",
                "status": "ok",
                "message": f"Время ответа сервера: {elapsed} сек.",
            })
        elif elapsed <= 3.0:
            results.append({
                "check": "response_time",
                "status": "warning",
                "message": f"Время ответа сервера: {elapsed} сек. Немного медленно.",
            })
        else:
            results.append({
                "check": "response_time",
                "status": "fail",
                "message": f"Время ответа сервера: {elapsed} сек. Слишком медленно.",
            })

        # --- Редирект HTTP → HTTPS ---
        http_url = f"http://{domain}"
        http_response = safe_get(http_url, follow_redirects=False, timeout=10)
        if http_response.status_code in (301, 302) and "https" in http_response.headers.get("location", ""):
            results.append({
                "check": "http_to_https",
                "status": "ok",
                "message": "Редирект с HTTP на HTTPS настроен.",
            })
        else:
            results.append({
                "check": "http_to_https",
                "status": "warning",
                "message": "Редирект с HTTP на HTTPS не обнаружен.",
            })

        # --- Цепочка редиректов ---
        redirect_response = safe_get(url, timeout=10)
        hops = len(redirect_response.history)
        if hops <= 1:
            results.append({
                "check": "redirect_chain",
                "status": "ok",
                "message": "Цепочка редиректов в норме.",
            })
        else:
            results.append({
                "check": "redirect_chain",
                "status": "warning",
                "message": f"Обнаружена цепочка редиректов: {hops} переходов.",
            })

    except httpx.RequestError as e:
        results.append({
            "check": "site_available",
            "status": "fail",
            "message": f"Сайт недоступен: {str(e)}",
        })
        return results

    # --- SSL-сертификат ---
    try:
        ctx = ssl.create_default_context()
        with ctx.wrap_socket(socket.socket(), server_hostname=domain) as s:
            s.settimeout(10)
            s.connect((domain, 443))
            cert = s.getpeercert()

        import datetime
        expire_str = cert.get("notAfter", "")
        expire_date = datetime.datetime.strptime(expire_str, "%b %d %H:%M:%S %Y %Z")
        days_left = (expire_date - datetime.datetime.utcnow()).days

        if days_left > 30:
            results.append({
                "check": "ssl_certificate",
                "status": "ok",
                "message": f"SSL-сертификат активен, истекает через {days_left} дней.",
            })
        elif days_left > 0:
            results.append({
                "check": "ssl_certificate",
                "status": "warning",
                "message": f"SSL-сертификат истекает через {days_left} дней. Проверьте автопродление.",
            })
        else:
            results.append({
                "check": "ssl_certificate",
                "status": "fail",
                "message": "SSL-сертификат истёк.",
            })

    except Exception as e:
        results.append({
            "check": "ssl_certificate",
            "status": "fail",
            "message": f"Не удалось проверить SSL: {str(e)}",
        })

    # --- Срок домена (WHOIS) ---
    try:
        import whois
        import datetime
        w = whois.whois(domain)
        expiry = w.expiration_date
        if isinstance(expiry, list):
            expiry = expiry[0]
        if expiry:
            if expiry.tzinfo is not None:
                now = datetime.datetime.now(datetime.timezone.utc)
            else:
                now = datetime.datetime.now()
            days_left = (expiry - now).days
            if days_left > 30:
                results.append({
                    "check": "domain_expiry",
                    "status": "ok",
                    "message": f"Домен активен, истекает через {days_left} дней ({expiry.strftime('%d.%m.%Y')}).",
                })
            elif days_left > 0:
                results.append({
                    "check": "domain_expiry",
                    "status": "warning",
                    "message": f"Домен истекает через {days_left} дней. Проверьте автопродление.",
                })
            else:
                results.append({
                    "check": "domain_expiry",
                    "status": "fail",
                    "message": "Срок регистрации домена истёк.",
                })
        else:
            results.append({
                "check": "domain_expiry",
                "status": "warning",
                "message": "Не удалось определить срок истечения домена.",
            })
    except Exception as e:
        results.append({
            "check": "domain_expiry",
            "status": "warning",
            "message": f"Не удалось проверить WHOIS домена: {str(e)}",
        })

    # --- DNS-записи ---
    try:
        import dns.resolver
        checks = {
            "A": "A-запись (IPv4)",
            "MX": "MX-запись (почта)",
        }
        for record_type, label in checks.items():
            try:
                dns.resolver.resolve(domain, record_type)
                results.append({
                    "check": f"dns_{record_type.lower()}",
                    "status": "ok",
                    "message": f"{label} найдена.",
                })
            except Exception:
                results.append({
                    "check": f"dns_{record_type.lower()}",
                    "status": "warning",
                    "message": f"{label} не найдена.",
                })
    except Exception as e:
        results.append({
            "check": "dns",
            "status": "warning",
            "message": f"Не удалось проверить DNS: {str(e)}",
        })

    return results