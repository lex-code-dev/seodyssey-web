from django.urls import path
from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("sites/", views.sites, name="sites"),
    path("sites/new/", views.site_new, name="site_new"),
    path("integrations/yandex/connect/", views.yandex_connect, name="yandex_connect"),
    path("integrations/yandex/callback/", views.yandex_callback, name="yandex_callback"),
    path("integrations/yandex/disconnect/", views.yandex_disconnect, name="yandex_disconnect"),
    path("sites/<int:site_id>/metrica/", views.site_metrica, name="site_metrica"),
    path("sites/<int:site_id>/webmaster/", views.site_webmaster, name="site_webmaster"),
    path("integrations/yandex/ping/", views.yandex_ping, name="yandex_ping"),
    path("sites/<int:site_id>/", views.site_checks, name="site_checks"),
    path("sites/<int:site_id>/run/", views.run_check, name="run_check"),
    path("sites/<int:site_id>/delete/", views.delete_site, name="delete_site"),
    path("checks/<int:check_id>/", views.check_detail, name="check_detail"),
    path("alerts/", views.alerts, name="alerts"),
    path("alerts/<int:check_id>/", views.alert_detail, name="alert_detail"),
    path("reports/", views.reports, name="reports"),
    path("reports/<int:year>/<int:month>/", views.report_detail, name="report_detail"),
    path("integrations/", views.integrations, name="integrations"),
    path("billing/", views.billing, name="billing"),
    path("team/", views.team, name="team"),
    path("help/", views.help_page, name="help"),
    path("account/settings/", views.user_settings, name="user_settings"),
    path("issues/<int:issue_id>/mute/", views.issue_mute, name="issue_mute"),
    path("issues/<int:issue_id>/resolve/", views.issue_resolve, name="issue_resolve"),
]
