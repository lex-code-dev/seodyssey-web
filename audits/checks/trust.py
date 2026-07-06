import re
import httpx
from bs4 import BeautifulSoup

def check_trust(domain: str) -> list[dict]:
    results = []
    base_url = f"https://{domain}"

    try:
        response = httpx.get(base_url, follow_redirects=True, timeout=10)
        soup = BeautifulSoup(response.text, "lxml")
        text = soup.get_text(separator=" ", strip=True)
        html = response.text

        # --- Телефон ---
        phone_pattern = re.compile(r'(\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}')
        if phone_pattern.search(text):
            results.append({
                "check": "phone",
                "status": "ok",
                "message": "На странице найден номер телефона.",
            })
        else:
            results.append({
                "check": "phone",
                "status": "warning",
                "message": "Номер телефона не найден на главной странице.",
            })

        # --- Email ---
        email_pattern = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
        if email_pattern.search(text):
            results.append({
                "check": "email",
                "status": "ok",
                "message": "На странице найден email.",
            })
        else:
            results.append({
                "check": "email",
                "status": "warning",
                "message": "Email не найден на главной странице.",
            })

        # --- Ссылка на контакты ---
        contacts_link = soup.find("a", href=re.compile(r'contact|kontakt|контакт', re.I))
        if contacts_link:
            results.append({
                "check": "contacts_link",
                "status": "ok",
                "message": "Найдена ссылка на страницу контактов.",
            })
        else:
            results.append({
                "check": "contacts_link",
                "status": "warning",
                "message": "Ссылка на страницу контактов не найдена.",
            })

        # --- Политика конфиденциальности ---
        privacy_link = soup.find("a", href=re.compile(r'privacy|politic|конфиденц|personal|персональн', re.I))
        if privacy_link:
            results.append({
                "check": "privacy_policy",
                "status": "ok",
                "message": "Найдена ссылка на политику конфиденциальности.",
            })
        else:
            results.append({
                "check": "privacy_policy",
                "status": "warning",
                "message": "Ссылка на политику конфиденциальности не найдена.",
            })

        # --- Яндекс.Метрика ---
        if "mc.yandex.ru" in html or "ym(" in html:
            results.append({
                "check": "yandex_metrika",
                "status": "ok",
                "message": "Найден счётчик Яндекс.Метрики.",
            })
        else:
            results.append({
                "check": "yandex_metrika",
                "status": "warning",
                "message": "Счётчик Яндекс.Метрики не найден.",
            })

        # --- Google Analytics / GTM ---
        if "gtag(" in html or "dataLayer" in html or "GTM-" in html:
            results.append({
                "check": "google_analytics",
                "status": "ok",
                "message": "Найден Google Analytics или GTM.",
            })
        else:
            results.append({
                "check": "google_analytics",
                "status": "warning",
                "message": "Google Analytics или GTM не найден.",
            })

        # --- Viewport (мобильная адаптация) ---
        viewport = soup.find("meta", attrs={"name": "viewport"})
        if viewport:
            results.append({
                "check": "viewport",
                "status": "ok",
                "message": "Viewport meta tag найден, мобильная адаптация настроена.",
            })
        else:
            results.append({
                "check": "viewport",
                "status": "warning",
                "message": "Viewport meta tag не найден. Возможны проблемы с отображением на мобильных.",
            })

        # --- Адрес ---
        address_keywords = ["г.", "ул.", "пр.", "д.", "офис", "этаж", "улица", "проспект", "переулок"]
        if any(kw in text for kw in address_keywords):
            results.append({
                "check": "address",
                "status": "ok",
                "message": "На странице найден адрес.",
            })
        else:
            results.append({
                "check": "address",
                "status": "warning",
                "message": "Адрес не найден на главной странице.",
            })

        # --- Реквизиты ---
        requisites_pattern = re.compile(r'(ИНН|ОГРН|КПП|ОКПО)\s*:?\s*\d{9,13}')
        if requisites_pattern.search(text.upper()):
            results.append({
                "check": "requisites",
                "status": "ok",
                "message": "На странице найдены реквизиты компании (ИНН/ОГРН).",
            })
        else:
            results.append({
                "check": "requisites",
                "status": "warning",
                "message": "Реквизиты компании (ИНН/ОГРН) не найдены.",
            })

        # --- Пользовательское соглашение ---
        terms_link = soup.find("a", href=re.compile(r'terms|oferta|оферт|соглашени', re.I))
        if terms_link:
            results.append({
                "check": "terms_of_service",
                "status": "ok",
                "message": "Найдена ссылка на пользовательское соглашение.",
            })
        else:
            results.append({
                "check": "terms_of_service",
                "status": "warning",
                "message": "Ссылка на пользовательское соглашение не найдена.",
            })

        # --- Отзывы ---
        review_keywords = ["отзыв", "отзывы", "отзывах", "reviews", "testimonial"]
        if any(kw in text for kw in review_keywords):
            results.append({
                "check": "reviews",
                "status": "ok",
                "message": "На странице найден блок с отзывами.",
            })
        else:
            results.append({
                "check": "reviews",
                "status": "warning",
                "message": "Блок с отзывами не найден. Отзывы повышают доверие пользователей и поисковых систем.",
            })


    except httpx.RequestError as e:
        results.append({
            "check": "trust",
            "status": "fail",
            "message": f"Не удалось проверить страницу: {str(e)}",
        })

    return results