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
    # Modul Booking Cek Unit
    path('panel/booking/', views.admin_booking, name='admin_booking'),
    path('panel/booking/<int:id>/', views.admin_booking_detail, name='admin_booking_detail'),
    path('panel/booking/<int:id>/status/', views.admin_booking_status, name='admin_booking_status'),
    path('panel/profil/', views.profil_admin, name='profil_admin'),
    path('panel/ubah-password/', views.ubah_password_admin, name='ubah_password_admin'),

    # CMS Artikel — Admin
    path('panel/artikel/', views.admin_artikel, name='admin_artikel'),
    path('panel/artikel/tambah/', views.admin_artikel_tambah, name='admin_artikel_tambah'),
    path('panel/artikel/<int:id>/edit/', views.admin_artikel_edit, name='admin_artikel_edit'),
    path('panel/artikel/<int:id>/hapus/', views.admin_artikel_hapus, name='admin_artikel_hapus'),
    path('panel/artikel/<int:id>/preview/', views.admin_artikel_preview, name='admin_artikel_preview'),
    path('panel/kategori-artikel/', views.admin_kategori_artikel, name='admin_kategori_artikel'),
    path('panel/kategori-artikel/tambah/', views.admin_kategori_tambah, name='admin_kategori_tambah'),
    path('panel/kategori-artikel/<int:id>/edit/', views.admin_kategori_edit, name='admin_kategori_edit'),
    path('panel/kategori-artikel/<int:id>/hapus/', views.admin_kategori_hapus, name='admin_kategori_hapus'),

    # CMS Artikel — Publik
    path('artikel/', views.artikel_publik, name='artikel_publik'),
    path('artikel/<slug:slug>/', views.artikel_detail, name='artikel_detail'),

    # Halaman Website — Admin
    path('panel/halaman/', views.admin_halaman, name='admin_halaman'),
    path('panel/halaman/tambah/', views.admin_halaman_tambah, name='admin_halaman_tambah'),
    path('panel/halaman/<int:id>/edit/', views.admin_halaman_edit, name='admin_halaman_edit'),
    path('panel/halaman/<int:id>/hapus/', views.admin_halaman_hapus, name='admin_halaman_hapus'),
    path('panel/halaman/<int:id>/preview/', views.admin_halaman_preview, name='admin_halaman_preview'),

    # Halaman Website — Publik
    path('halaman/<slug:slug>/', views.halaman_detail, name='halaman_detail'),
]
