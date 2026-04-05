from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.checks.registry import CHECKS_PIPELINE
from core.checks.types import CheckItem
from core.models import CheckRun, SiteMember, Issue
from notifications.telegram import send_telegram_message


# Анти-спам: по умолчанию шлём только "новые" алерты
# (сравниваем с предыдущим завершённым CheckRun этого сайта)
ALERT_DEDUP_ENABLED = True

def _normalize_domain(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""

    raw2 = raw if "://" in raw else "https://" + raw
    p = urlparse(raw2)
    host = p.hostname or ""
    return host.strip().lower()


def enrich_issue_details(check_key: str, item: dict) -> dict:
    """
    Дополняет details данными для подбора решения из каталога.
    Ничего не удаляет, только добавляет нужные поля.
    """
    details = item.copy() if isinstance(item, dict) else {}

    if check_key == "http":
        http_status = details.get("http_status")
        if http_status in (401, 403, 429):
            details.setdefault("issue_code", "http_access_restricted")
            details.setdefault("http_status", http_status)

    elif check_key == "ssl":
        # Пока для MVP используем общий код решения
        details.setdefault("issue_code", "ssl_expiring_soon")

    elif check_key == "traffic":
        details.setdefault("issue_code", "traffic_drop")
        details.setdefault("metric_name", "seo_visits_week")

    elif check_key == "index":
        details.setdefault("issue_code", "indexed_pages_drop")
        details.setdefault("metric_name", "indexed_pages")

    elif check_key == "excluded":
        details.setdefault("issue_code", "excluded_pages_growth")
        details.setdefault("metric_name", "excluded_pages")

    elif check_key == "sqi":
        details.setdefault("issue_code", "sqi_drop")
        details.setdefault("metric_name", "sqi")

    return details

class Command(BaseCommand):
    help = "Process queued CheckRun and perform MVP checks: HTTP, SSL expiry, domain expiry (optional)"

    # ----------------------------
    # Telegram helpers
    # ----------------------------
    def _get_recipients(self, site) -> List[str]:
        members = SiteMember.objects.filter(site=site, site__is_deleted=False).select_related("user")
        recipients: List[str] = []

        for m in members:
            profile = getattr(m.user, "profile", None)  # related_name='profile'
            if not profile:
                continue
            if not profile.telegram_enabled:
                continue
            if not profile.telegram_chat_id:
                continue
            recipients.append(profile.telegram_chat_id)

        return recipients

    def _send_alert(self, site, check_id: int, title: str, lines: List[str]) -> None:
        recipients = self._get_recipients(site)
        self.stdout.write(
            f"[ALERT] {title} site_id={site.id}, check_id={check_id}. Recipients: {len(recipients)}"
        )

        text = title + "\n" + ("\n".join(lines) if lines else "")

        for chat_id in recipients:
            send_telegram_message(chat_id, text)

    def _get_prev_alert_fingerprints(self, site, current_check_id: int) -> set[str]:
        """
        Берём предыдущий завершённый CheckRun этого сайта и достаём fingerprints уже отправленных алертов.
        Это самый простой MVP-антиспам без новых таблиц/миграций.
        """
        prev = (
            CheckRun.objects
            .filter(site=site, finished_at__isnull=False)
            .exclude(id=current_check_id)
            .order_by("-created_at")
            .first()
        )
        if not prev or not prev.result:
            return set()
        alerts = (prev.result or {}).get("alerts") or {}
        sent = alerts.get("sent_fingerprints") or []
        return set(sent)

    DEDUP_TTL_HOURS = 1  # повторять не чаще 1 раза в час

    def _should_send(self, fingerprint: str, prev_sent: Set[str], site=None) -> bool:
        if not ALERT_DEDUP_ENABLED:
            return True

        if fingerprint not in prev_sent:
            return True

        # если был, но прошло больше TTL — можно повторить
        if site:
            last_check = (
                CheckRun.objects
                .filter(site=site, status__in=[CheckRun.STATUS_OK, CheckRun.STATUS_FAIL])
                .order_by("-finished_at")
                .first()
            )

            if last_check and last_check.finished_at:
                if timezone.now() - last_check.finished_at > timedelta(hours=self.DEDUP_TTL_HOURS):
                    return True

        return False

    def handle(self, *args, **options):
        queued_checks = (
            CheckRun.objects
            .filter(status=CheckRun.STATUS_QUEUED, site__is_deleted=False)
            .select_related("site")
        )

        if not queued_checks.exists():
            self.stdout.write("No queued checks found.")
            return

        self.stdout.write(f"Found {queued_checks.count()} queued checks. Processing...")

        for check in queued_checks:
            site = check.site
            domain = _normalize_domain(site.domain)
            url = f"https://{domain}"

            try:
                # 🚫 Сайт удалён — закрываем проверку без алертов
                if getattr(site, "is_deleted", False):
                    check.status = CheckRun.STATUS_FAIL
                    check.started_at = timezone.now()
                    check.finished_at = timezone.now()
                    check.result = {"skipped": True, "reason": "site_deleted"}
                    check.save(update_fields=["status", "started_at", "finished_at", "result"])
                    self.stdout.write(f"[SKIP] site_id={site.id} deleted → check_id={check.id} closed")
                    continue

                # 1) running
                check.status = CheckRun.STATUS_RUNNING
                check.started_at = timezone.now()
                check.save(update_fields=["status", "started_at"])

                prev_sent = self._get_prev_alert_fingerprints(site, check.id)

                checks: Dict[str, Dict[str, Any]] = {}
                sent_fingerprints: List[str] = []
                overall_fail = False
                overall_warn = False


                checks_pipeline = CHECKS_PIPELINE

                checks: Dict[str, Dict[str, Any]] = {}
                sent_fingerprints: List[str] = []
                overall_fail = False

                dns_ok: Optional[bool] = None  # станет True/False после DnsCheck

                metrics: Dict[str, Any] = {}

                for chk in checks_pipeline:
                    # Особое правило MVP: если DNS не резолвится — SSL не проверяем
                    if chk.name == "ssl" and dns_ok is False:
                        ssl_item = CheckItem(status="skipped", details={"reason": "dns_not_resolved"})
                        checks["ssl"] = {"status": ssl_item.status, **ssl_item.details}
                        continue

                    if chk.name == "http":
                        item = chk.run(url=url)

                    elif chk.name == "dns":
                        ok, err = chk.run(domain=domain)
                        dns_ok = ok
                        checks["dns"] = {
                            "status": "ok" if ok else "fail",
                            "error": err,
                            "summary": {
                                "title": "DNS: резолвится" if ok else "DNS: не резолвится",
                                "lines": [] if ok else [f"Ошибка: {err}"],
                            },
                        }
                        if not ok:
                            overall_fail = True
                        continue

                    elif chk.name == "ssl":
                        item = chk.run(domain=domain)

                    elif chk.name == "domain":
                        item = chk.run(domain=domain)


                    elif chk.name == "metrics":

                        item = chk.run(site=site, check_id=check.id)

                    else:
                        continue

                    sub_checks = (item.details or {}).get("checks")

                    if isinstance(sub_checks, dict) and sub_checks:
                        for k, v in sub_checks.items():
                            checks[k] = v
                            if v.get("status") == "fail":
                                overall_fail = True
                            elif v.get("status") in ["warn", "warning"]:
                                overall_warn = True

                    maybe_metrics = (item.details or {}).get("metrics")
                    if isinstance(maybe_metrics, dict) and maybe_metrics:
                        metrics = maybe_metrics

                    checks[chk.name] = {"status": item.status, **item.details}
                    if item.status == "fail":
                        overall_fail = True
                    elif item.status in ["warn", "warning"]:
                        overall_warn = True

                # 5) Alerts (MVP)
                alert_lines: List[str] = []

                http_status = checks.get("http", {}).get("status")
                dns_status = checks.get("dns", {}).get("status")
                ssl_status = checks.get("ssl", {}).get("status")
                domain_status = checks.get("domain", {}).get("status")

                # Domain alerts
                if domain_status == "fail":
                    fp = "domain:expired"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        alert_lines.append("• Домен истёк")

                if domain_status == "warning":
                    fp = "domain:expiring"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        days = checks.get("domain", {}).get("expires_in_days")
                        alert_lines.append(f"• Домен скоро истекает: {days} дн.")

                # DNS alerts
                if dns_status == "fail":
                    fp = "dns:fail"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        alert_lines.append("• Домен не резолвится (DNS)")

                # HTTP down alert (только если DNS ок)
                if http_status == "fail" and dns_status != "fail":
                    fp = "http:down"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        alert_lines.append("• Сайт недоступен (HTTP)")

                # SSL alerts
                if ssl_status == "fail":
                    fp = "ssl:fail"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        err = checks.get("ssl", {}).get("error")
                        alert_lines.append(f"• SSL ошибка: {err}" if err else "• SSL ошибка")

                if ssl_status == "warning":
                    fp = "ssl:expiring"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        days = checks.get("ssl", {}).get("expires_in_days")
                        alert_lines.append(f"• SSL скоро истекает: {days} дн.")

                # Traffic WoW alerts
                if checks.get("traffic", {}).get("status") == "fail":
                    fp = "traffic:drop"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        d = checks.get("traffic", {})
                        alert_lines.append(
                            f"• Поисковый трафик WoW упал: {d.get('prev')} → {d.get('current')} ({d.get('delta_pct')}%)"
                        )

                # Traffic WoW warnings
                if checks.get("traffic", {}).get("status") == "warn":
                    fp = "traffic:drop:warn"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        d = checks.get("traffic", {})
                        alert_lines.append(
                            f"• Поисковый трафик WoW снизился: {d.get('prev')} → {d.get('current')} ({d.get('delta_pct')}%)"
                        )

                # Indexed pages alerts
                if checks.get("index", {}).get("status") == "fail":
                    fp = "index:drop"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        d = checks.get("index", {})
                        alert_lines.append(
                            f"• Страниц в индексе стало меньше: {d.get('prev')} → {d.get('current')} ({d.get('delta_pct')}%)"
                        )

                # Indexed pages warnings
                if checks.get("index", {}).get("status") == "warn":
                    fp = "index:drop:warn"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        d = checks.get("index", {})
                        alert_lines.append(
                            f"• Страниц в индексе стало меньше (warning): {d.get('prev')} → {d.get('current')} ({d.get('delta_pct')}%)"
                        )

                # Excluded pages alerts
                if checks.get("excluded", {}).get("status") == "fail":
                    fp = "excluded:spike"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        d = checks.get("excluded", {})
                        alert_lines.append(
                            f"• Исключённых страниц стало больше: {d.get('prev')} → {d.get('current')} (+{d.get('delta_pct')}%)"
                        )

                if checks.get("excluded", {}).get("status") == "warn":
                    fp = "excluded:spike:warn"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        d = checks.get("excluded", {})
                        alert_lines.append(
                            f"• Исключённых страниц стало больше (warning): {d.get('prev')} → {d.get('current')} (+{d.get('delta_pct')}%)"
                        )

                # SQI alerts
                if checks.get("sqi", {}).get("status") == "fail":
                    fp = "sqi:drop"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        d = checks.get("sqi", {})
                        alert_lines.append(
                            f"• ИКС упал: {d.get('prev')} → {d.get('current')} ({d.get('delta')})"
                        )

                if checks.get("sqi", {}).get("status") == "warn":
                    fp = "sqi:drop:warn"
                    if self._should_send(fp, prev_sent):
                        sent_fingerprints.append(fp)
                        d = checks.get("sqi", {})
                        alert_lines.append(
                            f"• ИКС снизился (warning): {d.get('prev')} → {d.get('current')} ({d.get('delta')})"
                        )

                # 5.5) Send Telegram alert (если есть что отправлять)
                if sent_fingerprints and alert_lines:
                    status_text = "FAIL" if overall_fail else ("WARNING" if overall_warn else "OK")

                    title = (
                        f"{'🚨' if status_text=='FAIL' else ('⚠️' if status_text=='WARNING' else '✅')} SEOdyssey: {status_text}\n"
                        f"Сайт: {site.name} ({url})\n"
                        f"CheckRun: #{check.id}"
                    )

                    self._send_alert(site, check.id, title, alert_lines)


                if not metrics:
                    metrics = (checks.get("metrics") or {}).get("metrics") or {}


                # 6) Save result
                # --- Summary (global) ---
                def _pick_status(checks_dict):
                    if any((v or {}).get("status") == "fail" for v in checks_dict.values()):
                        return "fail"
                    if any((v or {}).get("status") in ["warn", "warning"] for v in checks_dict.values()):
                        return "warn"
                    return "ok"

                def _badge(s: str) -> str:
                    return {"fail": "FAIL", "warn": "WARNING", "ok": "OK"}.get(s, "OK")

                def _global_summary(checks_dict: dict) -> dict:
                    # приоритет: инфраструктура > SEO-метрики
                    priority = [
                        ("http", "Сайт недоступен"),
                        ("dns", "DNS не резолвится"),
                        ("ssl", "Проблемы с SSL"),
                        ("domain", "Проблемы с доменом"),
                        ("traffic", "Падение поискового трафика"),
                        ("index", "Падение страниц в поиске"),
                        ("excluded", "Рост исключённых страниц"),
                        ("sqi", "Падение ИКС"),
                    ]

                    status = _pick_status(checks_dict)
                    # Если OK — берём аккуратную summary из metrics (без "проверь сервер")
                    if status == "ok":
                        ms = (checks_dict.get("metrics") or {}).get("summary") or {}
                        return {
                            "status": "ok",
                            "badge": _badge("ok"),
                            "title": ms.get("title") or "Всё в норме",
                            "lines": ms.get("lines") or [],
                        }

                    title = "Есть проблемы"
                    lines: List[str] = []

                    # 1) берём первый FAIL/WARN из приоритетного списка
                    for key, human in priority:
                        item = checks_dict.get(key) or {}
                        item_status = item.get("status")
                        if item_status == status or (status == "warn" and item_status == "warning"):
                            # Если чек сам отдаёт summary — используем её
                            s = (item.get("summary") or {}) if isinstance(item, dict) else {}
                            title = s.get("title") or human
                            raw_lines = s.get("lines") or []
                            if isinstance(raw_lines, list):
                                lines.extend([str(x) for x in raw_lines if x])
                            # Если у чека нет summary (на всякий) — fallback по ключу
                            if not lines:
                                if key == "traffic":
                                    lines.append(
                                        f"Поисковый трафик: {item.get('prev')} → {item.get('current')} ({item.get('delta_pct')}%)")
                                elif key == "index":
                                    lines.append(
                                        f"Страницы в поиске: {item.get('prev')} → {item.get('current')} ({item.get('delta_pct')}%)")
                                elif key == "excluded":
                                    dp = item.get("delta_pct")
                                    dp_txt = f"(+{dp}%)" if dp is not None else ""
                                    lines.append(
                                        f"Исключённые страницы: {item.get('prev')} → {item.get('current')} {dp_txt}".strip())
                                elif key == "sqi":
                                    lines.append(
                                        f"ИКС: {item.get('prev')} → {item.get('current')} ({item.get('delta')})")
                            break

                    return {
                        "status": status,
                        "badge": _badge(status),
                        "title": title,
                        "lines": lines[:3],
                    }

                summary = _global_summary(checks)
                check.result = {
                    "url": url,
                    "checks": checks,
                    "metrics": metrics,
                    "summary": summary,
                    "alerts": {
                        "sent_fingerprints": sent_fingerprints,
                        "dedup_enabled": ALERT_DEDUP_ENABLED,
                    },
                }
                check.status = CheckRun.STATUS_FAIL if overall_fail else CheckRun.STATUS_OK
                check.finished_at = timezone.now()
                check.save(update_fields=["result", "status", "finished_at"])
                # --- Issues (MVP) ---
                active_fps = set()

                for key, item in (checks or {}).items():
                    if not isinstance(item, dict):
                        continue

                    st = item.get("status")
                    # фиксируем только проблемы
                    if st not in ("fail", "warn", "warning"):
                        continue

                    # нормализуем warning -> warn
                    sev = "warn" if st in ("warn", "warning") else "fail"

                    # fingerprint пока простой (можно улучшить позже)
                    fp = f"{key}:{sev}"
                    active_fps.add(fp)

                    title = ((item.get("summary") or {}).get("title")) or f"Проблема: {key}"
                    details = enrich_issue_details(key, item)

                    Issue.objects.update_or_create(
                        site=site,
                        fingerprint=fp,
                        defaults={
                            "check_key": key,
                            "severity": sev,
                            "status": Issue.STATUS_OPEN,
                            "title": title,
                            "details": details,
                            "last_checkrun": check,
                            "resolved_at": None,
                        },
                    )

                # закрываем open issues, которых в этом прогоне нет
                open_issues = Issue.objects.filter(site=site, status=Issue.STATUS_OPEN)
                for iss in open_issues:
                    if iss.fingerprint not in active_fps:
                        iss.status = Issue.STATUS_RESOLVED
                        iss.resolved_at = timezone.now()
                        iss.last_checkrun = check
                        iss.save(update_fields=["status", "resolved_at", "last_checkrun", "last_seen_at"])
                # --- /Issues ---
                label = "FAIL" if overall_fail else "OK"
                self.stdout.write(f"[{label}] {site.name} — {url}")
            except Exception as exc:
                # ✅ ключевое: не оставляем running
                check.status = CheckRun.STATUS_FAIL
                check.finished_at = timezone.now()
                check.result = {
                    "crash": True,
                    "error": str(exc),
                    "alerts": {
                        "sent_fingerprints": [],
                        "dedup_enabled": ALERT_DEDUP_ENABLED,
                    },
                }
                check.save(update_fields=["status", "finished_at", "result"])
                self.stderr.write(f"[CRASH] check_id={check.id} error={exc}")
                continue

