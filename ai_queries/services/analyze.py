from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from ai_queries.services.llm import (
    try_llm_analyze,
    try_llm_expand_queries,
    try_llm_refine_queries,
)
from ai_queries.services.scoring import evaluate_query_set
from ai_queries.services.types import PageData

USER_AGENT = "SEOdyssey-Analyzer/1.0"

BAD_BRAND_WORDS = {"главная", "главный", "официальный сайт", "официальный", "сайт"}

NOISE_PHRASES = {
    "персональные данные", "политика конфиденциальности",
    "пользовательское соглашение", "условия использования",
    "cookie", "cookies", "карта сайта",
}

BRAND_STOP_WORDS = {
    "ооо", "ао", "зао", "ип", "пao", "company",
    "компания", "группа", "group", "официальный", "сайт",
}

CITY_KEYWORDS = {
    "москва": "Москва", "санкт-петербург": "Санкт-Петербург",
    "петербург": "Санкт-Петербург", "екатеринбург": "Екатеринбург",
    "новосибирск": "Новосибирск", "казань": "Казань",
    "нижний новгород": "Нижний Новгород", "самара": "Самара",
    "челябинск": "Челябинск", "омск": "Омск", "уфа": "Уфа",
    "ростов-на-дону": "Ростов-на-Дону", "краснодар": "Краснодар",
    "пермь": "Пермь", "волгоград": "Волгоград", "воронеж": "Воронеж",
}

LINK_PRIORITY_HINTS = [
    "/about", "/about-us", "/company", "/contacts", "/contact",
    "/services", "/service", "/products", "/product", "/solutions",
    "/catalog", "/pricing", "/price", "/tariffs", "/faq",
    "/о-компании", "/контакты", "/услуги", "/продукты", "/решения", "/цены",
]

FETCH_TIMEOUT_S = 15.0
FETCH_MAX_BYTES = 1_000_000
ANALYSIS_MAX_PAGES = 2
MAX_EXPAND_ROUNDS = 1
ENABLE_REFINE = False
MAX_BRAND_MENTIONS = 6


def fetch_html(url: str) -> str:
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=FETCH_TIMEOUT_S, follow_redirects=True, headers=headers) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise ValueError(f"Неподдерживаемый content-type: {content_type}")
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                total += len(chunk)
                if total > FETCH_MAX_BYTES:
                    raise ValueError(f"Страница слишком большая (> {FETCH_MAX_BYTES} bytes)")
                chunks.append(chunk)
            encoding = resp.encoding or "utf-8"
            return b"".join(chunks).decode(encoding, errors="ignore")


def _link_priority_score(link: str) -> int:
    path = (urlparse(link).path or "").lower()
    score = 0
    for idx, hint in enumerate(LINK_PRIORITY_HINTS):
        if hint in path:
            score += 100 - idx
    if path.count("/") <= 2:
        score += 10
    if any(path.endswith(ext) for ext in [".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip"]):
        score -= 1000
    return score


def extract_internal_links(html: str, base_url: str, limit: int = 10) -> list[str]:
    base_host = urlparse(base_url).netloc.lower().replace("www.", "")
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    for tag in soup.find_all("a", href=True):
        raw = (tag.get("href") or "").strip()
        if not raw or raw.startswith("#") or raw.startswith("mailto:") or raw.startswith("tel:"):
            continue
        full = urljoin(base_url, raw)
        parsed = urlparse(full)
        if parsed.scheme not in {"http", "https"}:
            continue
        host = parsed.netloc.lower().replace("www.", "")
        if host != base_host:
            continue
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if not normalized or normalized in seen or normalized.rstrip("/") == base_url.rstrip("/"):
            continue
        seen.add(normalized)
        links.append(normalized)
    links.sort(key=_link_priority_score, reverse=True)
    return links[:limit]


def extract_text(html: str, url: str) -> PageData:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "header", "footer"]):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title else ""
    description = ""
    desc_tag = soup.find("meta", attrs={"name": "description"})
    if desc_tag and desc_tag.get("content"):
        description = desc_tag["content"].strip()
    h1_tag = soup.find("h1")
    h1 = h1_tag.get_text(" ", strip=True) if h1_tag else ""
    h2 = [tag.get_text(" ", strip=True) for tag in soup.find_all("h2")[:7]]
    body_text = soup.get_text(" ", strip=True)
    body_text = re.sub(r"\s+", " ", body_text)
    body_text = body_text[:4500]
    return PageData(url=url, title=title, description=description, h1=h1, h2=h2, text=body_text)


def _fetch_page_data(url: str) -> Optional[PageData]:
    try:
        html = fetch_html(url)
        page = extract_text(html, url)
        if len(page.text) < 160 and not page.description:
            return None
        return page
    except Exception:
        return None


def collect_site_pages(url: str) -> tuple[PageData, list[PageData]]:
    homepage_html = fetch_html(url)
    primary = extract_text(homepage_html, url)

    candidate_links = extract_internal_links(homepage_html, url, limit=ANALYSIS_MAX_PAGES * 5)
    if not candidate_links:
        return primary, []

    links_to_fetch = candidate_links[: ANALYSIS_MAX_PAGES * 3]
    extra_pages: list[PageData] = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_fetch_page_data, link): link for link in links_to_fetch}
        for future in as_completed(futures):
            result = future.result()
            if result:
                extra_pages.append(result)
            if len(extra_pages) >= ANALYSIS_MAX_PAGES:
                break

    return primary, extra_pages[:ANALYSIS_MAX_PAGES]


