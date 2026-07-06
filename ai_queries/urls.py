from django.urls import path
from ai_queries import views

app_name = 'ai_queries'

urlpatterns = [
    path('', views.analyze_view, name='analyze'),
    path('history/', views.history_view, name='history'),
    path('history/<int:pk>/', views.history_detail_view, name='history_detail'),
    path('history/<int:pk>/visibility/', views.check_visibility_view, name='check_visibility'),
    path('history/clear/', views.history_clear_view, name='history_clear'),
    path('history/<int:pk>/visibility/single/', views.check_single_visibility_view, name='check_single_visibility'),
    path('geo/', views.geo_audit_view, name='geo_audit'),
    path('geo/<int:pk>/', views.geo_audit_status_view, name='geo_audit_status'),
    path('geo/<int:pk>/poll/', views.geo_audit_poll_view, name='geo_audit_poll'),
    path('geo/<int:pk>/pdf/', views.geo_audit_pdf_view, name='geo_audit_pdf'),
]