import datetime as dt
import requests


def _week_bounds(offset: int = 0) -> tuple[dt.date, dt.date]:
    """
    offset=0  — прошлая завершённая неделя (ПН-ВС)
    offset=-1 — позапрошлая неделя (ПН-ВС)
    """
    today = dt.date.today()
    # пн текущей (ещё не завершённой) недели
    this_monday = today - dt.timedelta(days=today.weekday())
    # пн нужной недели: -1 неделя = прошлая, -2 = позапрошлая
    monday = this_monday + dt.timedelta(weeks=offset - 1)
    sunday = monday + dt.timedelta(days=6)
    return monday, sunday


def get_visits_week(*, access_token: str, counter_id: int, offset: int = 0) -> tuple[int, dt.date, dt.date]:
    """
    Возвращает (визиты, date_from, date_to) за неделю.
    offset=0 — текущая неделя (пн — вчера), offset=-1 — прошлая (пн-вс).
    """
    headers = {"Authorization": f"OAuth {access_token}"}
    date1, date2 = _week_bounds(offset)

    if date2 < date1:
        return 0, date1, date2

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

    totals = data.get("totals") or [0]
    return int(round(totals[0] or 0)), date1, date2


# обратная совместимость
def get_visits_last_7d(*, access_token: str, counter_id: int) -> int:
    visits, _, _ = get_visits_week(access_token=access_token, counter_id=counter_id, offset=0)
    return visits