from django.urls import path
from . import views

urlpatterns = [
    # Publik
    path('', views.beranda, name='beranda'),
    path('katalog/', views.katalog, name='katalog'),
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
    path('panel/katalog/', views.admin_katalog, name='admin_katalog'),
    path('panel/katalog/tambah/', views.admin_katalog_tambah, name='admin_katalog_tambah'),
    path('panel/katalog/<int:id>/edit/', views.admin_katalog_edit, name='admin_katalog_edit'),
    path('panel/katalog/<int:id>/status/', views.admin_katalog_status, name='admin_katalog_status'),
    path('panel/katalog/<int:id>/hapus/', views.admin_katalog_hapus, name='admin_katalog_hapus'),
    path('panel/katalog/<int:id>/galeri/tambah/', views.admin_galeri_tambah, name='admin_galeri_tambah'),
    path('panel/katalog/<int:id>/galeri/<int:foto_id>/hapus/', views.admin_galeri_hapus, name='admin_galeri_hapus'),
    path('panel/profil/', views.profil_admin, name='profil_admin'),
    path('panel/ubah-password/', views.ubah_password_admin, name='ubah_password_admin'),
]
