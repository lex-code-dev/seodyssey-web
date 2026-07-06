from django.urls import path

from landing import views

urlpatterns = [
    path("requisites/", views.requisites, name="requisites"),
    path("offer/", views.offer, name="offer"),
    path("geo-check/", views.geo_check, name="geo_check"),
    path("geo-check/report/<str:token>/", views.geo_report, name="geo_report"),
    path("geo-check/pay/<str:token>/", views.geo_pay, name="geo_pay"),
    path("geo-check/report/<str:token>/pdf/", views.geo_report_pdf, name="geo_report_pdf"),
    path("geo-check/report/<str:token>/send-email/", views.geo_report_send_email, name="geo_report_send_email"),
    path("blog/", views.blog_index, name="blog_index"),
    path("blog/<slug:slug>/", views.blog_post, name="blog_post"),
    path("sitemap.xml", views.sitemap_xml, name="sitemap_xml"),
    path("robots.txt", views.robots_txt, name="robots_txt"),
    path("tools/robots-txt-check/", views.robots_txt_check_tool, name="robots_txt_check"),
]
