from __future__ import annotations

from typing import Any, Dict, List

from core.checks.types import CheckItem
from core.integrations.yandex_webmaster import get_host_diagnostics
from core.models import Site, YandexOAuth
from core.services.webmaster_mapper import map_webmaster_issue


class WebmasterDiagnosticsCheck:
    name = "webmaster"

    def run(self, *, site: Site, check_id: int) -> CheckItem:
        member = site.members.select_related("user").first()
        if not member or not member.user_id:
            return CheckItem(
                status="skipped",
                details={
                    "reason": "site_member_not_found",
                    "summary": {
                        "title": "Яндекс.Вебмастер: проверка пропущена",
                        "lines": ["Не найден участник сайта для получения OAuth."],
                    },
                    "checks": {},
                },
            )

        oauth = YandexOAuth.objects.filter(user=member.user).first()
        if not oauth:
            return CheckItem(
                status="skipped",
                details={
                    "reason": "oauth_not_found",
                    "summary": {
                        "title": "Яндекс.Вебмастер: проверка пропущена",
                        "lines": ["Нет подключённого OAuth Яндекса."],
                    },
                    "checks": {},
                },
            )

        host_id = getattr(site, "yandex_webmaster_host_id", None)
        if not host_id:
            return CheckItem(
                status="skipped",
                details={
                    "reason": "webmaster_host_id_not_set",
                    "summary": {
                        "title": "Яндекс.Вебмастер: проверка пропущена",
                        "lines": ["Для сайта не сохранён host_id Яндекс.Вебмастера."],
                    },
                    "checks": {},
                },
            )

        user_id = getattr(oauth, "webmaster_user_id", None)
        if not user_id:
            return CheckItem(
                status="skipped",
                details={
                    "reason": "webmaster_user_id_not_set",
                    "summary": {
                        "title": "Яндекс.Вебмастер: проверка пропущена",
                        "lines": ["Не найден webmaster_user_id в OAuth."],
                    },
                    "checks": {},
                },
            )

        try:
            raw_problems = get_host_diagnostics(
                access_token=oauth.access_token,
                user_id=user_id,
                host_id=host_id,
            )
        except Exception as exc:
            return CheckItem(
                status="warning",
                details={
                    "error": str(exc),
                    "summary": {
                        "title": "Яндекс.Вебмастер: не удалось получить диагностику",
                        "lines": [f"Ошибка API: {exc}"],
                    },
                    "checks": {},
                },
            )

        present_problems = [
            problem for problem in raw_problems
            if str(problem.get("state", "")).upper() == "PRESENT"
        ]

        mapped: List[Dict[str, Any]] = [map_webmaster_issue(problem) for problem in present_problems]

        checks: Dict[str, Dict[str, Any]] = {}
        summary_lines: List[str] = []

        any_fail = False
        any_warn = False

        for item in mapped:
            issue_code = item["issue_code"]
            severity = item["severity"]
            title = item["title"]
            details = item["details"]

            status = "fail" if severity == "fail" else "warn"
            checks[issue_code] = {
                "status": status,
                "issue_code": issue_code,
                "source": "yandex_webmaster",
                "external_code": details.get("external_code"),
                "external_title": details.get("external_title"),
                "external_description": details.get("external_description"),
                "external_severity": details.get("external_severity"),
                "external_state": details.get("external_state"),
                "summary": {
                    "title": title,
                    "lines": [
                        details.get("external_title") or issue_code,
                    ],
                },
                **details,
            }

            if status == "fail":
                any_fail = True
            elif status == "warn":
                any_warn = True

            if len(summary_lines) < 3:
                summary_lines.append(title)

        if any_fail:
            status = "fail"
            summary_title = "Есть критичные проблемы из Яндекс.Вебмастера"
        elif any_warn:
            status = "warn"
            summary_title = "Есть предупреждения из Яндекс.Вебмастера"
        else:
            status = "ok"
            summary_title = "Проблем из Яндекс.Вебмастера не найдено"
            summary_lines = ["Активных проблем со state=PRESENT не найдено."]

        return CheckItem(
            status=status,
            details={
                "count_present": len(present_problems),
                "summary": {
                    "status": status,
                    "badge": "FAIL" if status == "fail" else "WARNING" if status == "warn" else "OK",
                    "title": summary_title,
                    "lines": summary_lines,
                },
                "checks": checks,
                "raw_present_problems": present_problems,
            },
        )