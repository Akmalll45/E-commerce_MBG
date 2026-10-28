from django.urls import path
from . import views

urlpatterns = [
    # Publik
    path('', views.beranda, name='beranda'),
    path('mobil/<int:id>/', views.detail_produk, name='detail_produk'),

    # Autentikasi
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),

    # Dashboard Pengguna
    path('dashboard/', views.dashboard_user, name='dashboard_user'),
    path('dashboard/profil/', views.profil_user, name='profil_user'),
    path('dashboard/ubah-password/', views.ubah_password_user, name='ubah_password_user'),

    # Dashboard Admin
    path('panel/', views.dashboard_admin, name='dashboard_admin'),
    path('panel/profil/', views.profil_admin, name='profil_admin'),
    path('panel/ubah-password/', views.ubah_password_admin, name='ubah_password_admin'),
]
