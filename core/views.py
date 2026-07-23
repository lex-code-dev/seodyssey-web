from __future__ import annotations
import json
import requests
import secrets
from django.http import JsonResponse, HttpResponse
from urllib.parse import urlencode
from django.conf import settings
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import User
from core.models import YandexOAuth, Issue
from datetime import timedelta, date
from django.contrib import messages
from django.core.cache import cache
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Count, Max
from django.http import HttpResponseBadRequest, HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from .forms import SignUpForm
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from core.forms import AddSiteForm
from billing.services import get_active_plan, can_add_integration
from core.forms_profile import UserProfileForm
from core.models import Site, SiteMember, CheckRun, UserProfile, IssueSolution, UserActivity
import logging
logger = logging.getLogger(__name__)
import socket
_orig_getaddrinfo = socket.getaddrinfo
def _ipv4_getaddrinfo(host, port, family=0, *args, **kwargs):
    return _orig_getaddrinfo(host, port, socket.AF_INET, *args, **kwargs)
socket.getaddrinfo = _ipv4_getaddrinfo


def _require_site_access(user, site: Site) -> bool:
    """Проверяем, что пользователь имеет доступ к сайту."""
    return SiteMember.objects.filter(site=site, user=user).exists()


def _get_profile(user) -> UserProfile:
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def _integration_flags(profile: UserProfile) -> dict:
    telegram_configured = bool((profile.telegram_chat_id or "").strip())
    telegram_ready = bool(profile.telegram_enabled and telegram_configured)
    yandex_ready = bool(profile.yandex_connected)
    return {
        "telegram_configured": telegram_configured,
        "telegram_ready": telegram_ready,
        "yandex_ready": yandex_ready,
    }


