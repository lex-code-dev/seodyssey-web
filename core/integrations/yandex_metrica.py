import datetime as dt
import requests


def get_visits_last_7d(*, access_token: str, counter_id: int) -> int:
    """
    Возвращает суммарные визиты за 7 полных прошедших дней (до вчера).
    """
    headers = {"Authorization": f"OAuth {access_token}"}

    today = dt.date.today()
    date2 = today - dt.timedelta(days=1)  # вчера — последний полный день
    date1 = date2 - dt.timedelta(days=6)  # 7 полных дней

    params = {
        "id": counter_id,
        "metrics": "ym:s:visits",
        "date1": date1.isoformat(),
        "date2": date2.isoformat(),
        "accuracy": "full",
        "filters": "ym:s:trafficSource=='organic'",
    }

    r = requests.get(
        "https://api-metrika.yandex.net/stat/v1/data",
        headers=headers,
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    # В ответе totals — список, берём первый metric
    totals = data.get("totals") or [0]
    return int(round(totals[0] or 0))