from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, Http404
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from landing.forms import GeoCheckForm
from landing.models import GeoLead, ServicePrice, BlogPost



def index(request):
    return render(request, "landing/index.html")


def index_v2(request):
    """Макет редизайна главной в стиле Magic Portfolio. Превью на /v2/,
    в прод-навигацию не выводится; контекст тот же, что собирает
    AppDomainMiddleware для настоящей главной."""
    from billing.models import Plan
    from landing.models import ServicePrice

    return render(request, "landing/index_v2.html", {
        "plans": Plan.objects.filter(is_active=True).order_by("sort_order"),
        "latest_posts": BlogPost.objects.filter(is_published=True)[:3],
        "geo_report": ServicePrice.objects.filter(
            code="geo_report", is_active=True
        ).first(),
    })


def requisites(request):
    return render(request, "landing/requisites.html")


def offer(request):
    return render(request, "landing/offer.html")


def geo_check(request):
    from landing.rate_limit import (
        GEO_LIMIT, GEO_RATE_LIMIT_MESSAGE, GEO_WINDOW, is_rate_limited,
    )

    if request.method == "POST":
        form = GeoCheckForm(request.POST)
        # is_valid() до add_error: иначе форме некуда класть ошибку.
        # Лимит считаем только для валидных отправок — опечатка в адресе
        # не должна съедать квоту.
        valid = form.is_valid()
        if valid and is_rate_limited(request, "geo", limit=GEO_LIMIT, window=GEO_WINDOW):
            form.add_error(None, GEO_RATE_LIMIT_MESSAGE)
        elif valid:
            from landing.models import _gen_report_token
            lead = GeoLead.objects.create(
                url=form.cleaned_data["url"],
                channel="",  # канал больше не выбирается на входе
                access_token=_gen_report_token(),
            )
            # Запуск проверки в фоне (Celery)
            from landing.tasks import run_geo_check
            run_geo_check.delay(lead.pk)
            # Сразу ведём на страницу отчёта — она сама покажет "идёт проверка"
            return redirect("geo_report", token=lead.access_token)
    else:
        form = GeoCheckForm()

    return render(request, "landing/geo_check.html", {"form": form})


def geo_report(request, token):
    lead = get_object_or_404(GeoLead, access_token=token)
    results = lead.result_json or []
    check_done = lead.status == GeoLead.STATUS_DONE

    ok = sum(1 for r in results if r.get("status") == "ok")
    warn = sum(1 for r in results if r.get("status") == "warning")
    fail = sum(1 for r in results if r.get("status") == "fail")
    total = len(results) or 1
    percent = round(ok / total * 100)

    ai_report = lead.ai_report if isinstance(lead.ai_report, dict) else {}
    is_paid = lead.pay_status == GeoLead.PAY_PAID
    awaiting_payment = request.GET.get("paid") == "1" and not is_paid
    sp = ServicePrice.objects.filter(code="geo_report", is_active=True).first()
    price = sp.price if sp else 890
    old_price = sp.old_price if sp else None
    discount = round((1 - price / old_price) * 100) if old_price and old_price > price else None
    ai_status = ai_report.get("status")  # "done" / "error" / None

    return render(request, "landing/geo_report.html", {
        "lead": lead,
        "results": results,
        "check_done": check_done,
        "ok": ok,
        "warn": warn,
        "fail": fail,
        "percent": percent,
        "fails": [r for r in results if r.get("status") == "fail"],
        "warns": [r for r in results if r.get("status") == "warning"],
        "oks": [r for r in results if r.get("status") == "ok"],
        "is_paid": is_paid,
        "price": price,
        "old_price": old_price,
        "discount": discount,
        "ai_items": ai_report.get("items", []),
        "ai_ready": is_paid and ai_status == "done",
        "demo_item": lead.demo_item if not is_paid else None,
        "ai_pending": is_paid and ai_status is None,
        "ai_error": is_paid and ai_status == "error",
        "awaiting_payment": awaiting_payment,
    })


def geo_pay(request, token):
    """Создаёт платёж YuKassa за полный AI-разбор GEO-отчёта (гость, без аккаунта)."""
    import uuid
    from billing.yookassa_config import configure_yookassa
    from yookassa import Payment as YooPayment

    lead = get_object_or_404(GeoLead, access_token=token)

    # Уже оплачено — не создаём повторный платёж, ведём на отчёт
    if lead.pay_status == GeoLead.PAY_PAID:
        return redirect("geo_report", token=token)

    sp = ServicePrice.objects.filter(code="geo_report", is_active=True).first()
    GEO_REPORT_PRICE = sp.price if sp else 890  # источник истины — админка; 890 — fallback
    configure_yookassa()
    yoo_payment = YooPayment.create({
        "amount": {"value": f"{GEO_REPORT_PRICE}.00", "currency": "RUB"},
        "confirmation": {
            "type": "redirect",
            "return_url": request.build_absolute_uri(f"/geo-check/report/{token}/?paid=1"),
        },
        "capture": True,
        "description": "SEOdyssey — разбор GEO-отчёта с рекомендациями",
        "metadata": {"geo_lead_token": token},
    }, str(uuid.uuid4()))

    lead.yk_payment_id = yoo_payment.id
    lead.pay_status = GeoLead.PAY_PENDING
    lead.save(update_fields=["yk_payment_id", "pay_status"])

    return redirect(yoo_payment.confirmation.confirmation_url)