@login_required
def dashboard(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    # сайты, к которым у юзера есть доступ
    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    sites_qs = Site.objects.filter(id__in=site_ids, is_deleted=False).order_by("-created_at")

    now = timezone.now()
    last_check_at = CheckRun.objects.filter(site_id__in=site_ids).aggregate(m=Max("created_at"))["m"]
    alerts_7d = CheckRun.objects.filter(
        site_id__in=site_ids, status=CheckRun.STATUS_FAIL, created_at__gte=now - timedelta(days=7)
    ).count()
    queued_count = CheckRun.objects.filter(
        site_id__in=site_ids, status__in=[CheckRun.STATUS_QUEUED, CheckRun.STATUS_RUNNING]
    ).count()

    recent_alerts = (
        CheckRun.objects.select_related("site")
        .filter(site_id__in=site_ids, status=CheckRun.STATUS_FAIL)
        .order_by("-created_at")[:5]
    )

    # “превью” сайтов
    sites_preview = list(sites_qs[:5])

    # сколько сайтов с последним статусом fail
    last_by_site = (
        CheckRun.objects.filter(site_id__in=site_ids)
        .values("site_id")
        .annotate(last_status=Max("status"))  # not perfect, but ok for MVP
    )
    # более корректно: считаем сайты, у которых есть хотя бы один FAIL за 7 дней
    fail_sites_count = CheckRun.objects.filter(
        site_id__in=site_ids, status=CheckRun.STATUS_FAIL, created_at__gte=now - timedelta(days=7)
    ).values("site_id").distinct().count()

    
    # таблица сайтов (как в Вебмастере)
    # источник данных: последний завершённый CheckRun + активные Issues (open)
    open_issues = Issue.objects.filter(site_id__in=site_ids, status=Issue.STATUS_OPEN)
    issue_map = {sid: {"fail": 0, "warn": 0} for sid in site_ids}
    for row in open_issues.values("site_id", "severity").annotate(c=Count("id")):
        sev = row["severity"]
        if sev not in ("fail", "warn"):
            continue
        issue_map.setdefault(row["site_id"], {"fail": 0, "warn": 0})[sev] = row["c"]

    sites_table = []
    for s in sites_qs:
        sev = "ok"
        if issue_map.get(s.id, {}).get("fail", 0) > 0:
            sev = "fail"
        elif issue_map.get(s.id, {}).get("warn", 0) > 0:
            sev = "warn"

        last_run = (
            CheckRun.objects.filter(site=s)
            .exclude(status__in=[CheckRun.STATUS_QUEUED, CheckRun.STATUS_RUNNING])
            .order_by("-created_at")
            .first()
        )
        metrics = (last_run.result or {}).get("metrics", {}) if last_run and last_run.result else {}
        checks = (last_run.result or {}).get("checks", {}) if last_run and last_run.result else {}

        # traffic/index delta_pct уже считаются в MetricsCheck
        traffic_chk = checks.get("traffic") or {}
        index_chk = checks.get("index") or {}
        sqi_chk = checks.get("sqi") or {}

        # SQI delta_pct считаем сами (там delta points)
        sqi_prev = sqi_chk.get("prev")
        sqi_delta = sqi_chk.get("delta")
        sqi_delta_pct = None
        try:
            if sqi_prev not in (None, 0) and sqi_delta is not None:
                sqi_delta_pct = round((float(sqi_delta) / float(sqi_prev)) * 100.0, 2)
        except Exception:
            sqi_delta_pct = None

        sites_table.append(
            {
                "site": s,
                "state": sev,
                "last_checkrun_id": last_run.id if last_run else None,
                "traffic": {
                    "value": metrics.get("seo_visits_week"),
                    "delta_pct": traffic_chk.get("delta_pct"),
                },
                "indexed": {
                    "value": metrics.get("indexed_pages"),
                    "delta_pct": index_chk.get("delta_pct"),
                },
                "sqi": {
                    "value": metrics.get("sqi"),
                    "delta_pct": sqi_delta_pct,
                },
            }
        )

    ctx = {
        "today": now,
        "last_check_at": last_check_at,
        "sites_count": sites_qs.count(),
        "active_sites_count": sites_qs.filter(is_active=True).count(),
        "fail_sites_count": fail_sites_count,
        "alerts_7d": alerts_7d,
        "queued_count": queued_count,
        "recent_alerts": recent_alerts,
        "sites_preview": sites_preview,
        "sites_table": sites_table,
        **flags,
    }
    return render(request, "core/dashboard.html", ctx)


@login_required
def sites(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    q = (request.GET.get("q") or "").strip()
    status = (request.GET.get("status") or "").strip()

    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    sites_qs = Site.objects.filter(id__in=site_ids, is_deleted=False)

    if q:
        sites_qs = sites_qs.filter(Q(name__icontains=q) | Q(domain__icontains=q))

    # подмешиваем last_check_at, last_status, fail_7d
    now = timezone.now()
    checks = CheckRun.objects.filter(site_id__in=site_ids)

    last_check_map = dict(
        checks.values("site_id").annotate(last=Max("created_at")).values_list("site_id", "last")
    )
    # last status: берём статус последней проверки по created_at
    last_status_map = {}
    for c in checks.order_by("site_id", "-created_at").values("site_id", "status"):
        sid = c["site_id"]
        if sid not in last_status_map:
            last_status_map[sid] = c["status"]

    fail_7d_map = dict(
        checks.filter(status=CheckRun.STATUS_FAIL, created_at__gte=now - timedelta(days=7))
        .values("site_id")
        .annotate(cnt=Count("id"))
        .values_list("site_id", "cnt")
    )

    rows = []
    for s in sites_qs.order_by("-created_at"):
        rows.append(
            {
                "site": s,
                "last_check_at": last_check_map.get(s.id),
                "last_status": last_status_map.get(s.id),
                "fail_7d": fail_7d_map.get(s.id, 0),
            }
        )

    if status:
        if status == "fail":
            rows = [r for r in rows if r["last_status"] == CheckRun.STATUS_FAIL]
        elif status == "ok":
            rows = [r for r in rows if r["last_status"] == CheckRun.STATUS_OK]

    return render(
        request,
        "core/sites.html",
        {
            "sites": rows,
            "q": q,
            "status": status,
            **flags,
        },
    )

def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")
    if request.method == "POST":
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            try:
                from notifications.email import send_welcome_email, notify_owner_new_user
                send_welcome_email(user)
                notify_owner_new_user(user)
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Не отправились письма о регистрации user_id=%s", user.id)
            login(request, user)
            return redirect("dashboard")
    else:
        form = SignUpForm()
    return render(request, "registration/register.html", {"form": form})

@login_required
def site_new(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    if request.method == "POST":
        form = AddSiteForm(request.POST)
        if form.is_valid():
            domain = form.cleaned_data["domain"]

            # Есть ли у этого пользователя уже активный сайт с таким доменом
            existing_for_user = Site.objects.filter(
                members__user=request.user,
                domain=domain,
                is_deleted=False,
            ).first()
            # Лимит сайтов из активного тарифа пользователя
            plan = get_active_plan(request.user)
            max_projects = plan.max_projects if plan else 1
            sites_count = SiteMember.objects.filter(
                user=request.user,
                site__is_deleted=False
            ).count()
            if sites_count >= max_projects:
                messages.error(
                    request,
                    f"Достигнут лимит тарифа: {max_projects} "
                    f"{'сайт' if max_projects == 1 else 'сайтов'}. "
                    f"Перейдите на тариф выше, чтобы добавить больше."
                )
                return redirect("site_new")
            if existing_for_user:
                form.add_error("domain", "Ты уже добавил этот домен.")
                return render(request, "core/site_new.html", {"form": form, **flags})

            # Есть ли у этого пользователя удалённый сайт с таким доменом
            restored_site = Site.objects.filter(
                members__user=request.user,
                domain=domain,
                is_deleted=True,
            ).first()
            if restored_site:
                restored_site.restore()
                SiteMember.objects.get_or_create(
                    user=request.user, site=restored_site, defaults={"role": SiteMember.ROLE_OWNER}
                )
                messages.success(request, "Сайт был ранее удалён и восстановлен")
                return redirect("site_checks", site_id=restored_site.id)

            with transaction.atomic():
                site = form.save()
                SiteMember.objects.create(user=request.user, site=site, role=SiteMember.ROLE_OWNER)

            counter_id = request.POST.get("counter_id")
            host_id = request.POST.get("host_id")
            if counter_id:
                allowed, limit = can_add_integration(
                    request.user, site, "yandex_metrica_counter_id"
                )
                if allowed:
                    site.yandex_metrica_counter_id = int(counter_id)
                    site.save(update_fields=["yandex_metrica_counter_id"])
                else:
                    messages.warning(
                        request,
                        f"Метрика не подключена: достигнут лимит интеграций ({limit}) для сайта."
                    )
            if host_id:
                allowed, limit = can_add_integration(
                    request.user, site, "yandex_webmaster_host_id"
                )
                if allowed:
                    site.yandex_webmaster_host_id = host_id
                    site.save(update_fields=["yandex_webmaster_host_id"])
                else:
                    messages.warning(
                        request,
                        f"Вебмастер не подключён: достигнут лимит интеграций ({limit}) для сайта."
                    )

            telegram_chat_id = request.POST.get("telegram_chat_id", "").strip()
            if telegram_chat_id:
                profile.telegram_chat_id = telegram_chat_id
                profile.telegram_enabled = True
                profile.save(update_fields=["telegram_chat_id", "telegram_enabled"])

            messages.success(request, "Сайт добавлен ✅")
            return redirect("site_checks", site_id=site.id)
        # форма невалидна — падаем вниз, form уже содержит ошибки
    else:
        initial = {
            "domain": request.GET.get("domain", ""),
            "name": request.GET.get("name", ""),
        }
        form = AddSiteForm(initial=initial)

    counters = []
    hosts = []

    show_onboarding = not profile.onboarding_done
    return render(request, "core/site_new.html",
                  {"form": form, "counters": counters, "hosts": hosts, "show_onboarding": show_onboarding, **flags})

@login_required
def site_edit(request, site_id: int):
    site = get_object_or_404(Site, id=site_id, is_deleted=False)
    if not _require_site_access(request.user, site):
        return HttpResponseForbidden("Нет доступа к сайту")

    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    if request.method == "POST":
        counter_id = request.POST.get("counter_id")
        host_id = request.POST.get("host_id")
        if counter_id:
            allowed, limit = can_add_integration(
                request.user, site, "yandex_metrica_counter_id"
            )
            if allowed:
                site.yandex_metrica_counter_id = int(counter_id)
                site.save(update_fields=["yandex_metrica_counter_id"])
            else:
                messages.warning(
                    request,
                    f"Метрика не подключена: достигнут лимит интеграций ({limit}) для сайта."
                )
        if host_id:
            allowed, limit = can_add_integration(
                request.user, site, "yandex_webmaster_host_id"
            )
            if allowed:
                site.yandex_webmaster_host_id = host_id
                site.save(update_fields=["yandex_webmaster_host_id"])
            else:
                messages.warning(
                    request,
                    f"Вебмастер не подключён: достигнут лимит интеграций ({limit}) для сайта."
                )
        messages.success(request, "Настройки сайта сохранены ✅")
        return redirect("site_checks", site_id=site.id)

    return render(request, "core/site_edit.html", {"site": site, **flags})


@login_required
@require_POST
def run_check(request, site_id: int):
    site = get_object_or_404(Site, id=site_id, is_deleted=False)

    if not _require_site_access(request.user, site):
        return HttpResponseForbidden("Нет доступа к сайту")

    already = CheckRun.objects.filter(
        site=site, status__in=[CheckRun.STATUS_QUEUED, CheckRun.STATUS_RUNNING]
    ).exists()

    if already:
        messages.info(request, "Проверка уже в очереди или выполняется. Новую не ставим 🙂")
        return redirect("site_checks", site_id=site.id)

    CheckRun.objects.create(site=site, created_by=request.user, status=CheckRun.STATUS_QUEUED)
    messages.success(request, "Проверка добавлена в очередь.")

    from core.tasks import run_queued_checks
    run_queued_checks.delay()

    return redirect("site_checks", site_id=site.id)

@login_required
def site_new_integrations(request):
    profile = _get_profile(request.user)
    if not profile.yandex_connected:
        return JsonResponse({"counters": [], "hosts": []})

    oauth = getattr(request.user, "yandex_oauth", None)
    if not oauth:
        return JsonResponse({"counters": [], "hosts": []})

    cache_key_counters = f"yandex_counters_{request.user.id}"
    cache_key_hosts = f"yandex_hosts_{request.user.id}"

    counters = cache.get(cache_key_counters)
    hosts = cache.get(cache_key_hosts)

    headers = {"Authorization": f"OAuth {oauth.access_token}"}

    if counters is None:
        try:
            r = requests.get("https://api-metrika.yandex.net/management/v1/counters", headers=headers, timeout=35)
            counters = r.json().get("counters", [])
            cache.set(cache_key_counters, counters, 3600)
        except Exception:
            counters = []

    if hosts is None:
        try:
            r_user = requests.get("https://api.webmaster.yandex.net/v4/user", headers=headers, timeout=35)
            user_id = r_user.json().get("user_id")
            r_hosts = requests.get(f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts", headers=headers, timeout=35)
            hosts = r_hosts.json().get("hosts", [])
            cache.set(cache_key_hosts, hosts, 3600)
        except Exception:
            hosts = []

    return JsonResponse({"counters": counters, "hosts": hosts})

@login_required
def site_checks(request, site_id: int):
    site = get_object_or_404(Site, id=site_id, is_deleted=False)

    if not _require_site_access(request.user, site):
        return HttpResponseForbidden("Нет доступа к сайту")

    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    checks = site.checks.all()[:20]
    has_active_check = site.checks.filter(status__in=[CheckRun.STATUS_QUEUED, CheckRun.STATUS_RUNNING]).exists()

    last = site.checks.first()
    site_last_status = last.status if last else None
    last_result = (last.result or {}) if last else {}
    summary = last_result.get("summary") or {}

    summary = (last.result or {}).get("summary") if last else {}

    last_result = (last.result or {}) if last else {}
    last_metrics = last_result.get("metrics") or {}
    last_checks = last_result.get("checks") or {}

    traffic_check = last_checks.get("traffic") or {}
    index_check = last_checks.get("index") or {}

    excluded_check = last_checks.get("excluded") or {}
    sqi_check = last_checks.get("sqi") or {}

    metrics_available = bool(last_metrics) or bool(traffic_check) or bool(index_check)

    # баннер восстановления — показываем если сайт был восстановлен недавно
    restored_banner = False
    # если когда-то был deleted_at, а сейчас не удалён — считать восстановленным
    # в модели deleted_at очищается при restore(), поэтому просто оставим False. (Можно хранить audits later)

    last_check_at = last.created_at if last else None

    open_issues_qs = Issue.objects.filter(site=site, status=Issue.STATUS_OPEN)

    # Загружаем приоритеты из IssueSolution
    issue_codes = [
        (i.details or {}).get("issue_code") for i in open_issues_qs
    ]
    priorities = {
        s.issue_code: s.priority
        for s in IssueSolution.objects.filter(issue_code__in=issue_codes)
    }

    open_issues = sorted(
        open_issues_qs,
        key=lambda i: (
            priorities.get((i.details or {}).get("issue_code"), 999),
            0 if i.severity == "fail" else 1,
        )
    )

    return render(
        request,
        "core/site_checks.html",
        {
            "site": site,
            "checks": checks,
            "excluded_check": excluded_check,
            "open_issues": open_issues,
            "sqi_check": sqi_check,
            "last_check_id": last.id if last else None,
            "has_active_check": has_active_check,
            "site_last_status": site_last_status,
            "last_check_at": last_check_at,
            "restored_banner": restored_banner,
            "metrics_available": metrics_available,
            "last_metrics": last_metrics,
            "traffic_check": traffic_check,
            "index_check": index_check,
            "summary": summary,
            **flags,
        },
    )


@login_required
def check_detail(request, check_id: int):
    check = get_object_or_404(CheckRun.objects.select_related("site"), id=check_id)
    if not _require_site_access(request.user, check.site):
        return HttpResponseForbidden("Нет доступа к сайту")

    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    if check.result is None:
        result_pretty = "{}"

    else:
        try:
            result_pretty = json.dumps(check.result, ensure_ascii=False, indent=2)
        except TypeError:
            result_pretty = str(check.result)

    return render(
        request,
        "core/check_detail.html",
        {"check": check, "result_pretty": result_pretty, **flags},
    )


@login_required
@require_POST
def delete_site(request, site_id: int):
    membership = get_object_or_404(
        SiteMember, user=request.user, site_id=site_id, role=SiteMember.ROLE_OWNER
    )
    membership.site.soft_delete()
    messages.success(request, "Сайт удалён.")
    return redirect("dashboard")


@login_required
def alerts(request):
    # Страница алертов ещё не доведена до брендового вида и не в меню —
    # скрыта до готовности, чтобы не показывать недоделанный экран.
    return redirect("dashboard")


@login_required
def alert_detail(request, check_id: int):
    check = get_object_or_404(CheckRun.objects.select_related("site"), id=check_id)
    if not _require_site_access(request.user, check.site):
        return HttpResponseForbidden("Нет доступа к сайту")

    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    return render(request, "core/alert_detail.html", {"check": check, **flags})


@login_required
def reports(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    qs = CheckRun.objects.filter(site_id__in=site_ids)

    # агрегируем по месяцам (последние 12)
    months = {}
    for c in qs.only("created_at", "status").order_by("-created_at")[:2000]:
        y = c.created_at.year
        m = c.created_at.month
        key = (y, m)
        if key not in months:
            months[key] = {"year": y, "month": m, "checks": 0, "fails": 0}
        months[key]["checks"] += 1
        if c.status == CheckRun.STATUS_FAIL:
            months[key]["fails"] += 1

    # сортировка desc
    items = sorted(months.values(), key=lambda x: (x["year"], x["month"]), reverse=True)[:12]
    # заголовки
    import calendar
    for it in items:
        it["title"] = f"{calendar.month_name[it['month']].capitalize()} {it['year']}"

    return render(request, "core/reports.html", {"months": items, **flags})


@login_required
def report_detail(request, year: int, month: int):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    # range for month
    start = timezone.datetime(year=year, month=month, day=1, tzinfo=timezone.get_current_timezone())
    if month == 12:
        end = timezone.datetime(year=year + 1, month=1, day=1, tzinfo=timezone.get_current_timezone())
    else:
        end = timezone.datetime(year=year, month=month + 1, day=1, tzinfo=timezone.get_current_timezone())

    checks = CheckRun.objects.select_related("site").filter(site_id__in=site_ids, created_at__gte=start, created_at__lt=end)
    checks_total = checks.count()
    fails_total = checks.filter(status=CheckRun.STATUS_FAIL).count()
    oks_total = checks.filter(status=CheckRun.STATUS_OK).count()

    per_site_map = {}
    for c in checks:
        sid = c.site_id
        if sid not in per_site_map:
            per_site_map[sid] = {"site": c.site, "checks": 0, "fails": 0}
        per_site_map[sid]["checks"] += 1
        if c.status == CheckRun.STATUS_FAIL:
            per_site_map[sid]["fails"] += 1
    per_site = sorted(per_site_map.values(), key=lambda r: r["fails"], reverse=True)

    import calendar
    title = f"{calendar.month_name[month].capitalize()} {year}"

    return render(
        request,
        "core/report_detail.html",
        {
            "title": title,
            "sites_count": len(set([c.site_id for c in checks])),
            "checks_total": checks_total,
            "fails_total": fails_total,
            "oks_total": oks_total,
            "per_site": per_site,
            **flags,
        },
    )


@login_required
def integrations(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)
    return render(request, "core/integrations.html", {**flags})


@login_required
def billing(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)
    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    sites_count = Site.objects.filter(id__in=site_ids, is_deleted=False).count()
    alerts_30d = CheckRun.objects.filter(site_id__in=site_ids, status=CheckRun.STATUS_FAIL, created_at__gte=timezone.now()-timedelta(days=30)).count()

    from billing.models import Plan, Subscription
    from billing.services import get_active_plan

    plans = list(Plan.objects.filter(is_active=True).order_by("sort_order"))
    current_plan = get_active_plan(request.user)
    subscription = Subscription.objects.filter(user=request.user).select_related("plan").first()

    return render(request, "core/billing.html", {
        "sites_count": sites_count,
        "alerts_30d": alerts_30d,
        "plans": plans,
        "current_plan": current_plan,
        "subscription": subscription,
        **flags,
    })

@login_required
def create_payment(request, plan_id):
    import uuid
    from billing.models import Plan, Payment
    from billing.yookassa_config import configure_yookassa
    from yookassa import Payment as YooPayment

    plan = get_object_or_404(Plan, id=plan_id, is_active=True)

    # Бесплатный тариф — оплата не нужна
    if plan.price_monthly <= 0:
        messages.info(request, "Этот тариф бесплатный — оплата не требуется.")
        return redirect("billing")

    configure_yookassa()
    yoo_payment = YooPayment.create({
        "amount": {"value": f"{plan.price_monthly}.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": request.build_absolute_uri("/billing/"),
        },
        "capture": True,
        "description": f"SEOdyssey — тариф {plan.name}",
        "metadata": {"plan_id": plan.id, "user_id": request.user.id},
    }, str(uuid.uuid4()))

    Payment.objects.create(
        user=request.user,
        plan=plan,
        yookassa_id=yoo_payment.id,
        amount=plan.price_monthly,
        status=Payment.STATUS_PENDING,
    )

    return redirect(yoo_payment.confirmation.confirmation_url)

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

@csrf_exempt
@require_POST
def yookassa_webhook(request):
    import json
    from billing.models import Payment
    from billing.yookassa_config import configure_yookassa
    from billing.services import apply_successful_payment
    from yookassa import Payment as YooPayment

    try:
        event = json.loads(request.body)
        payment_id = event["object"]["id"]
    except (ValueError, KeyError):
        return HttpResponse(status=400)



    # Не верим телу вебхука — перепроверяем статус у ЮKassa по ID
    configure_yookassa()
    try:
        yoo = YooPayment.find_one(payment_id)
    except Exception:
        return HttpResponse(status=400)

    # --- Платёж за GEO-отчёт (гостевой, метим по metadata) ---
    metadata = getattr(yoo, "metadata", None) or {}
    geo_token = metadata.get("geo_lead_token")
    if geo_token:
        from django.utils import timezone
        from landing.models import GeoLead
        try:
            lead = GeoLead.objects.get(access_token=geo_token)
        except GeoLead.DoesNotExist:
            return HttpResponse(status=200)
        if yoo.status == "succeeded" and lead.pay_status != GeoLead.PAY_PAID:
            lead.pay_status = GeoLead.PAY_PAID
            lead.paid_at = timezone.now()
            lead.save(update_fields=["pay_status", "paid_at"])
            # Запуск генерации AI-разбора в фоне (Celery)
            from landing.tasks import generate_geo_ai_report
            generate_geo_ai_report.delay(lead.pk)
        elif yoo.status == "canceled":
            lead.pay_status = GeoLead.PAY_NONE
            lead.save(update_fields=["pay_status"])
        return HttpResponse(status=200)

    try:
        payment = Payment.objects.get(yookassa_id=payment_id)
    except Payment.DoesNotExist:
        return HttpResponse(status=200)  # не наш платёж — просто подтверждаем приём

    if yoo.status == "succeeded":
        apply_successful_payment(payment)
    elif yoo.status == "canceled":
        payment.status = Payment.STATUS_CANCELED
        payment.save(update_fields=["status"])

    return HttpResponse(status=200)

@login_required
def team(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    # показываем уникальных пользователей, которые имеют доступ к вашим сайтам
    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    members = (
        SiteMember.objects.select_related("user")
        .filter(site_id__in=site_ids)
        .order_by("user__username")
    )
    # уникализируем по user
    uniq = {}
    for m in members:
        if m.user_id not in uniq:
            uniq[m.user_id] = m
    return render(request, "core/team.html", {"members": list(uniq.values()), **flags})


@login_required
def help_page(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    sites = Site.objects.filter(id__in=site_ids, is_deleted=False).order_by("name")
    return render(request, "core/help.html", {"sites": sites, **flags})

@login_required
@require_POST
def yandex_disconnect(request):
    profile = _get_profile(request.user)

    with transaction.atomic():
        oauth = getattr(request.user, "yandex_oauth", None)  # OneToOne у User
        if oauth:
            oauth.delete()

        profile.yandex_connected = False
        profile.save(update_fields=["yandex_connected"])

    messages.success(request, "Яндекс отключён. Теперь можно подключить другой аккаунт.")
    return redirect("user_settings")

@login_required
def yandex_connect(request):
    state = secrets.token_urlsafe(24)
    request.session["yandex_oauth_state"] = state
    request.session["yandex_oauth_next"] = request.GET.get("next", "")

    params = {
        "response_type": "code",
        "client_id": settings.YANDEX_CLIENT_ID,
        "redirect_uri": settings.YANDEX_REDIRECT_URI,
        "scope": settings.YANDEX_SCOPE,
        "state": state,
        "force_confirm": "yes",
        "lang": "ru",
    }

    url = "https://oauth.yandex.ru/authorize?" + urlencode(params)
    return redirect(url)


@login_required
def yandex_callback(request):
    code = request.GET.get("code")
    state = request.GET.get("state")

    if not code:
        return HttpResponseBadRequest("No code in callback")
    if state != request.session.get("yandex_oauth_state"):
        return HttpResponseBadRequest("Invalid state")

    token_url = "https://oauth.yandex.ru/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": settings.YANDEX_CLIENT_ID,
        "client_secret": settings.YANDEX_CLIENT_SECRET,
    }

    r = requests.post(token_url, data=data, timeout=20)

    r.raise_for_status()
    payload = r.json()

    access_token = payload["access_token"]
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")

    expires_at = None
    if expires_in:
        expires_at = timezone.now() + timezone.timedelta(seconds=int(expires_in))

    YandexOAuth.objects.update_or_create(
        user=request.user,
        defaults={
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "scope": settings.YANDEX_SCOPE,
        },
    )

    profile = _get_profile(request.user)
    profile.yandex_connected = True
    profile.save(update_fields=["yandex_connected"])

    # webmaster_user_id будет получен лениво при первом запросе счётчиков

    # ВАЖНО: чтобы твой yandex_ready стал ✅
    profile = _get_profile(request.user)
    profile.yandex_connected = True
    profile.save(update_fields=["yandex_connected"])

    messages.success(request, "Яндекс подключен ✅")
    next_url = request.session.pop("yandex_oauth_next", "") or "user_settings"
    return redirect(next_url)

@login_required
def yandex_ping(request):
    oauth = request.user.yandex_oauth  # OneToOne
    headers = {"Authorization": f"OAuth {oauth.access_token}"}

    # 1) Пингуем Метрику: список счетчиков (management API)
    r = requests.get(
        "https://api-metrika.yandex.net/management/v1/counters",
        headers=headers,
        timeout=20,
    )
    return JsonResponse({"status": r.status_code, "body": r.json()})

@login_required
def user_settings(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    if request.method == "POST":
        if request.POST.get("telegram_enabled") == "false":
            profile.telegram_chat_id = ""
            profile.telegram_enabled = False
            profile.save()
            messages.success(request, "Telegram отключён ✅")
            return redirect("user_settings")
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            profile = form.save(commit=False)
            chat_id = (profile.telegram_chat_id or "").strip()
            profile.telegram_enabled = bool(chat_id)
            profile.save()
            messages.success(request, "Настройки сохранены ✅")
            return redirect("user_settings")
    else:
        form = UserProfileForm(instance=profile)

    return render(request, "core/user_settings.html", {"profile": profile, "form": form, **flags})


@login_required
@require_POST
def account_change_password(request):
    form = PasswordChangeForm(request.user, request.POST)
    if form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)  # не разлогинивать после смены
        messages.success(request, "Пароль изменён ✅")
    else:
        errs = "; ".join(e for errlist in form.errors.values() for e in errlist)
        messages.error(request, errs or "Не удалось изменить пароль")
    return redirect("user_settings")


@login_required
@require_POST
def account_change_email(request):
    new_email = (request.POST.get("email") or "").strip()
    password = request.POST.get("current_password") or ""
    if not request.user.check_password(password):
        messages.error(request, "Неверный текущий пароль")
    elif not new_email:
        messages.error(request, "Укажите новый email")
    elif User.objects.filter(email__iexact=new_email).exclude(pk=request.user.pk).exists():
        messages.error(request, "Этот email уже используется другим аккаунтом")
    else:
        request.user.email = new_email
        request.user.save(update_fields=["email"])
        messages.success(request, "Email обновлён ✅")
    return redirect("user_settings")


@login_required
@require_POST
def account_delete(request):
    if not request.user.check_password(request.POST.get("current_password") or ""):
        messages.error(request, "Неверный пароль — аккаунт не удалён")
        return redirect("user_settings")
    user = request.user

    # GeoLead не связан с User внешним ключом — заявку оставляют без аккаунта.
    # Каскад его не заденет, а email там лежит, поэтому чистим по адресу:
    # иначе после «удалить аккаунт» персональные данные остаются в базе.
    email = (user.email or "").strip()
    if email:
        from landing.models import GeoLead
        deleted, _ = GeoLead.objects.filter(email__iexact=email).delete()
        if deleted:
            logger.info("Удалено GEO-заявок вместе с аккаунтом: %s", deleted)

    logout(request)
    user.delete()
    return redirect("login")


@login_required
def account_export(request):
    u = request.user
    site_ids = list(
        SiteMember.objects.filter(user=u, site__is_deleted=False).values_list("site_id", flat=True)
    )
    sites = Site.objects.filter(id__in=site_ids, is_deleted=False)
    data = {
        "account": {
            "username": u.username,
            "email": u.email,
            "date_joined": u.date_joined.isoformat() if u.date_joined else None,
            "last_login": u.last_login.isoformat() if u.last_login else None,
        },
        "sites": [
            {"name": s.name, "domain": s.domain} for s in sites
        ],
    }
    try:
        from billing.models import Subscription
        sub = Subscription.objects.filter(user=u).select_related("plan").first()
        if sub:
            data["subscription"] = {
                "plan": sub.plan.name,
                "status": sub.status,
                "started_at": sub.started_at.isoformat() if sub.started_at else None,
            }
    except Exception:
        pass
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    resp = HttpResponse(payload, content_type="application/json; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="seodyssey-{u.username}.json"'
    return resp


@login_required
def site_metrica(request, site_id: int):
    site = get_object_or_404(Site, id=site_id)

    if not _require_site_access(request.user, site):
        return HttpResponseForbidden("Нет доступа к сайту")

    oauth = getattr(request.user, "yandex_oauth", None)
    if not oauth:
        messages.error(request, "Сначала подключи Яндекс в настройках аккаунта.")
        return redirect("user_settings")

    headers = {"Authorization": f"OAuth {oauth.access_token}"}

    r = requests.get(
        "https://api-metrika.yandex.net/management/v1/counters",
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()
    data = r.json()
    counters = data.get("counters", [])

    if request.method == "POST":
        counter_id = request.POST.get("counter_id")
        if counter_id:
            allowed, limit = can_add_integration(
                request.user, site, "yandex_metrica_counter_id"
            )
            if not allowed:
                messages.error(
                    request,
                    f"Достигнут лимит интеграций для сайта ({limit}). "
                    f"Перейдите на тариф выше, чтобы подключить больше."
                )
                return redirect("site_metrica", site_id=site.id)
            site.yandex_metrica_counter_id = int(counter_id)
            site.save(update_fields=["yandex_metrica_counter_id"])
            messages.success(request, "Счётчик Метрики привязан ✅")
            return redirect("site_webmaster", site_id=site.id)

    return render(
        request,
        "core/site_metrica.html",
        {
            "site": site,
            "counters": counters,
        },
    )

@login_required
def site_webmaster(request, site_id: int):
    site = get_object_or_404(Site, id=site_id)

    if not _require_site_access(request.user, site):
        return HttpResponseForbidden("Нет доступа к сайту")

    oauth = getattr(request.user, "yandex_oauth", None)
    if not oauth:
        messages.error(request, "Сначала подключи Яндекс в настройках аккаунта.")
        return redirect("user_settings")

    headers = {"Authorization": f"OAuth {oauth.access_token}"}

    # 1) user_id из Вебмастера
    r_user = requests.get(
        "https://api.webmaster.yandex.net/v4/user",
        headers=headers,
        timeout=20,
    )
    r_user.raise_for_status()
    user_id = r_user.json()["user_id"]

    # 2) список сайтов (hosts)
    r_hosts = requests.get(
        f"https://api.webmaster.yandex.net/v4/user/{user_id}/hosts",
        headers=headers,
        timeout=20,
    )
    r_hosts.raise_for_status()
    hosts = r_hosts.json().get("hosts", [])

    if request.method == "POST":
        host_id = request.POST.get("host_id")
        if host_id:
            allowed, limit = can_add_integration(
                request.user, site, "yandex_webmaster_host_id"
            )
            if not allowed:
                messages.error(
                    request,
                    f"Достигнут лимит интеграций для сайта ({limit}). "
                    f"Перейдите на тариф выше, чтобы подключить больше."
                )
                return redirect("site_webmaster", site_id=site.id)
            site.yandex_webmaster_host_id = host_id
            site.save(update_fields=["yandex_webmaster_host_id"])
            messages.success(request, "Вебмастер привязан ✅")
            return redirect("site_checks", site_id=site.id)

    return render(
        request,
        "core/site_webmaster.html",
        {"site": site, "hosts": hosts},
    )


@login_required
@require_POST
def issue_mute(request, issue_id: int):
    issue = get_object_or_404(Issue, id=issue_id)
    if not _require_site_access(request.user, issue.site):
        return HttpResponseForbidden("Нет доступа")

    issue.status = Issue.STATUS_MUTED
    issue.resolved_at = None
    issue.save(update_fields=["status", "resolved_at", "last_seen_at"])
    messages.success(request, "Проблема замьючена.")
    return redirect("site_checks", site_id=issue.site_id)


@login_required
@require_POST
def issue_resolve(request, issue_id: int):
    issue = get_object_or_404(Issue, id=issue_id)
    if not _require_site_access(request.user, issue.site):
        return HttpResponseForbidden("Нет доступа")

    issue.status = Issue.STATUS_RESOLVED
    issue.resolved_at = timezone.now()
    issue.save(update_fields=["status", "resolved_at", "last_seen_at"])
    messages.success(request, "Проблема закрыта.")
    return redirect("site_checks", site_id=issue.site_id)


@login_required
@require_POST
def site_rename(request, site_id: int):
    site = get_object_or_404(Site, id=site_id, is_deleted=False)
    if not _require_site_access(request.user, site):
        return HttpResponseForbidden("Нет доступа к сайту")

    name = (request.POST.get("name") or "").strip()
    if name:
        site.name = name
        site.save(update_fields=["name"])
        messages.success(request, "Название обновлено ✅")
    else:
        messages.error(request, "Название не может быть пустым.")

    return redirect("site_checks", site_id=site.id)

@login_required
@require_POST
def onboarding_done(request):
    profile = _get_profile(request.user)
    profile.onboarding_done = True
    profile.save(update_fields=["onboarding_done"])
    return JsonResponse({"ok": True})


@csrf_exempt
def telegram_webhook(request):
    if request.method != "POST":
        return HttpResponse("ok")

    # Эндпоинт публичный и без CSRF, поэтому единственное, что отличает
    # настоящий апдейт от подделки, — секрет из setWebhook(secret_token=...).
    # Без него кто угодно мог слать боту произвольные chat_id и текст
    # и перебирать tg_token чужих GEO-заявок.
    secret = settings.TELEGRAM_WEBHOOK_SECRET
    # Сравниваем байты: compare_digest на строках с не-ASCII бросает TypeError,
    # то есть заголовок с кириллицей выдавал бы 500 вместо отказа.
    provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not secret or not secrets.compare_digest(
        provided.encode("utf-8", "ignore"), secret.encode("utf-8")
    ):
        logger.warning("Telegram webhook: неверный секрет, апдейт отброшен")
        return HttpResponse(status=403)

    import json
    try:
        data = json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return HttpResponse("ok")

    # Telegram шлёт разные апдейты (message, edited_message, callback_query...) — берём только обычное сообщение
    message = data.get("message") or {}
    chat_id = message.get("chat", {}).get("id")
    text = message.get("text", "") or ""

    if not (chat_id and text.startswith("/start")):
        return HttpResponse("ok")

    from notifications.telegram import send_telegram_message

    # /start <token> — заявка из GEO-чекера на лендинге
    parts = text.split(maxsplit=1)
    token = parts[1].strip() if len(parts) > 1 else ""

    if token:
        from landing.models import GeoLead
        from landing.tasks import build_geo_report
        try:
            lead = GeoLead.objects.get(tg_token=token, channel=GeoLead.CHANNEL_TELEGRAM)
        except GeoLead.DoesNotExist:
            lead = None

        if lead is not None:
            # запоминаем chat_id в любом случае
            if not lead.tg_chat_id:
                lead.tg_chat_id = str(chat_id)
                lead.save(update_fields=["tg_chat_id"])

            if lead.status == GeoLead.STATUS_DONE and lead.result_json:
                # проверка уже готова — шлём отчёт сразу
                send_telegram_message(str(chat_id), build_geo_report(lead.url, lead.result_json))
            else:
                # ещё считается — отправит Celery-задача, когда закончит
                send_telegram_message(
                    str(chat_id),
                    "✅ Принято! Проверяю вашу страницу — отчёт придёт сюда через несколько секунд.",
                )
            return HttpResponse("ok")

    # обычный /start без токена — прежнее поведение
    send_telegram_message(
        str(chat_id),
        f"👋 Привет!\n\nТвой Telegram ID: <code>{chat_id}</code>\n\nСкопируй его и вставь в поле на странице Интеграции → https://seodyssey.ru/account/settings/"
    )
    return HttpResponse("ok")

@login_required
def analytics(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Нет доступа")

    from django.db.models import Count, Avg
    from django.db.models.functions import TruncDay, TruncHour, TruncWeek
    from django.utils import timezone
    from django.core.cache import cache
    import datetime
    import json

    CACHE_KEY = "analytics_data"
    CACHE_TTL = 300  # 5 минут

    cached = cache.get(CACHE_KEY)
    if cached:
        return render(request, "core/analytics.html", cached)

    now = timezone.now()
    days_30 = now - datetime.timedelta(days=30)
    days_7 = now - datetime.timedelta(days=7)

    # Пользователи
    users = User.objects.all().order_by("-last_login")

    # Регистрации по дням (30 дней)
    registrations = list(
        User.objects
        .filter(date_joined__gte=days_30)
        .annotate(day=TruncDay("date_joined"))
        .values("day")
        .annotate(c=Count("id"))
        .order_by("day")
    )

    # Активность по дням (30 дней)
    activity_by_day = list(
        UserActivity.objects
        .filter(created_at__gte=days_30)
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(c=Count("id"))
        .order_by("day")
    )

    # Проверки по дням
    checks_by_day = list(
        CheckRun.objects
        .filter(created_at__gte=days_30)
        .annotate(day=TruncDay("created_at"))
        .values("day")
        .annotate(c=Count("id"))
        .order_by("day")
    )

    # Активность по часам
    activity_by_hour = list(
        UserActivity.objects
        .filter(created_at__gte=days_7)
        .annotate(hour=TruncHour("created_at"))
        .values("hour")
        .annotate(c=Count("id"))
        .order_by("hour")
    )

    # Популярные страницы
    top_pages = list(
        UserActivity.objects
        .filter(created_at__gte=days_30)
        .values("path")
        .annotate(c=Count("id"))
        .order_by("-c")[:15]
    )

    # DAU/WAU
    dau = UserActivity.objects.filter(created_at__gte=now - datetime.timedelta(days=1)).values("user").distinct().count()
    wau = UserActivity.objects.filter(created_at__gte=days_7).values("user").distinct().count()

    # Проверки на пользователя
    total_users = User.objects.count()
    total_checks = CheckRun.objects.count()
    checks_per_user = round(total_checks / total_users, 1) if total_users else 0
    checks_per_user_30d = round(
        CheckRun.objects.filter(created_at__gte=days_30).count() / total_users, 1
    ) if total_users else 0

    # Retention по date_joined (правильный расчёт)
    cohort_weeks = 8
    retention_data = []
    for i in range(cohort_weeks):
        cohort_start = now - datetime.timedelta(weeks=cohort_weeks - i)
        cohort_end = cohort_start + datetime.timedelta(weeks=1)
        cohort_users = set(
            User.objects
            .filter(date_joined__gte=cohort_start, date_joined__lt=cohort_end)
            .values_list("id", flat=True)
        )
        if not cohort_users:
            retention_data.append({"week": cohort_start.strftime("%d.%m"), "size": 0, "weeks": []})
            continue
        weeks_ret = []
        for j in range(cohort_weeks - i):
            ret_start = cohort_end + datetime.timedelta(weeks=j)
            ret_end = ret_start + datetime.timedelta(weeks=1)
            returned = UserActivity.objects.filter(
                created_at__gte=ret_start,
                created_at__lt=ret_end,
                user_id__in=cohort_users
            ).values("user_id").distinct().count()
            pct = round(returned / len(cohort_users) * 100) if cohort_users else 0
            weeks_ret.append(pct)
        retention_data.append({
            "week": cohort_start.strftime("%d.%m"),
            "size": len(cohort_users),
            "weeks": weeks_ret,
        })

    # Сортируем по реальным датам (date), а не по строкам "%d.%m".
    # Иначе "01.06" встаёт раньше "11.05" — строковое сравнение по символам.
    all_dates = sorted(set(
        [r["day"] for r in activity_by_day] +
        [r["day"] for r in checks_by_day] +
        [r["day"] for r in registrations]
    ))
    act_map = {r["day"]: r["c"] for r in activity_by_day}
    chk_map = {r["day"]: r["c"] for r in checks_by_day}
    reg_map = {r["day"]: r["c"] for r in registrations}
    # Подписи формируем только сейчас, уже после сортировки по дате.
    chart_days = json.dumps([d.strftime("%d.%m") for d in all_dates])
    chart_activity = json.dumps([act_map.get(d, 0) for d in all_dates])
    chart_checks = json.dumps([chk_map.get(d, 0) for d in all_dates])
    chart_registrations = json.dumps([reg_map.get(d, 0) for d in all_dates])

    # Часы суток — заполняем все 24 часа
    hour_map = {r["hour"].hour: r["c"] for r in activity_by_hour}
    chart_hours = json.dumps(list(range(24)))
    chart_hours_data = json.dumps([hour_map.get(h, 0) for h in range(24)])

    ctx = {
        "users": users,
        "registrations": registrations,
        "activity_by_day": activity_by_day,
        "activity_by_hour": activity_by_hour,
        "top_pages": top_pages,
        "dau": dau,
        "wau": wau,
        "checks_by_day": checks_by_day,
        "retention_data": retention_data,
        "total_users": total_users,
        "total_sites": Site.objects.filter(is_deleted=False).count(),
        "total_checks": total_checks,
        "checks_per_user": checks_per_user,
        "checks_per_user_30d": checks_per_user_30d,
        "chart_days": chart_days,
        "chart_activity": chart_activity,
        "chart_checks": chart_checks,
        "chart_registrations": chart_registrations,
        "chart_hours": chart_hours,
        "chart_hours_data": chart_hours_data,
    }

    ctx["metric_cards"] = [
        ("Пользователей", total_users, "#111"),
        ("Сайтов", ctx["total_sites"], "#111"),
        ("Проверок", total_checks, "#111"),
        ("DAU", dau, "#6378ff"),
        ("WAU", wau, "#6378ff"),
        ("Проверок/юзер", checks_per_user, "#3b6d11"),
        ("Проверок/юзер 30д", checks_per_user_30d, "#3b6d11"),
    ]

    cache.set(CACHE_KEY, ctx, CACHE_TTL)
    return render(request, "core/analytics.html", ctx)

class ThrottledPasswordResetView(auth_views.PasswordResetView):
    """
    Сброс пароля шлёт письмо на каждый POST, без всякого предела: удобный
    способ и завалить чужой ящик, и сжечь нашу квоту у отправителя.
    Ограничиваем по IP.
    """

    RESET_LIMIT = 5
    RESET_WINDOW = 3600

    def post(self, request, *args, **kwargs):
        from landing.rate_limit import is_rate_limited

        if is_rate_limited(request, "pwreset",
                           limit=self.RESET_LIMIT, window=self.RESET_WINDOW):
            form = self.get_form()
            form.add_error(
                None,
                "Слишком много запросов на сброс пароля с вашего адреса. "
                "Попробуйте через час.",
            )
            return self.form_invalid(form)
        return super().post(request, *args, **kwargs)


LIMIT_EXHAUSTED_TEXT = "Лимит AI-проверок исчерпан. Обновится 1-го числа следующего месяца."


@login_required
def ai_recommendations(request, check_id):
    check = get_object_or_404(CheckRun, id=check_id, site__members__user=request.user)

    from core.ai_limits import check_ai_limit, check_and_spend_ai_limit

    # Лимита здесь не было вовсе — эндпоинт можно было дёргать в цикле.
    ok, used, limit = check_ai_limit(request.user)
    if not ok:
        return JsonResponse({
            "status": "limit",
            "text": LIMIT_EXHAUSTED_TEXT,
            "ai_used": used,
            "ai_limit": limit,
        }, status=403)

    check_data = check.result or {}

    try:
        from rag.engine import get_recommendations
        data = get_recommendations(check_data)
    except Exception:
        logger.exception("AI-рекомендации не собрались, check_id=%s", check_id)
        return JsonResponse(
            {"status": "error", "text": "Не удалось собрать рекомендации. Попробуйте позже."},
            status=500,
        )

    _, used, limit = check_and_spend_ai_limit(request.user)
    return JsonResponse({"status": "ok", "data": data, "ai_used": used, "ai_limit": limit})

@login_required
@require_POST
def site_ai_recommendations(request, site_id):
    site = get_object_or_404(Site, id=site_id, is_deleted=False)
    if not _require_site_access(request.user, site):
        return HttpResponseForbidden("Нет доступа к сайту")

    from core.ai_limits import check_ai_limit, check_and_spend_ai_limit

    last = site.checks.first()
    if not last:
        return JsonResponse({"status": "error", "text": "Нет данных проверки"}, status=400)

    # Проверяем ДО обращения к модели: раньше лимит смотрели после, и юзер
    # с исчерпанной квотой получал отказ уже после того, как запрос оплачен.
    ok, used, limit = check_ai_limit(request.user)
    if not ok:
        return JsonResponse({
            "status": "limit",
            "text": LIMIT_EXHAUSTED_TEXT,
            "ai_used": used,
            "ai_limit": limit,
        }, status=403)

    check_data = last.result or {}

    try:
        from rag.engine import get_recommendations
        data = get_recommendations(check_data)
    except Exception:
        logger.exception("AI-рекомендации не собрались, site_id=%s", site_id)
        return JsonResponse(
            {"status": "error", "text": "Не удалось собрать рекомендации. Попробуйте позже."},
            status=500,
        )

    # Списываем только после успешного ответа
    _, used, limit = check_and_spend_ai_limit(request.user)
    return JsonResponse({"status": "ok", "data": data, "ai_used": used, "ai_limit": limit})
