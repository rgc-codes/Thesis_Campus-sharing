from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', views.catalog_view, name='catalog'),
    path('reserve/<int:item_id>/', views.reserve_item_view, name='reserve_item'),
    path('dashboard/', views.student_dashboard_view, name='student_dashboard'),
    path('management/dashboard/', views.admin_dashboard_view, name='admin_dashboard'),
    path('management/circulation-log/', views.circulation_log_view, name='circulation_log'),
    path('management/reservation/<int:pk>/approve/', views.approve_reservation, name='approve_reservation'),
    path('management/reservation/<int:pk>/checkout/', views.checkout_reservation, name='checkout_reservation'),
    path('management/reservation/<int:pk>/reject/', views.reject_reservation, name='reject_reservation'),
    path('management/reservation/<int:pk>/return/', views.mark_returned, name='return_item'),
]
