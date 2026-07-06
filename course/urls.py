from django.urls import path

from . import views

app_name = "course"

urlpatterns = [
    path("course/", views.lesson_list, name="lesson_list"),
    path("course/<slug:slug>/", views.lesson_detail, name="lesson_detail"),
]