def geo_report_send_email(request, token):
    """Отправка GEO-отчёта себе на email по кнопке на странице отчёта."""
    from django.core.mail import send_mail
    lead = get_object_or_404(GeoLead, access_token=token)
    if lead.status != GeoLead.STATUS_DONE:
        return redirect("geo_report", token=token)
    if request.method != "POST":
        return redirect("geo_report", token=token)
    # Адрес привязывается к заявке один раз и больше не меняется. Иначе один
    # действующий токен превращался в ретранслятор: адрес брался из формы,
    # и по одной ссылке можно было слать письма на сколько угодно чужих ящиков.
    # Теперь на каждый новый адрес нужна новая GEO-проверка, а она уже под
    # своим лимитом.
    if lead.email:
        email = lead.email
    else:
        email = (request.POST.get("email") or "").strip()
        if not email:
            return redirect("geo_report", token=token)
        try:
            validate_email(email)
        except DjangoValidationError:
            return redirect(f"/geo-check/report/{token}/?mail=err")

    from landing.rate_limit import GEO_LIMIT, GEO_WINDOW, is_rate_limited
    if is_rate_limited(request, "geo_email", limit=GEO_LIMIT, window=GEO_WINDOW):
        return redirect(f"/geo-check/report/{token}/?mail=limit")

    if not lead.email:
        lead.email = email
        lead.save(update_fields=["email"])
    results = lead.result_json or []
    ok = sum(1 for r in results if r.get("status") == "ok")
    warn = sum(1 for r in results if r.get("status") == "warning")
    fail = sum(1 for r in results if r.get("status") == "fail")
    total = len(results) or 1
    percent = round(ok / total * 100)
    report_url = f"{SITE_URL}/geo-check/report/{lead.access_token}/"
    from django.template.loader import render_to_string
    ai_report = lead.ai_report if isinstance(lead.ai_report, dict) else {}
    is_paid = lead.pay_status == GeoLead.PAY_PAID
    ai_ready = is_paid and ai_report.get("status") == "done"
    if ai_ready:
        # Оплачено и разбор готов — шлём письмо про AI-разбор
        items_count = len(ai_report.get("items", []))
        subject = "Ваш AI-разбор готов"
        body = (
            f"Ваш AI-разбор готов.\n\n"
            f"Мы разобрали {items_count} проблем(ы) вашей страницы с готовыми решениями:\n{lead.url}\n\n"
            f"Открыть разбор:\n{report_url}\n\n"
            f"— SEOdyssey"
        )
        html_body = render_to_string("landing/email/geo_paid_email.html", {
            "url": lead.url,
            "items_count": items_count,
            "items": ai_report.get("items", []),
            "report_url": report_url,
        })
    else:
        # Бесплатный отчёт
        subject = "Ваш GEO-аудит готов"
        body = (
            f"Готов GEO-аудит вашей страницы:\n{lead.url}\n\n"
            f"Готовность к GEO: {percent}%\n"
            f"Пройдено: {ok}   Замечания: {warn}   Критично: {fail}\n\n"
            f"Полный отчёт с рекомендациями:\n{report_url}\n\n"
            f"— SEOdyssey"
        )
        html_body = render_to_string("landing/email/geo_report_email.html", {
            "url": lead.url,
            "percent": percent,
            "ok": ok,
            "warn": warn,
            "fail": fail,
            "report_url": report_url,
        })
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=None,
            recipient_list=[email],
            fail_silently=False,
            html_message=html_body,
        )
    except Exception:
        return redirect(f"/geo-check/report/{token}/?mail=err")
    return redirect(f"/geo-check/report/{token}/?mail=ok")


