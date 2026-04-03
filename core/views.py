from __future__ import annotations
import json
import requests
import secrets
from django.http import JsonResponse
from urllib.parse import urlencode
from django.conf import settings
from core.models import YandexOAuth, Issue
from datetime import timedelta, date
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Count, Max
from django.http import HttpResponseForbidden, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from core.forms import AddSiteForm
from core.forms_profile import UserProfileForm
from core.models import Site, SiteMember, CheckRun, UserProfile


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


@login_required
def site_new(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    if request.method == "POST":
        form = AddSiteForm(request.POST)
        if form.is_valid():
            restored_site = getattr(form, "restored_site", None)
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

            messages.success(request, "Сайт добавлен ✅")
            return redirect("site_checks", site_id=site.id)
    else:
        form = AddSiteForm()

    return render(request, "core/site_new.html", {"form": form, **flags})


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
    return redirect("site_checks", site_id=site.id)


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
    # в модели deleted_at очищается при restore(), поэтому просто оставим False. (Можно хранить audit later)

    last_check_at = last.created_at if last else None

    open_issues = (
        Issue.objects
        .filter(site=site, status=Issue.STATUS_OPEN)
        .order_by("-severity", "-last_seen_at")
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
    messages.success(request, "Сайт удалён (soft delete).")
    return redirect("sites")


@login_required
def alerts(request):
    profile = _get_profile(request.user)
    flags = _integration_flags(profile)

    q = (request.GET.get("q") or "").strip()

    site_ids = list(
        SiteMember.objects.filter(user=request.user, site__is_deleted=False).values_list("site_id", flat=True)
    )
    qs = CheckRun.objects.select_related("site").filter(
        site_id__in=site_ids, status=CheckRun.STATUS_FAIL
    )

    if q:
        qs = qs.filter(Q(site__name__icontains=q) | Q(site__domain__icontains=q))

    alerts_list = list(qs.order_by("-created_at")[:100])
    return render(request, "core/alerts.html", {"alerts": alerts_list, "q": q, **flags})


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
    return render(request, "core/billing.html", {"sites_count": sites_count, "alerts_30d": alerts_30d, **flags})


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
    return redirect("integrations")

@login_required
def yandex_connect(request):
    state = secrets.token_urlsafe(24)
    request.session["yandex_oauth_state"] = state

    params = {
        "response_type": "code",
        "client_id": settings.YANDEX_CLIENT_ID,
        "redirect_uri": settings.YANDEX_REDIRECT_URI,
        "scope": settings.YANDEX_SCOPE,
        "state": state,
        "force_confirm": "yes",
    }
    url = "https://oauth.yandex.com/authorize?" + urlencode(params)
    return redirect(url)


@login_required
def yandex_callback(request):
    code = request.GET.get("code")
    state = request.GET.get("state")

    if not code:
        return HttpResponseBadRequest("No code in callback")
    if state != request.session.get("yandex_oauth_state"):
        return HttpResponseBadRequest("Invalid state")

    token_url = "https://oauth.yandex.com/token"
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

    try:
        headers = {"Authorization": f"OAuth {access_token}"}
        r_user = requests.get("https://api.webmaster.yandex.net/v4/user", headers=headers, timeout=20)
        r_user.raise_for_status()
        obj.webmaster_user_id = r_user.json().get("user_id")
        obj.save(update_fields=["webmaster_user_id"])
    except Exception:
        pass

    # ВАЖНО: чтобы твой yandex_ready стал ✅
    profile = _get_profile(request.user)
    profile.yandex_connected = True
    profile.save(update_fields=["yandex_connected"])

    messages.success(request, "Яндекс подключен ✅")
    return redirect("user_settings")

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
        form = UserProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, "Настройки сохранены ✅")
            return redirect("user_settings")
    else:
        form = UserProfileForm(instance=profile)

    return render(request, "core/user_settings.html", {"profile": profile, "form": form, **flags})

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
            site.yandex_metrica_counter_id = int(counter_id)
            site.save(update_fields=["yandex_metrica_counter_id"])
            messages.success(request, "Счётчик Метрики привязан ✅")
            return redirect("site_checks", site_id=site.id)

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
