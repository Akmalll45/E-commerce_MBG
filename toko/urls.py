from django.urls import path
from . import views

urlpatterns = [
    # Publik
    path('', views.beranda, name='beranda'),
    path('jual-titip/', views.jual_titip, name='jual_titip'),
    path('katalog/', views.katalog, name='katalog'),
    path('mobil/<int:id>/', views.detail_produk, name='detail_produk'),
    path('mobil/<int:id>/inquiry/', views.kirim_inquiry_c2c, name='kirim_inquiry_c2c'),
    path('mobil/<int:id>/status-ketersediaan/', views.status_ketersediaan_unit, name='status_ketersediaan_unit'),

    # Autentikasi
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),

    # Dashboard Pengguna
    path('dashboard/', views.dashboard_user, name='dashboard_user'),
    path('dashboard/profil/', views.profil_user, name='profil_user'),
    path('dashboard/ubah-password/', views.ubah_password_user, name='ubah_password_user'),
    path('dashboard/titip-jual/', views.titip_jual_daftar, name='titip_jual_daftar'),
    path('dashboard/titip-jual/tambah/', views.titip_jual_tambah, name='titip_jual_tambah'),
    path('dashboard/titip-jual/<int:pengajuan_id>/', views.titip_jual_detail, name='titip_jual_detail'),
    path('dashboard/titip-jual/<int:pengajuan_id>/ubah/', views.titip_jual_ubah, name='titip_jual_ubah'),
    path(
        'dashboard/titip-jual/<int:pengajuan_id>/tindakan/',
        views.titip_jual_tindakan_pemilik,
        name='titip_jual_tindakan',
    ),
    path(
        'dokumen-titip-jual/<int:dokumen_id>/',
        views.dokumen_titip_jual_unduh,
        name='dokumen_titip_jual_download',
    ),
    path(
        'serah-terima-titip-jual/<int:serah_terima_id>/foto/',
        views.serah_terima_foto_unduh,
        name='serah_terima_foto_download',
    ),
    path(
        'pembayaran-pesanan/<int:pembayaran_id>/bukti-transfer/',
        views.bukti_transfer_pesanan_lihat,
        name='bukti_transfer_pesanan_lihat',
    ),

    # Dashboard Admin
    path('panel/', views.dashboard_admin, name='dashboard_admin'),
    path('panel/pengguna/', views.admin_pengguna, name='admin_pengguna'),
    path('panel/pengguna/tambah/', views.admin_pengguna_tambah, name='admin_pengguna_tambah'),
    path('panel/pengguna/<int:pengguna_id>/', views.admin_pengguna_detail, name='admin_pengguna_detail'),
    path('panel/pengguna/<int:pengguna_id>/edit/', views.admin_pengguna_edit, name='admin_pengguna_edit'),
    path('panel/pengguna/<int:pengguna_id>/status/', views.admin_pengguna_status, name='admin_pengguna_status'),
    path('panel/titip-jual/', views.admin_titip_jual, name='admin_titip_jual'),
    path(
        'panel/titip-jual/tahap/<slug:tahap>/',
        views.admin_titip_jual_tahap,
        name='admin_titip_jual_tahap',
    ),
    path(
        'panel/titip-jual/<int:pengajuan_id>/',
        views.admin_titip_jual_detail,
        name='admin_titip_jual_detail',
    ),
    path(
        'panel/titip-jual/<int:pengajuan_id>/tindakan/',
        views.admin_titip_jual_tindakan,
        name='admin_titip_jual_tindakan',
    ),
    path('panel/inquiry-c2c/', views.admin_inquiry_c2c, name='admin_inquiry_c2c'),
    path(
        'panel/inquiry-c2c/<int:inquiry_id>/status/',
        views.admin_inquiry_c2c_status,
        name='admin_inquiry_c2c_status',
    ),
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
    path('panel/pesanan-pembelian/', views.admin_pesanan_pembelian, name='admin_pesanan_pembelian'),
    path(
        'panel/pesanan-pembelian/pembayaran/<int:pembayaran_id>/verifikasi/',
        views.admin_verifikasi_pembayaran_pesanan,
        name='admin_verifikasi_pembayaran_pesanan',
    ),
    path(
        'panel/pesanan-pembelian/<int:pesanan_id>/pembayaran/catat/',
        views.admin_catat_pembayaran_pesanan,
        name='admin_catat_pembayaran_pesanan',
    ),
    path(
        'panel/pesanan-pembelian/pembayaran/<int:pembayaran_id>/tolak/',
        views.admin_tolak_pembayaran_pesanan,
        name='admin_tolak_pembayaran_pesanan',
    ),
    path(
        'panel/pesanan-pembelian/refund/<int:refund_id>/setujui/',
        views.admin_setujui_refund_pesanan,
        name='admin_setujui_refund_pesanan',
    ),
    path(
        'panel/pesanan-pembelian/refund/<int:refund_id>/tolak/',
        views.admin_tolak_refund_pesanan,
        name='admin_tolak_refund_pesanan',
    ),
    path(
        'panel/pesanan-pembelian/refund/<int:refund_id>/catat-pengembalian/',
        views.admin_catat_pengembalian_refund,
        name='admin_catat_pengembalian_refund',
    ),
    path(
        'panel/pesanan-pembelian/<int:pesanan_id>/selesaikan/',
        views.admin_selesaikan_pesanan_pembelian,
        name='admin_selesaikan_pesanan_pembelian',
    ),
    path(
        'panel/pesanan-pembelian/<int:pesanan_id>/tinjau-kredit/',
        views.admin_tinjau_pengajuan_kredit,
        name='admin_tinjau_pengajuan_kredit',
    ),
    path(
        'panel/pesanan-pembelian/<int:pesanan_id>/batalkan/',
        views.admin_batalkan_pesanan_pembelian,
        name='admin_batalkan_pesanan_pembelian',
    ),
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

    # Hero Slide — Admin
    path('panel/hero/', views.admin_hero, name='admin_hero'),
    path('panel/hero/tambah/', views.admin_hero_tambah, name='admin_hero_tambah'),
    path('panel/hero/<int:id>/edit/', views.admin_hero_edit, name='admin_hero_edit'),
    path('panel/hero/<int:id>/status/', views.admin_hero_status, name='admin_hero_status'),
    path('panel/hero/<int:id>/hapus/', views.admin_hero_hapus, name='admin_hero_hapus'),

    # Kerja Sama / Mitra — Admin
    path('panel/mitra/', views.admin_mitra, name='admin_mitra'),
    path('panel/mitra/tambah/', views.admin_mitra_tambah, name='admin_mitra_tambah'),
    path('panel/mitra/<int:id>/edit/', views.admin_mitra_edit, name='admin_mitra_edit'),
    path('panel/mitra/<int:id>/status/', views.admin_mitra_status, name='admin_mitra_status'),
    path('panel/mitra/<int:id>/hapus/', views.admin_mitra_hapus, name='admin_mitra_hapus'),

    # Pengaturan Website — Admin
    path('panel/pengaturan/', views.admin_pengaturan, name='admin_pengaturan'),

    # Customer Purchase Flow (Sprint 2)
    path('mobil/<int:id>/beli/', views.checkout_pembelian, name='beli_mobil'),
    path('pembelian/<int:id>/checkout/', views.checkout_pembelian, name='checkout_pembelian'),
    path('pembelian/<int:id>/konfirmasi/', views.konfirmasi_pembelian, name='konfirmasi_pembelian'),
    path('pembelian/pesanan/<str:nomor_pesanan>/berhasil/', views.pesanan_berhasil, name='pesanan_berhasil'),
    path('pembelian/pesanan/<str:nomor_pesanan>/', views.pesanan_detail, name='pesanan_detail'),
    path(
        'pembelian/pesanan/<str:nomor_pesanan>/konfirmasi-transfer/',
        views.konfirmasi_transfer_pesanan,
        name='konfirmasi_transfer_pesanan',
    ),

    # Midtrans Snap Sandbox (Sprint 3)
    path('pembelian/pesanan/<str:nomor_pesanan>/snap-token/', views.midtrans_snap_token, name='midtrans_snap_token'),
    path('pembelian/pesanan/<str:nomor_pesanan>/payment-status/', views.midtrans_payment_status, name='midtrans_payment_status'),
    path('pembelian/webhook/midtrans/', views.midtrans_webhook, name='midtrans_webhook'),
]
