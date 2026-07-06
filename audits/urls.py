from django.urls import path
from . import views

urlpatterns = [
    path("", views.audit_list, name="audit_list"),
    path("sites/<int:site_id>/run/", views.run_audit, name="audit_run"),
    path("sites/<int:site_id>/result/", views.audit_result, name="audit_result"),
    path("sites/<int:site_id>/result/pdf/", views.audit_result_pdf, name="audit_result_pdf"),
]