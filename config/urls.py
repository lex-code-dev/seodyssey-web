from django.contrib import admin
from django.urls import path, include
from django.http import HttpResponse

def google_verify(request):
    return HttpResponse(
        "google-site-verification: googlef9201b04cc74dcc6.html",
        content_type="text/html"
    )

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("landing.urls")),
    path("", include("core.urls")),
    path("", include("course.urls")),
    path("", include("django.contrib.auth.urls")),
    path('ai-queries/', include('ai_queries.urls')),
    path('audits/', include('audits.urls')),
    path('googlef9201b04cc74dcc6.html', google_verify),
    path("ckeditor5/", include("django_ckeditor_5.urls")),
]
