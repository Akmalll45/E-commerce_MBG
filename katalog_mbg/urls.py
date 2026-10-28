from django.contrib import admin
from django.urls import path
from django.conf import settings 
from django.conf.urls.static import static 

from toko import views

urlpatterns = [
    # Django Admin Site Bawaan
    path('admin/', admin.site.urls),

    # Publik: Beranda & Detail Mobil
    path('', views.beranda, name='beranda'),
    path('mobil/<int:id>/', views.detail_produk, name='detail_produk'),

    # Autentikasi MBG
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),

    # Dashboard Pelanggan (Customer)
    path('dashboard/', views.dashboard_user, name='dashboard_user'),
    path('dashboard/profil/', views.profil_user, name='profil_user'),
    path('dashboard/ubah-password/', views.ubah_password_user, name='ubah_password_user'),

    # Dashboard Frontend Administrator (MBG Panel)
    path('panel/', views.dashboard_admin, name='dashboard_admin'),
    path('panel/profil/', views.profil_admin, name='profil_admin'),
    path('panel/ubah-password/', views.ubah_password_admin, name='ubah_password_admin'),
]

# Konfigurasi pembacaan file media (foto mobil) saat server berjalan di mode DEBUG
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
