from __future__ import annotations
import requests
from dataclasses import dataclass
from typing import Any, Dict, Optional

from core.checks.types import CheckItem
from core.models import CheckRun, Site
from core.integrations.yandex_metrica import get_visits_last_7d
from core.integrations.yandex_webmaster import get_webmaster_kpis
from core.models import YandexOAuth


class MetricsCheck:
    """
    MVP-метрики: берём manual_traffic_week и manual_indexed_pages из Site
    и сравниваем с предыдущим завершённым CheckRun (WoW).
    """

    name = "metrics"
    TRAFFIC_WARN_PCT = 10  # warning, если падение 10–20%
    INDEX_WARN_PCT = 5  # warning, если падение 5–10%
    EXCLUDED_WARN_PCT = 5  # warning, если рост 5–10%
    SQI_WARN_POINTS = 5  # warning, если падение 5–10 пунктов
    TRAFFIC_DROP_PCT = 20
    INDEX_DROP_PCT = 10

    def _get_prev_metrics(self, site: Site, current_check_id: int) -> Dict[str, Any]:
        prev = (
            CheckRun.objects
            .filter(site=site, finished_at__isnull=False)
            .exclude(id=current_check_id)
            .order_by("-created_at")
            .first()
        )
        if not prev or not prev.result:
            return {}

        return (prev.result or {}).get("metrics") or {}

    def run(self, *, site: Site, check_id: int) -> CheckItem:
        oauth = None
        indexed_pages = None
        excluded_pages = None
        sqi = None
        indexed_source = "manual"
        traffic_week = None

        indexed_pages = None

        if getattr(site, "yandex_webmaster_host_id", None) and oauth:
            try:
                headers = {"Authorization": f"OAuth {oauth.access_token}"}
                r_user = requests.get("https://api.webmaster.yandex.net/v4/user", headers=headers, timeout=20)
                r_user.raise_for_status()
                user_id = r_user.json()["user_id"]


            except Exception:
                indexed_pages = None

        if indexed_pages is None:
            indexed_pages = getattr(site, "manual_indexed_pages", None)

        # если привязан счетчик и есть OAuth — пробуем авто
        member = site.members.select_related("user").first()
        if not member:
            # нет участников — не можем взять токен, уходим в manual
            traffic_week = None
        oauth = None

        if member and member.user_id:
            oauth = YandexOAuth.objects.filter(user=member.user).first()
            if oauth:
                try:
                    traffic_week = get_visits_last_7d(
                        access_token=oauth.access_token,
                        counter_id=site.yandex_metrica_counter_id,
                    )
                except Exception:
                    traffic_week = None

        # --- Webmaster summary KPI ---
        if getattr(site, "yandex_webmaster_host_id", None) and oauth:
            try:
                headers = {"Authorization": f"OAuth {oauth.access_token}"}
                user_id = getattr(oauth, "webmaster_user_id", None)

                if not user_id:
                    r_user = requests.get("https://api.webmaster.yandex.net/v4/user", headers=headers, timeout=20)
                    r_user.raise_for_status()
                    user_id = r_user.json()["user_id"]

                    # кешируем, чтобы больше не дергать /v4/user каждый прогон
                    oauth.webmaster_user_id = user_id
                    oauth.save(update_fields=["webmaster_user_id"])

                kpis = get_webmaster_kpis(
                    access_token=oauth.access_token,
                    user_id=user_id,
                    host_id=site.yandex_webmaster_host_id,
                )

                indexed_pages = kpis.get("indexed_pages")
                excluded_pages = kpis.get("excluded_pages")
                sqi = kpis.get("sqi")

                if indexed_pages is not None or excluded_pages is not None or sqi is not None:
                    indexed_source = kpis.get("source") or "webmaster:summary"
            except Exception:
                pass

        # fallback (пока только indexed_pages у тебя manual)
        if indexed_pages is None:
            indexed_pages = getattr(site, "manual_indexed_pages", None)

        # fallback на manual
        if traffic_week is None:
            traffic_week = getattr(site, "manual_traffic_week", None)

        metrics = {
            "seo_visits_week": traffic_week,
            "indexed_pages": indexed_pages,
            "excluded_pages": excluded_pages,
            "sqi": sqi,
            "traffic_source": "metrika:trafficSource=organic",
            "traffic_window": "last_7_full_days",
            "indexed_source": indexed_source,
        }

        prev_metrics = self._get_prev_metrics(site, check_id)
        prev_traffic = prev_metrics.get("seo_visits_week")
        if prev_traffic is None:
            prev_traffic = prev_metrics.get("traffic_week")
        prev_indexed = prev_metrics.get("indexed_pages")


        checks: Dict[str, Dict[str, Any]] = {}

        # --- traffic WoW ---
        cur_traffic = metrics.get("seo_visits_week")
        if cur_traffic is None:
            checks["traffic"] = {
                "status": "skipped",
                "reason": "seo_visits_week_not_available",
                "metric": "seo_visits_week",
                "label": "Поисковый трафик (визиты) WoW",
            }
        elif prev_traffic is None:
            checks["traffic"] = {
                "status": "ok",
                "note": "no_prev_value",
                "current": cur_traffic,
                "metric": "seo_visits_week",
                "label": "Поисковый трафик (визиты) WoW",
            }
        elif prev_traffic == 0:
            checks["traffic"] = {
                "status": "ok",
                "prev": prev_traffic,
                "current": cur_traffic,
                "note": "prev_zero",
                "metric": "seo_visits_week",
                "label": "Поисковый трафик (визиты) WoW",
            }
        else:
            delta_pct = ((cur_traffic - prev_traffic) / prev_traffic) * 100.0
            status = "ok"
            if delta_pct <= -self.TRAFFIC_DROP_PCT:
                status = "fail"
            elif delta_pct <= -self.TRAFFIC_WARN_PCT:
                status = "warn"
            checks["traffic"] = {
                "status": status,
                "prev": prev_traffic,
                "current": cur_traffic,
                "delta_pct": round(delta_pct, 2),
                "threshold_pct": self.TRAFFIC_DROP_PCT,
                "metric": "seo_visits_week",
                "label": "Поисковый трафик (визиты) WoW",
            }

        # --- indexed pages WoW ---
        cur_indexed = metrics.get("indexed_pages")
        if cur_indexed is None:
            checks["index"] = {"status": "skipped", "reason": "manual_indexed_pages_not_set"}
        elif prev_indexed is None:
            checks["index"] = {"status": "ok", "note": "no_prev_value", "current": cur_indexed}
        elif prev_indexed == 0:
            checks["index"] = {"status": "ok", "prev": prev_indexed, "current": cur_indexed, "note": "prev_zero"}
        else:
            delta_pct = ((cur_indexed - prev_indexed) / prev_indexed) * 100.0
            status = "ok"
            if delta_pct <= -self.INDEX_DROP_PCT:
                status = "fail"
            elif delta_pct <= -self.INDEX_WARN_PCT:
                status = "warn"
            checks["index"] = {
                "status": status,
                "prev": prev_indexed,
                "current": cur_indexed,
                "delta_pct": round(delta_pct, 2),
                "threshold_pct": self.INDEX_DROP_PCT,
            }

        # --- excluded pages WoW (рост > 10%) ---
        prev_excluded = prev_metrics.get("excluded_pages")
        cur_excluded = metrics.get("excluded_pages")

        if cur_excluded is None:
            checks["excluded"] = {"status": "skipped", "reason": "excluded_pages_not_available"}
        elif prev_excluded is None:
            checks["excluded"] = {"status": "ok", "note": "no_prev_value", "current": cur_excluded}
        elif prev_excluded == 0:
            checks["excluded"] = {
                "status": "fail" if cur_excluded > 0 else "ok",
                "prev": prev_excluded,
                "current": cur_excluded,
                "note": "prev_zero",
                "metric": "excluded_pages",
                "label": "Исключённые страницы WoW",
                "threshold_pct": 10,
            }
        else:
            delta_pct = ((cur_excluded - prev_excluded) / prev_excluded) * 100.0
            status = "ok"
            if delta_pct > 10:
                status = "fail"
            elif delta_pct >= self.EXCLUDED_WARN_PCT:
                status = "warn"
            checks["excluded"] = {
                "status": status,
                "prev": prev_excluded,
                "current": cur_excluded,
                "delta_pct": round(delta_pct, 2),
                "metric": "excluded_pages",
                "label": "Исключённые страницы WoW",
                "threshold_pct": 10,
                "direction": "increase",
            }

        # --- SQI WoW (падение на 10+ пунктов) ---
        prev_sqi = prev_metrics.get("sqi")
        cur_sqi = metrics.get("sqi")

        if cur_sqi is None:
            checks["sqi"] = {"status": "skipped", "reason": "sqi_not_available"}
        elif prev_sqi is None:
            checks["sqi"] = {"status": "ok", "note": "no_prev_value", "current": cur_sqi}
        else:
            delta = cur_sqi - prev_sqi
            status = "ok"
            if delta <= -10:
                status = "fail"
            elif delta <= -self.SQI_WARN_POINTS:
                status = "warn"
            checks["sqi"] = {
                "status": status,
                "prev": prev_sqi,
                "current": cur_sqi,
                "delta": delta,
                "metric": "sqi",
                "label": "ИКС WoW",
                "threshold_points": 10,
            }

        # общий статус чека
        any_fail = any(v.get("status") == "fail" for v in checks.values())
        any_warn = any(v.get("status") == "warn" for v in checks.values())
        all_skipped = all(v.get("status") == "skipped" for v in checks.values()) if checks else True

        if any_fail:
            status = "fail"
        elif all_skipped:
            status = "skipped"
        elif any_warn:
            status = "warn"
        else:
            status = "ok"

        # --- Summary (MVP) ---
        def _badge(s: str) -> str:
            if s == "fail":
                return "FAIL"
            if s == "warn":
                return "WARNING"
            if s == "ok":
                return "OK"
            return "SKIP"

        summary_lines = []

        t = checks.get("traffic") or {}
        if t.get("status") in ("fail", "warn"):
            summary_lines.append(f"Поисковый трафик: {t.get('prev')} → {t.get('current')} ({t.get('delta_pct')}%)")

        i = checks.get("index") or {}
        if i.get("status") in ("fail", "warn"):
            summary_lines.append(f"Страницы в поиске: {i.get('prev')} → {i.get('current')} ({i.get('delta_pct')}%)")

        ex = checks.get("excluded") or {}
        if ex.get("status") in ("fail", "warn"):
            # delta_pct может быть None в некоторых ветках
            dp = ex.get("delta_pct")
            dp_txt = f"(+{dp}%)" if dp is not None else ""
            summary_lines.append(f"Исключённые страницы: {ex.get('prev')} → {ex.get('current')} {dp_txt}".strip())

        sq = checks.get("sqi") or {}
        if sq.get("status") in ("fail", "warn"):
            summary_lines.append(f"ИКС: {sq.get('prev')} → {sq.get('current')} ({sq.get('delta')})")

        # Заголовок: одна фраза
        if status == "fail":
            summary_title = "Критичные изменения в SEO-метриках"
        elif status == "warn":
            summary_title = "Есть изменения в SEO-метриках"
        elif status == "ok":
            summary_title = "SEO-метрики в норме"
        else:
            summary_title = "SEO-метрики: нет данных"

        if status == "ok":
            summary_lines = [
                f"SEO визиты (7д): {metrics.get('seo_visits_week') or '—'} • "
                f"в поиске: {metrics.get('indexed_pages') or '—'} • "
                f"исключено: {metrics.get('excluded_pages') or '—'} • "
                f"ИКС: {metrics.get('sqi') or '—'}"
            ]

        metrics_summary = {
            "status": status,  # ok/warn/fail/skipped
            "badge": _badge(status),  # OK/WARNING/FAIL/SKIP
            "title": summary_title,
            "lines": summary_lines[:3],  # не больше 3 строк, чтобы не спамить
        }

        return CheckItem(
            status=status,
            details={
                "metrics": metrics,
                "summary": metrics_summary,
                # отдаём под-проверки отдельно, чтобы run_checks мог их “распаковать” в общий checks
                "checks": checks,
            },
        )