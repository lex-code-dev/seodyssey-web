from django.shortcuts import redirect, render
from core.models import UserActivity

class AppDomainMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.get_host().split(":")[0]
        path = request.path

        if host in ("seodyssey.ru", "www.seodyssey.ru"):
            if path == "/":
                from billing.models import Plan
                from landing.models import BlogPost, ServicePrice
                plans = Plan.objects.filter(is_active=True).order_by("sort_order")
                latest_posts = BlogPost.objects.filter(is_published=True)[:3]
                geo_report = ServicePrice.objects.filter(
                    code="geo_report", is_active=True
                ).first()
                return render(request, "landing/index.html", {
                    "plans": plans,
                    "latest_posts": latest_posts,
                    "geo_report": geo_report,
                })
            if path == "/requisites/":
                return render(request, "landing/requisites.html")
            if path == "/offer/":
                return render(request, "landing/offer.html")
            # Публичный GEO-чекер живёт на лендинге — отдаём обычным вьюхам, не редиректим на app
            if path.startswith("/geo-check/"):
                return self.get_response(request)
            # Блог тоже публичный, живёт на лендинге
            if path.startswith("/blog/"):
                return self.get_response(request)
            # sitemap и robots — отдаём Django, не редиректим
            if path in ("/sitemap.xml", "/robots.txt"):
                return self.get_response(request)
            return redirect("https://app.seodyssey.ru" + path)

        if host == "app.seodyssey.ru":
            if path == "/" and not request.user.is_authenticated:
                return redirect("https://app.seodyssey.ru/login/")

        return self.get_response(request)

class ActivityMiddleware:
    SKIP_PREFIXES = (
        "/static/",
        "/favicon",
        "/admin/",
        "/telegram/webhook/",
        "/onboarding/done/",
        "/sites/new/integrations/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        if not request.user.is_authenticated:
            return response

        path = request.path
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return response

        if request.method not in ("GET", "POST"):
            return response

        try:
            UserActivity.objects.create(
                user=request.user,
                path=path,
                method=request.method,
            )
        except Exception:
            pass

        return response