def geo_report_pdf(request, token):
    """Выгрузка GEO-отчёта в PDF. Бесплатный чек — всем; AI-разбор — только оплатившим (вариант C)."""
    from django.template.loader import render_to_string
    from django.utils import timezone

    lead = get_object_or_404(GeoLead, access_token=token)

    # Проверка ещё не готова — PDF не отдаём, возвращаем на страницу отчёта
    if lead.status != GeoLead.STATUS_DONE:
        return redirect("geo_report", token=token)

    # Email-gate: PDF отдаём только если email уже есть или пришёл сейчас POST'ом
    if request.method == "POST":
        email = (request.POST.get("email") or "").strip()
        if email:
            if not lead.email:
                lead.email = email
                lead.save(update_fields=["email"])
        else:
            # пустой email — назад на отчёт
            return redirect("geo_report", token=token)
    elif not lead.email:
        # GET без сохранённого email — не отдаём PDF, отправляем на отчёт
        return redirect("geo_report", token=token)

    results = lead.result_json or []
    ok = sum(1 for r in results if r.get("status") == "ok")
    warn = sum(1 for r in results if r.get("status") == "warning")
    fail = sum(1 for r in results if r.get("status") == "fail")
    total = len(results) or 1
    percent = round(ok / total * 100)

    # Геометрия бублика (r=50 → длина окружности 2*pi*50 ≈ 314.16)
    c = 314.16
    len_ok = ok / total * c

    ai_report = lead.ai_report if isinstance(lead.ai_report, dict) else {}
    is_paid = lead.pay_status == GeoLead.PAY_PAID
    # Вернулись с оплаты (?paid=1), но вебхук ещё не подтвердил платёж — показываем "ждём", а не кнопку оплаты
    awaiting_payment = request.GET.get("paid") == "1" and not is_paid
    ai_ready = is_paid and ai_report.get("status") == "done"

    context = {
        "lead": lead,
        "now": timezone.now(),
        "percent": percent,
        "ok": ok,
        "warn": warn,
        "fail": fail,
        "fails": [r for r in results if r.get("status") == "fail"],
        "warns": [r for r in results if r.get("status") == "warning"],
        "oks": [r for r in results if r.get("status") == "ok"],
        "ai_ready": ai_ready,
        "ai_items": ai_report.get("items", []),
        # строка с точкой — защита от запятой в русской локали
        "donut_ok": f"{len_ok:.2f}",
    }

    html_string = render_to_string("landing/geo_report_pdf.html", context)
    from weasyprint import HTML  # отложенный импорт: грузится только при генерации PDF
    pdf_bytes = HTML(
        string=html_string,
        base_url=request.build_absolute_uri("/"),
    ).write_pdf()

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="seodyssey-geo-report.pdf"'
    return response

def blog_index(request):
    posts = BlogPost.objects.filter(is_published=True)
    return render(request, "landing/blog_index.html", {"posts": posts})


def blog_post(request, slug):
    try:
        post = BlogPost.objects.get(slug=slug, is_published=True)
    except BlogPost.DoesNotExist:
        raise Http404("Статья не найдена")
    return render(request, "landing/blog_post.html", {"post": post})

SITE_URL = "https://seodyssey.ru"


def sitemap_xml(request):
    """
    Карта сайта лежит в templates/sitemap.xml: статические страницы правятся
    прямо в нём, статьи блога подставляются из базы.
    """
    posts = BlogPost.objects.filter(is_published=True)
    return render(
        request,
        "sitemap.xml",
        {"site_url": SITE_URL, "posts": posts},
        content_type="application/xml",
    )


def tools_index(request):
    """Витрина бесплатных инструментов — родительская страница для /tools/*."""
    return render(request, "landing/tools/index.html")


def robots_txt_check_tool(request):
    """
    Два режима: robots.txt всего сайта и разбор одной страницы —
    закрыта ли она от индексации и каким именно правилом.
    """
    from audits.net_guard import TargetError, normalize_domain, normalize_url
    from landing.rate_limit import RATE_LIMIT_MESSAGE, is_rate_limited

    results = None
    error = None
    target = ""
    mode = "site"

    if request.method == "POST":
        mode = "page" if request.POST.get("mode") == "page" else "site"
        target = (request.POST.get("domain") or "").strip()
        if target and is_rate_limited(request, "tools"):
            error = RATE_LIMIT_MESSAGE
        elif target:
            from audits.checks.indexability import check_page, check_robots_txt
            try:
                if mode == "page":
                    results = check_page(normalize_url(target))
                else:
                    results = check_robots_txt(normalize_domain(target))
            except TargetError as e:
                error = str(e)

    return render(request, "landing/tools/robots-txt-check.html", {
        "domain": target,
        "mode": mode,
        "results": results,
        "error": error,
    })


def meta_tags_check_tool(request):
    """Проверка title, description и H1 одной страницы + превью сниппета."""
    from audits.net_guard import TargetError, normalize_url
    from landing.rate_limit import RATE_LIMIT_MESSAGE, is_rate_limited

    results = None
    error = None
    meta = None
    target = ""

    if request.method == "POST":
        target = (request.POST.get("domain") or "").strip()
        if target and is_rate_limited(request, "tools"):
            error = RATE_LIMIT_MESSAGE
        elif target:
            from audits.checks.seo import fetch_and_evaluate_seo
            try:
                results, meta = fetch_and_evaluate_seo(normalize_url(target))
            except TargetError as e:
                error = str(e)

    return render(request, "landing/tools/meta-tags-check.html", {
        "domain": target,
        "results": results,
        "meta": meta,
        "error": error,
    })


def robots_txt(request):
    """
    Правила лежат в templates/robots.txt (лендинг) и templates/robots_app.txt
    (личный кабинет) — правятся как обычные txt-файлы, без правок кода.
    Личный кабинет на app.* закрыт от индексации целиком.
    """
    host = request.get_host().split(":")[0]
    template = "robots_app.txt" if host.startswith("app.") else "robots.txt"
    return render(request, template, {"site_url": SITE_URL}, content_type="text/plain")