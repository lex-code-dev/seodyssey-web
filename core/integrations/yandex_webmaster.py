import requests


def get_host_summary(*, access_token: str, user_id: int, host_id: str) -> dict:
    """
    /summary даёт нам нужные KPI:
    - searchable_pages_count (страницы, доступные для поиска)
    - excluded_pages_count (исключённые)
    - sqi (ИКС)
    """
    headers = {"Authorization": f"OAuth {access_token}"}
    url = f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts/{host_id}/summary"
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def get_webmaster_kpis(*, access_token: str, user_id: int, host_id: str) -> dict:
    """
    Возвращает kpi из /summary:
      - indexed_pages (searchable_pages_count)
      - excluded_pages (excluded_pages_count)
      - sqi
    """
    summary = get_host_summary(access_token=access_token, user_id=user_id, host_id=host_id)

    def _to_int(x):
        try:
            return int(x) if x is not None else None
        except Exception:
            return None

    return {
        "indexed_pages": _to_int(summary.get("searchable_pages_count")),
        "excluded_pages": _to_int(summary.get("excluded_pages_count")),
        "sqi": _to_int(summary.get("sqi")),
        "source": "webmaster:summary",
    }