def infer_brand_name(page: PageData) -> str:
    candidates: list[str] = []
    if page.title:
        candidates.append(page.title)
    if page.h1:
        candidates.append(page.h1)

    for raw in candidates:
        cleaned = re.sub(r"[|–—-].*$", "", raw).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        words = cleaned.split()
        filtered = [w for w in words if w.lower() not in BAD_BRAND_WORDS]
        brand = " ".join(filtered[:3]).strip()
        if brand and len(brand) >= 2:
            return brand

    parsed = urlparse(page.url)
    return parsed.netloc.replace("www.", "").split(".")[0].capitalize()


def detect_geo_heuristic(text: str, url: str) -> dict:
    text_lower = (text or "").lower()
    for keyword, city_name in CITY_KEYWORDS.items():
        if keyword in text_lower:
            return {"country": "Россия", "city": city_name, "confidence": 0.5}
    if ".ru" in url:
        return {"country": "Россия", "city": "", "confidence": 0.3}
    return {"country": "", "city": "", "confidence": 0.0}


def _norm_match(text: str) -> str:
    value = (text or "").lower()
    value = re.sub(r"[^a-zа-я0-9\s]", " ", value, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip()


def _brand_forms(brand: str) -> list[str]:
    norm = _norm_match(brand)
    if not norm:
        return []
    forms = {norm}
    for token in norm.split():
        if len(token) >= 4 and token not in BRAND_STOP_WORDS:
            forms.add(token)
    return sorted(forms, key=len, reverse=True)


def _query_mentions_brand(query: str, brand: str) -> bool:
    qn = _norm_match(query)
    return any(form and form in qn for form in _brand_forms(brand))


def _count_brand_queries(queries: list[str], brand: str) -> int:
    return sum(1 for q in queries if _query_mentions_brand(q, brand))


def _enforce_brand_limit(queries: list[str], brand: str, max_brand_mentions: int) -> list[str]:
    keep: list[str] = []
    brand_count = 0
    for q in queries:
        if _query_mentions_brand(q, brand):
            if brand_count < max_brand_mentions:
                keep.append(q)
                brand_count += 1
        else:
            keep.append(q)
    out: list[str] = []
    seen: set[str] = set()
    for q in keep:
        key = q.lower().strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(q)
    return out


def sanitize_queries(queries: list[str], brand: str, target: int = 20) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for q in queries:
        q = q.strip()
        if not q or len(q) < 10:
            continue
        if any(phrase in q.lower() for phrase in NOISE_PHRASES):
            continue
        key = q.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(q)
        if len(result) >= target:
            break
    return result


def analyze(url: str) -> dict:
    started = time.perf_counter()

    primary, context_pages = collect_site_pages(url)

    signal_parts = [primary.title, primary.description, primary.h1, " ".join(primary.h2), primary.text]
    for page in context_pages:
        signal_parts.extend([page.title, page.description, page.h1, " ".join(page.h2), page.text])
    signal_text = " ".join(signal_parts)

    base_brand = infer_brand_name(primary)
    geo_fallback = detect_geo_heuristic(signal_text, url)
    signals = {
        "title": primary.title,
        "description": primary.description,
        "h1": primary.h1,
        "h2": primary.h2,
        "context_pages": [{"url": p.url, "title": p.title} for p in context_pages],
    }

    llm = try_llm_analyze(primary, context_pages=context_pages)
    if not llm:
        return {
            "input_url": url,
            "brand": base_brand,
            "theme": ["не определено"],
            "geo": geo_fallback,
            "ai_queries": [],
            "signals": signals,
            "error": "LLM не ответила. Попробуйте позже.",
            "quality": evaluate_query_set([], brand=base_brand, theme=["не определено"]),
        }

    brand = str(llm.get("brand") or base_brand).strip() or base_brand
    theme = [str(t).strip() for t in (llm.get("theme") or []) if str(t).strip()][:2] or ["не определено"]

    llm_geo = llm.get("geo") if isinstance(llm.get("geo"), dict) else {}
    geo = {
        "country": str(llm_geo.get("country") or geo_fallback["country"]),
        "city": str(llm_geo.get("city") or geo_fallback["city"]),
        "confidence": float(llm_geo.get("confidence") or geo_fallback["confidence"]),
    }

    queries = sanitize_queries([str(q) for q in (llm.get("ai_queries") or [])], brand, target=20)

    for _ in range(MAX_EXPAND_ROUNDS):
        if len(queries) >= 20:
            break
        need = 20 - len(queries)
        extra = try_llm_expand_queries(
            data=primary, context_pages=context_pages,
            brand=brand, theme=theme, geo=geo,
            existing=queries, need_count=need,
        )
        queries = sanitize_queries(queries + extra, brand, target=20)

    brand_count = _count_brand_queries(queries, brand)
    if brand_count > MAX_BRAND_MENTIONS:
        queries = sanitize_queries(_enforce_brand_limit(queries, brand, MAX_BRAND_MENTIONS), brand, target=20)

    if ENABLE_REFINE and len(queries) >= 8:
        refined = try_llm_refine_queries(brand=brand, theme=theme, geo=geo, queries=queries, target=20)
        if refined:
            queries = sanitize_queries(refined, brand, target=20)

    query_types = llm.get("query_types", {})

    return {
        "input_url": url,
        "brand": brand,
        "theme": theme,
        "geo": geo,
        "ai_queries": queries,
        "query_types": query_types,
        "signals": signals,
        "quality": evaluate_query_set(queries, brand=brand, theme=theme),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    }