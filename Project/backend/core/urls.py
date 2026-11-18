from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('dispatch/', views.login_dispatch_view, name='login_dispatch'),
    
    # Public / Student Catalog & Reservations
    path('', views.catalog_view, name='catalog'),
    path('reserve/<int:item_id>/', views.reserve_item_view, name='reserve_item'),
    path('dashboard/', views.student_dashboard_view, name='student_dashboard'),
    path('dashboard/reservation/<int:pk>/cancel/', views.cancel_reservation_view, name='cancel_reservation'),
    
    # Manager Dedicated Console
    path('manager/dashboard/', views.manager_dashboard_view, name='manager_dashboard'),
    path('manager/circulation-logs/', views.circulation_log_view, name='circulation_log'),
    path('manager/reservation/<int:pk>/approve/', views.approve_reservation_view, name='approve_reservation'),
    path('manager/reservation/<int:pk>/checkout/', views.checkout_reservation_view, name='checkout_reservation'),
    path('manager/reservation/<int:pk>/reject/', views.reject_reservation_view, name='reject_reservation'),
    path('manager/reservation/<int:pk>/return/', views.return_item_view, name='return_item'),
    
    # Admin Dedicated System Information Hub & Backend Telemetry
    path('admin-system/dashboard/', views.admin_system_dashboard_view, name='admin_system_dashboard'),
    path('admin-system/logs/', views.admin_system_logs_view, name='admin_system_logs'),

    # Backward compatibility redirect
    path('management/dashboard/', views.admin_dashboard_view, name='admin_dashboard'),
]
