from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import DatabaseError, transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from functools import wraps
from urllib.parse import urlencode

from .models import (
    Produk, FotoMobil, TitipJual, BookingCekUnit, Transaksi,
    KategoriArtikel, Artikel, HalamanWebsite, HeroSlide, Mitra, PengaturanWebsite,
)
from .forms import (
    LoginForm, RegisterForm, UserProfileForm, AdminProfileForm, UbahPasswordForm,
    ProdukForm, validasi_foto_mobil,
    KategoriArtikelForm, ArtikelForm, HalamanWebsiteForm, HeroSlideForm, MitraForm, PengaturanWebsiteForm,
)

User = get_user_model()


# ═══════════════════════════════════════════════════════════════════
# 1. HALAMAN PUBLIK
# ═══════════════════════════════════════════════════════════════════

JUMLAH_MOBIL_PILIHAN = 6      # jumlah mobil pilihan di beranda
JUMLAH_MAKS_SLIDE = 6           # jumlah maksimal slide hero yang tampil di beranda
JUMLAH_MAKS_MITRA = 12          # jumlah maksimal mitra yang tampil di beranda
KATALOG_PER_HALAMAN = 9       # jumlah kartu per halaman di katalog

# Aturan bisnis "tayang untuk publik": hanya unit berstatus Ready Stock
# (sama dengan aturan yang sudah dipakai beranda dan "mobil terkait").
STATUS_TAYANG_PUBLIK = 'ready'


def produk_tayang():
    """Queryset dasar semua unit yang boleh dilihat publik."""
    return Produk.objects.filter(status_stok=STATUS_TAYANG_PUBLIK)


def hero_slide_tayang():
    """
    Slide hero berstatus Tayang untuk beranda, terurut menurut kolom Urutan.

    Sengaja tahan galat (sama seperti context processor `halaman_footer`): jika tabel
    belum dimigrasi, beranda jatuh ke hero statis bawaan dan tidak ikut error 500.
    """
    try:
        return list(
            HeroSlide.objects.filter(status='published').order_by('urutan', 'id')[:JUMLAH_MAKS_SLIDE]
        )
    except DatabaseError:
        return []


def mitra_tayang():
    """
    Mitra berstatus Tayang untuk beranda, terurut menurut kolom Urutan.

    Tahan galat seperti `hero_slide_tayang`: jika tabel belum dimigrasi, bagian mitra
    cukup disembunyikan dan beranda tidak ikut error 500.
    """
    try:
        return list(
            Mitra.objects.filter(status='published').order_by('urutan', 'nama')[:JUMLAH_MAKS_MITRA]
        )
    except DatabaseError:
        return []


def beranda(request):
    """
    Beranda: hanya menampilkan beberapa mobil pilihan (unit Ready Stock terbaru).
    Daftar lengkap, pencarian, dan filter ada di halaman katalog.
    """
    semua_tayang = produk_tayang()
    katalog_mobil = semua_tayang.order_by('-created_at', '-id')[:JUMLAH_MOBIL_PILIHAN]

    return render(request, 'beranda.html', {
        'katalog_mobil': katalog_mobil,
        'total_unit_tayang': semua_tayang.count(),
        'hero_slides': hero_slide_tayang(),
        'mitra_list': mitra_tayang(),
    })


# ── Konfigurasi katalog (dipakai untuk validasi parameter GET) ───────────────
PRESET_HARGA = {
    # kunci: (label, harga_min [>=], harga_max [<])
    'lt150':   ('Di bawah Rp 150 Juta', None,        150_000_000),
    '150-250': ('Rp 150 – 250 Juta',    150_000_000, 250_000_000),
    '250-350': ('Rp 250 – 350 Juta',    250_000_000, 350_000_000),
    'gt350':   ('Di atas Rp 350 Juta',  350_000_000, None),
}

PRESET_KM = {
    # kunci (km maksimum): label
    '25000':  'Maks. 25.000 km',
    '50000':  'Maks. 50.000 km',
    '75000':  'Maks. 75.000 km',
    '100000': 'Maks. 100.000 km',
}

PILIHAN_URUTAN = {
    # kunci: (label, urutan ORM). "-id" menjaga urutan stabil antar halaman.
    'terbaru':    ('Terbaru Ditambahkan',    ('-created_at', '-id')),
    'harga_asc':  ('Harga: Rendah → Tinggi', ('harga', '-id')),
    'harga_desc': ('Harga: Tinggi → Rendah', ('-harga', '-id')),
    'tahun_desc': ('Tahun: Paling Baru',     ('-tahun', '-created_at', '-id')),
}
URUTAN_DEFAULT = 'terbaru'

TAHUN_MIN_VALID = 1980
MAKS_KATA_KUNCI = 5
MAKS_PANJANG_KATA = 50


def _param_int(request, kunci, minimum, maksimum):
    """Ambil parameter GET berupa bilangan bulat dalam rentang; selain itu None."""
    mentah = request.GET.get(kunci, '').strip()
    try:
        nilai = int(mentah)
    except (TypeError, ValueError):
        return None
    return nilai if minimum <= nilai <= maksimum else None


def katalog(request):
    """
    Katalog publik: seluruh unit tayang + pencarian, filter, pengurutan, pagination.
    Semua parameter GET divalidasi; nilai tidak valid diabaikan (tidak error).
    """
    dasar = produk_tayang()
    hasil = dasar

    # Opsi pilihan untuk dropdown berasal dari data tayang yang sebenarnya
    daftar_merek = list(dasar.order_by('merek').values_list('merek', flat=True).distinct())
    daftar_kategori = list(dasar.order_by('kategori').values_list('kategori', flat=True).distinct())

    params = {}  # hanya parameter VALID; dipakai untuk form, chip filter, dan pagination

    # 1. Kata kunci: setiap kata harus cocok di salah satu kolom (AND antar kata)
    q = ' '.join(request.GET.get('q', '').split())[:200]
    if q:
        params['q'] = q
        for kata in q.split(' ')[:MAKS_KATA_KUNCI]:
            kata = kata[:MAKS_PANJANG_KATA]
            hasil = hasil.filter(
                Q(nama__icontains=kata) | Q(merek__icontains=kata)
                | Q(kategori__icontains=kata) | Q(warna__icontains=kata)
            )

    # 2. Merek & kategori: hanya nilai yang memang ada di data tayang
    merek = request.GET.get('merek', '')
    if merek in daftar_merek:
        params['merek'] = merek
        hasil = hasil.filter(merek=merek)

    kategori = request.GET.get('kategori', '')
    if kategori in daftar_kategori:
        params['kategori'] = kategori
        hasil = hasil.filter(kategori=kategori)

    # 3. Transmisi & tipe penjual (stok MBG B2C / titip-jual C2C)
    transmisi = request.GET.get('transmisi', '')
    if transmisi in dict(Produk.TRANSMISI_CHOICES):
        params['transmisi'] = transmisi
        hasil = hasil.filter(transmisi=transmisi)

    tipe = request.GET.get('tipe', '')
    if tipe in dict(Produk.TIPE_PENJUAL_CHOICES):
        params['tipe'] = tipe
        hasil = hasil.filter(tipe_penjual=tipe)

    # 4. Rentang harga (preset)
    harga = request.GET.get('harga', '')
    if harga in PRESET_HARGA:
        params['harga'] = harga
        _, harga_min, harga_max = PRESET_HARGA[harga]
        if harga_min is not None:
            hasil = hasil.filter(harga__gte=harga_min)
        if harga_max is not None:
            hasil = hasil.filter(harga__lt=harga_max)

    # 5. Rentang tahun (jika terbalik, ditukar otomatis)
    tahun_maks_valid = timezone.localdate().year + 1
    tahun_min = _param_int(request, 'tahun_min', TAHUN_MIN_VALID, tahun_maks_valid)
    tahun_max = _param_int(request, 'tahun_max', TAHUN_MIN_VALID, tahun_maks_valid)
    if tahun_min and tahun_max and tahun_min > tahun_max:
        tahun_min, tahun_max = tahun_max, tahun_min
    if tahun_min:
        params['tahun_min'] = tahun_min
        hasil = hasil.filter(tahun__gte=tahun_min)
    if tahun_max:
        params['tahun_max'] = tahun_max
        hasil = hasil.filter(tahun__lte=tahun_max)

    # 6. Kilometer maksimum (preset)
    km = request.GET.get('km', '')
    if km in PRESET_KM:
        params['km'] = km
        hasil = hasil.filter(jarak_tempuh__lte=int(km))

    # 7. Pengurutan
    urutan = request.GET.get('urutan', URUTAN_DEFAULT)
    if urutan not in PILIHAN_URUTAN:
        urutan = URUTAN_DEFAULT
    if urutan != URUTAN_DEFAULT:
        params['urutan'] = urutan
    hasil = hasil.order_by(*PILIHAN_URUTAN[urutan][1])

    # 8. Pagination (nomor halaman tidak valid → halaman pertama/terakhir)
    paginator = Paginator(hasil, KATALOG_PER_HALAMAN)
    page_obj = paginator.get_page(request.GET.get('page'))
    rentang_halaman = paginator.get_elided_page_range(
        page_obj.number, on_each_side=1, on_ends=1
    )

    # Chip "filter aktif": tiap chip membawa URL untuk menghapus filter itu saja
    label_filter = {
        'q': lambda v: f'Kata kunci: “{v}”',
        'merek': lambda v: f'Merek: {v}',
        'kategori': lambda v: f'Kategori: {v}',
        'transmisi': lambda v: dict(Produk.TRANSMISI_CHOICES)[v],
        'tipe': lambda v: dict(Produk.TIPE_PENJUAL_CHOICES)[v],
        'harga': lambda v: PRESET_HARGA[v][0],
        'tahun_min': lambda v: f'Tahun ≥ {v}',
        'tahun_max': lambda v: f'Tahun ≤ {v}',
        'km': lambda v: PRESET_KM[v],
    }
    filter_aktif = []
    for kunci, buat_label in label_filter.items():
        if kunci in params:
            sisa = {k: v for k, v in params.items() if k != kunci}
            filter_aktif.append({
                'label': buat_label(params[kunci]),
                'url': '?' + urlencode(sisa) if sisa else request.path,
            })

    return render(request, 'katalog.html', {
        'page_obj': page_obj,
        'mobil_list': page_obj.object_list,
        'total_hasil': paginator.count,
        'rentang_halaman': rentang_halaman,
        'ELLIPSIS': Paginator.ELLIPSIS,
        'querystring': urlencode(params),   # tanpa "page"
        'nilai': params,                    # nilai form yang sedang aktif
        'urutan_aktif': urutan,
        'filter_aktif': filter_aktif,
        'jumlah_filter_aktif': len([k for k in params if k not in ('q', 'urutan')]),
        'ada_pencarian': bool(params.keys() - {'urutan'}),
        'daftar_merek': daftar_merek,
        'daftar_kategori': daftar_kategori,
        'pilihan_transmisi': Produk.TRANSMISI_CHOICES,
        'pilihan_tipe': Produk.TIPE_PENJUAL_CHOICES,
        'pilihan_harga': [(k, v[0]) for k, v in PRESET_HARGA.items()],
        'pilihan_km': list(PRESET_KM.items()),
        'pilihan_urutan': [(k, v[0]) for k, v in PILIHAN_URUTAN.items()],
        'total_unit_tayang': dasar.count(),
    })


def detail_produk(request, id):
    """
    Menampilkan halaman detail spesifikasi lengkap mobil MBG.
    """
    produk = get_object_or_404(Produk, id=id)
    mobil_terkait = Produk.objects.filter(status_stok='ready').exclude(id=id)[:3]

    return render(request, 'detail_mobil.html', {
        'produk': produk,
        'mobil': produk,
        'mobil_terkait': mobil_terkait,
    })


# ═══════════════════════════════════════════════════════════════════
# 2. AUTENTIKASI: LOGIN, REGISTER, LOGOUT
# ═══════════════════════════════════════════════════════════════════

def login_view(request):
    """
    Halaman Login Pengguna & Admin MBG.
    Mendukung username atau email, dan redirect otomatis berdasarkan peran akun.
    """
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return redirect('dashboard_admin')
        return redirect('dashboard_user')

    next_url = request.GET.get('next') or request.POST.get('next') or ''

    if request.method == 'POST':
        form = LoginForm(request=request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.success(request, f"Selamat datang kembali, {user.first_name or user.username}!")
            
            # Pengalihan cerdas berdasarkan role & parameter next
            if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
                return redirect(next_url)
            
            if user.is_staff or user.is_superuser:
                return redirect('dashboard_admin')
            return redirect('dashboard_user')
        else:
            messages.error(request, "Gagal masuk. Silakan periksa kembali username/email dan kata sandi Anda.")
    else:
        form = LoginForm(request=request)

    return render(request, 'auth/login.html', {
        'form': form,
        'next': next_url,
    })


def register_view(request):
    """
    Halaman Registrasi Akun Pelanggan MBG.
    Mendaftar sebagai pengguna biasa (bukan staf/admin).
    """
    if request.user.is_authenticated:
        if request.user.is_staff or request.user.is_superuser:
            return redirect('dashboard_admin')
        return redirect('dashboard_user')

    if request.method == 'POST':
        form = RegisterForm(data=request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(
                request,
                f"Pendaftaran berhasil! Akun '{user.username}' telah aktif. Silakan masuk dengan akun Anda."
            )
            return redirect('login')
        else:
            messages.error(request, "Terjadi kesalahan pada data registrasi. Mohon periksa kembali kolom formulir.")
    else:
        form = RegisterForm()

    return render(request, 'auth/register.html', {
        'form': form,
    })


def logout_view(request):
    """
    Logout dan akhiri sesi dengan aman, lalu arahkan kembali ke beranda.
    """
    logout(request)
    messages.info(request, "Anda telah berhasil keluar dari akun.")
    return redirect('beranda')


# ═══════════════════════════════════════════════════════════════════
# 3. DASHBOARD PENGGUNA (CUSTOMER)
# ═══════════════════════════════════════════════════════════════════

@login_required(login_url='login')
def dashboard_user(request):
    """
    Dashboard Pelanggan MBG:
    Menampilkan data akun dan aktivitas pribadi (Booking, Titip-Jual, Transaksi).
    """
    user = request.user

    # Kueri aktivitas terkait user berdasarkan email atau no_whatsapp
    filter_q = Q()
    if user.email:
        filter_q |= Q(email__iexact=user.email)
    if user.no_whatsapp:
        filter_q |= Q(no_whatsapp=user.no_whatsapp)

    if filter_q:
        user_bookings = BookingCekUnit.objects.filter(filter_q).select_related('produk').order_by('-created_at')
        user_titip_jual = TitipJual.objects.filter(filter_q).order_by('-created_at')
        user_transaksi = Transaksi.objects.filter(booking__in=user_bookings).select_related('booking', 'booking__produk').order_by('-created_at')
    else:
        user_bookings = BookingCekUnit.objects.none()
        user_titip_jual = TitipJual.objects.none()
        user_transaksi = Transaksi.objects.none()

    context = {
        'user': user,
        'user_bookings': user_bookings,
        'user_titip_jual': user_titip_jual,
        'user_transaksi': user_transaksi,
        'total_booking': user_bookings.count(),
        'total_titip_jual': user_titip_jual.count(),
        'total_transaksi': user_transaksi.count(),
    }
    return render(request, 'dashboard/user/dashboard.html', context)


@login_required(login_url='login')
def profil_user(request):
    """
    Update data profil pengguna (Foto, Nama, Email, WhatsApp, Alamat).
    """
    user = request.user
    if request.method == 'POST':
        form = UserProfileForm(request.POST, request.FILES, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profil Anda berhasil diperbarui.")
            return redirect('profil_user')
        else:
            # Validasi sudah mengisi instance dengan input yang belum valid (termasuk file
            # yang belum tersimpan). Pulihkan dari database agar sidebar tidak menampilkan
            # foto/nama yang belum tersimpan; isian form tetap berasal dari data POST.
            user.refresh_from_db()
            messages.error(request, "Gagal memperbarui profil. Silakan periksa pesan kesalahan pada formulir.")
    else:
        form = UserProfileForm(instance=user)

    return render(request, 'dashboard/user/profil.html', {
        'form': form,
        'user': user,
    })


@login_required(login_url='login')
def ubah_password_user(request):
    """
    Ganti kata sandi pengguna dengan validasi keamanan Django.
    """
    user = request.user
    if request.method == 'POST':
        form = UbahPasswordForm(user=user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)
            messages.success(request, "Kata sandi Anda berhasil diperbarui!")
            return redirect('dashboard_user')
        else:
            messages.error(request, "Gagal mengubah kata sandi. Periksa kecocokan kata sandi lama dan baru.")
    else:
        form = UbahPasswordForm(user=user)

    return render(request, 'dashboard/user/ubah_password.html', {
        'form': form,
        'user': user,
    })


# ═══════════════════════════════════════════════════════════════════
# 4. DASHBOARD ADMIN (MBG MANAGEMENT PANEL)
# ═══════════════════════════════════════════════════════════════════

# Kolom Pengaturan Website yang dihitung untuk "kelengkapan" di dashboard.
KOLOM_KELENGKAPAN_PENGATURAN = (
    'whatsapp', 'email', 'alamat', 'alamat_singkat', 'jam_buka', 'link_peta', 'deskripsi_footer',
    'instagram', 'facebook', 'tiktok', 'youtube', 'logo', 'favicon',
)


def ringkasan_konten():
    """
    Hitungan modul CMS (Artikel, Halaman, Hero Slide, Mitra, Pengaturan) untuk kartu KPI di
    dashboard admin. Mengembalikan None jika salah satu tabel belum dimigrasi, supaya
    dashboard tetap terbuka dan hanya bagian ini yang disembunyikan.
    """
    def hitung(model):
        return model.objects.aggregate(
            total=Count('id'),
            tayang=Count('id', filter=Q(status='published')),
            draft=Count('id', filter=Q(status='draft')),
        )

    try:
        pengaturan = PengaturanWebsite.objects.filter(pk=1).first()
        return {
            'artikel':  hitung(Artikel),
            'kategori': KategoriArtikel.objects.count(),
            'halaman':  hitung(HalamanWebsite),
            'hero':     hitung(HeroSlide),
            'mitra':    hitung(Mitra),
            'pengaturan_tersimpan': pengaturan is not None,
            'pengaturan_terisi': (
                sum(1 for kolom in KOLOM_KELENGKAPAN_PENGATURAN if getattr(pengaturan, kolom))
                if pengaturan else 0
            ),
            'pengaturan_total':  len(KOLOM_KELENGKAPAN_PENGATURAN),
            'logo_terpasang':    bool(pengaturan and pengaturan.logo),
            'favicon_terpasang': bool(pengaturan and pengaturan.favicon),
        }
    except DatabaseError:
        return None


@login_required(login_url='login')
def dashboard_admin(request):
    """
    Panel Dashboard Frontend Administrator MBG:
    Statistik bisnis riil dari database (Katalog, Pengguna, Booking, Titip-Jual, Transaksi).
    """
    # Proteksi hak akses di level backend
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, "Akses ditolak. Halaman Dashboard Admin hanya dapat diakses oleh staf atau pengelola MBG.")
        return redirect('dashboard_user')

    # 1. Statistik Katalog Kendaraan
    total_katalog = Produk.objects.count()
    stok_ready = Produk.objects.filter(status_stok='ready').count()
    stok_in_review = Produk.objects.filter(status_stok='in_review').count()
    stok_sold = Produk.objects.filter(status_stok='sold').count()

    # 2. Statistik Pengguna
    total_pengguna = User.objects.count()
    total_pelanggan = User.objects.filter(is_staff=False, is_superuser=False).count()

    # 3. Statistik Booking Cek Unit
    total_booking = BookingCekUnit.objects.count()
    booking_menunggu_dp = BookingCekUnit.objects.filter(status='menunggu_dp').count()
    booking_terkonfirmasi = BookingCekUnit.objects.filter(status='terkonfirmasi').count()

    # 4. Statistik Pengajuan Titip-Jual
    total_titip_jual = TitipJual.objects.count()
    titip_jual_pending = TitipJual.objects.filter(status='pending').count()
    titip_jual_disetujui = TitipJual.objects.filter(status='disetujui').count()

    # 5. Statistik Transaksi
    total_transaksi = Transaksi.objects.count()
    transaksi_berhasil = Transaksi.objects.filter(status_pembayaran='berhasil').count()
    transaksi_pending = Transaksi.objects.filter(status_pembayaran='pending').count()

    # Ringkasan data terbaru untuk quick monitoring
    recent_booking = BookingCekUnit.objects.select_related('produk').order_by('-created_at')[:5]
    recent_titip_jual = TitipJual.objects.order_by('-created_at')[:5]
    recent_transaksi = Transaksi.objects.select_related('booking', 'booking__produk').order_by('-created_at')[:5]
    recent_produk = Produk.objects.order_by('-created_at')[:5]

    context = {
        'total_katalog': total_katalog,
        'stok_ready': stok_ready,
        'stok_in_review': stok_in_review,
        'stok_sold': stok_sold,
        'total_pengguna': total_pengguna,
        'total_pelanggan': total_pelanggan,
        'total_booking': total_booking,
        'booking_menunggu_dp': booking_menunggu_dp,
        'booking_terkonfirmasi': booking_terkonfirmasi,
        'total_titip_jual': total_titip_jual,
        'titip_jual_pending': titip_jual_pending,
        'titip_jual_disetujui': titip_jual_disetujui,
        'total_transaksi': total_transaksi,
        'transaksi_berhasil': transaksi_berhasil,
        'transaksi_pending': transaksi_pending,
        'recent_booking': recent_booking,
        'recent_titip_jual': recent_titip_jual,
        'recent_transaksi': recent_transaksi,
        'recent_produk': recent_produk,
        'konten': ringkasan_konten(),
    }
    return render(request, 'dashboard/admin/dashboard.html', context)


@login_required(login_url='login')
def profil_admin(request):
    """
    Update data profil administrator MBG.
    """
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, "Akses ditolak. Halaman ini hanya untuk staf/admin MBG.")
        return redirect('dashboard_user')

    admin_user = request.user
    if request.method == 'POST':
        form = AdminProfileForm(request.POST, instance=admin_user)
        if form.is_valid():
            form.save()
            messages.success(request, "Profil admin berhasil diperbarui.")
            return redirect('profil_admin')
        else:
            messages.error(request, "Gagal memperbarui profil admin. Silakan periksa isian formulir.")
    else:
        form = AdminProfileForm(instance=admin_user)

    return render(request, 'dashboard/admin/profil.html', {
        'form': form,
        'user': admin_user,
    })


@login_required(login_url='login')
def ubah_password_admin(request):
    """
    Ganti kata sandi administrator MBG.
    """
    if not (request.user.is_staff or request.user.is_superuser):
        messages.error(request, "Akses ditolak.")
        return redirect('dashboard_user')

    admin_user = request.user
    if request.method == 'POST':
        form = UbahPasswordForm(user=admin_user, data=request.POST)
        if form.is_valid():
            form.save()
            update_session_auth_hash(request, form.user)
            messages.success(request, "Kata sandi admin berhasil diperbarui!")
            return redirect('dashboard_admin')
        else:
            messages.error(request, "Gagal memperbarui kata sandi admin.")
    else:
        form = UbahPasswordForm(user=admin_user)

    return render(request, 'dashboard/admin/ubah_password.html', {
        'form': form,
        'user': admin_user,
    })



# ═══════════════════════════════════════════════════════════════════
# 5. PANEL ADMIN — KELOLA KATALOG MOBIL (CRUD)
# ═══════════════════════════════════════════════════════════════════

ADMIN_KATALOG_PER_HALAMAN = 15
GALERI_MAKS_FOTO = 12            # total foto galeri per mobil
GALERI_MAKS_SEKALI_UNGGAH = 8    # jumlah foto per sekali unggah


def staf_required(view):
    """Hanya staf/superuser. Pengguna biasa diarahkan ke dashboard mereka (sama seperti panel yang sudah ada)."""
    @wraps(view)
    @login_required(login_url='login')
    def pembungkus(request, *args, **kwargs):
        if not (request.user.is_staff or request.user.is_superuser):
            messages.error(request, "Akses ditolak. Halaman ini hanya untuk staf atau pengelola MBG.")
            return redirect('dashboard_user')
        return view(request, *args, **kwargs)
    return pembungkus


@staf_required
def admin_katalog(request):
    """Daftar seluruh unit (semua status) + pencarian, filter status/sumber, pagination."""
    daftar = Produk.objects.annotate(jumlah_booking=Count('bookingcekunit')).order_by('-created_at', '-id')
    params = {}

    q = ' '.join(request.GET.get('q', '').split())[:100]
    if q:
        params['q'] = q
        for kata in q.split(' ')[:5]:
            daftar = daftar.filter(
                Q(nama__icontains=kata) | Q(sku__icontains=kata)
                | Q(merek__icontains=kata) | Q(plat_nomor__icontains=kata)
            )

    status = request.GET.get('status', '')
    if status in dict(Produk.STATUS_STOK_CHOICES):
        params['status'] = status
        daftar = daftar.filter(status_stok=status)

    tipe = request.GET.get('tipe', '')
    if tipe in dict(Produk.TIPE_PENJUAL_CHOICES):
        params['tipe'] = tipe
        daftar = daftar.filter(tipe_penjual=tipe)

    page_obj = Paginator(daftar, ADMIN_KATALOG_PER_HALAMAN).get_page(request.GET.get('page'))

    hitung = dict(Produk.objects.values_list('status_stok').annotate(n=Count('id')))
    tab_status = [('', 'Semua', sum(hitung.values()))] + [
        (kode, label, hitung.get(kode, 0)) for kode, label in Produk.STATUS_STOK_CHOICES
    ]

    return render(request, 'dashboard/admin/katalog_list.html', {
        'page_obj': page_obj,
        'produk_list': page_obj.object_list,
        'total_hasil': page_obj.paginator.count,
        'querystring': urlencode(params),
        'nilai': params,
        'tab_status': tab_status,
        'pilihan_tipe': Produk.TIPE_PENJUAL_CHOICES,
        'pilihan_status': Produk.STATUS_STOK_CHOICES,
        'total_katalog': hitung and sum(hitung.values()) or 0,
        'next_url': request.get_full_path(),
    })


def _simpan_form_produk(request, produk=None):
    """Logika bersama tambah & ubah. Mengembalikan (form, produk_tersimpan atau None)."""
    if request.method == 'POST':
        form = ProdukForm(request.POST, request.FILES, instance=produk)
        if form.is_valid():
            return form, form.save()
        messages.error(request, "Data belum bisa disimpan. Periksa kolom yang ditandai merah.")
        return form, None
    return ProdukForm(instance=produk), None


@staf_required
def admin_katalog_tambah(request):
    form, tersimpan = _simpan_form_produk(request)
    if tersimpan:
        messages.success(
            request,
            f"Unit “{tersimpan.nama}” berhasil ditambahkan ke katalog. "
            "Anda bisa menambahkan foto galeri di bawah."
        )
        return redirect('admin_katalog_edit', id=tersimpan.id)
    return render(request, 'dashboard/admin/katalog_form.html', {
        'form': form, 'produk': None, 'total_katalog': Produk.objects.count(),
    })


@staf_required
def admin_katalog_edit(request, id):
    produk = get_object_or_404(Produk, id=id)
    form, tersimpan = _simpan_form_produk(request, produk)
    if tersimpan:
        messages.success(request, f"Perubahan pada “{tersimpan.nama}” berhasil disimpan.")
        return redirect('admin_katalog')
    return render(request, 'dashboard/admin/katalog_form.html', {
        'form': form,
        'produk': produk,
        'galeri': produk.galeri_foto.all().order_by('id'),
        'galeri_maks': GALERI_MAKS_FOTO,
        'jumlah_booking': produk.bookingcekunit_set.count(),
        'total_katalog': Produk.objects.count(),
    })


@staf_required
@require_POST
def admin_katalog_status(request, id):
    """Ubah cepat status stok (Ready / In Review / Terjual) langsung dari daftar."""
    produk = get_object_or_404(Produk, id=id)
    status = request.POST.get('status_stok', '')
    if status not in dict(Produk.STATUS_STOK_CHOICES):
        messages.error(request, "Status stok tidak valid.")
    else:
        produk.status_stok = status
        produk.save(update_fields=['status_stok', 'updated_at'])
        messages.success(request, f"Status “{produk.nama}” diubah menjadi {produk.get_status_stok_display()}.")
    tujuan = request.POST.get('next', '')
    if tujuan and url_has_allowed_host_and_scheme(tujuan, allowed_hosts={request.get_host()}):
        return redirect(tujuan)
    return redirect('admin_katalog')


@staf_required
@require_POST
def admin_katalog_hapus(request, id):
    """
    Hapus unit. DIBLOKIR jika unit sudah punya booking: relasi booking → produk
    bersifat CASCADE, sehingga menghapus mobil akan ikut menghapus booking dan
    transaksi DP-nya. Untuk unit seperti itu gunakan status "Terjual".
    """
    produk = get_object_or_404(Produk, id=id)
    jumlah_booking = produk.bookingcekunit_set.count()
    if jumlah_booking:
        messages.error(
            request,
            f"“{produk.nama}” tidak bisa dihapus karena memiliki {jumlah_booking} booking/transaksi. "
            "Ubah statusnya menjadi “Terjual” agar tidak tampil di katalog tanpa menghilangkan riwayat."
        )
        return redirect('admin_katalog')

    nama = produk.nama
    berkas = [produk.gambar_utama.name] if produk.gambar_utama else []
    berkas += [f.gambar.name for f in produk.galeri_foto.all() if f.gambar]
    storage = produk.gambar_utama.storage
    with transaction.atomic():
        produk.delete()
        # File fisik dihapus hanya setelah data di database benar-benar terhapus.
        transaction.on_commit(lambda: [storage.delete(n) for n in berkas])
    messages.success(request, f"Unit “{nama}” telah dihapus dari katalog.")
    return redirect('admin_katalog')


@staf_required
@require_POST
def admin_galeri_tambah(request, id):
    """Unggah satu atau beberapa foto galeri tambahan untuk sebuah unit."""
    produk = get_object_or_404(Produk, id=id)
    berkas = request.FILES.getlist('gambar')
    keterangan = ' '.join(request.POST.get('keterangan', '').split())[:100]

    if not berkas:
        messages.error(request, "Pilih minimal satu foto untuk diunggah.")
        return redirect('admin_katalog_edit', id=produk.id)

    sisa = GALERI_MAKS_FOTO - produk.galeri_foto.count()
    if sisa <= 0:
        messages.error(request, f"Galeri sudah penuh (maksimal {GALERI_MAKS_FOTO} foto). Hapus foto lama dulu.")
        return redirect('admin_katalog_edit', id=produk.id)
    if len(berkas) > min(sisa, GALERI_MAKS_SEKALI_UNGGAH):
        messages.error(
            request,
            f"Terlalu banyak foto. Sekali unggah maksimal {min(sisa, GALERI_MAKS_SEKALI_UNGGAH)} foto "
            f"(sisa kuota galeri: {sisa})."
        )
        return redirect('admin_katalog_edit', id=produk.id)

    berhasil, gagal = 0, []
    for f in berkas:
        nama_asli = f.name
        try:
            validasi_foto_mobil(f)
        except ValidationError as e:
            gagal.append(f"{nama_asli}: {e.messages[0]}")
            continue
        FotoMobil.objects.create(produk=produk, gambar=f, keterangan=keterangan)
        berhasil += 1

    if berhasil:
        messages.success(request, f"{berhasil} foto galeri berhasil ditambahkan.")
    for pesan in gagal:
        messages.error(request, pesan)
    return redirect('admin_katalog_edit', id=produk.id)


@staf_required
@require_POST
def admin_galeri_hapus(request, id, foto_id):
    produk = get_object_or_404(Produk, id=id)
    foto = get_object_or_404(FotoMobil, id=foto_id, produk=produk)
    berkas = foto.gambar.name
    storage = foto.gambar.storage
    with transaction.atomic():
        foto.delete()
        transaction.on_commit(lambda: storage.delete(berkas))
    messages.success(request, "Foto galeri dihapus.")
    return redirect('admin_katalog_edit', id=produk.id)


# ═══════════════════════════════════════════════════════════════════
# 6. MODUL BOOKING CEK UNIT (ADMIN PANEL)
# ═══════════════════════════════════════════════════════════════════

ADMIN_BOOKING_PER_HALAMAN = 15


@staf_required
def admin_booking(request):
    """
    Daftar seluruh Booking Cek Unit MBG:
    Pencarian (nama pemesan, telepon, email, unit), filter status booking,
    filter opsi delivery, filter tanggal cek unit, pengurutan, pagination.
    """
    daftar = BookingCekUnit.objects.select_related('produk', 'transaksi').order_by('-created_at', '-id')
    params = {}

    # 1. Pencarian teks
    q = ' '.join(request.GET.get('q', '').split())[:100]
    if q:
        params['q'] = q
        daftar = daftar.filter(
            Q(nama_pemesan__icontains=q) |
            Q(no_whatsapp__icontains=q) |
            Q(email__icontains=q) |
            Q(produk__nama__icontains=q) |
            Q(produk__sku__icontains=q) |
            Q(produk__plat_nomor__icontains=q)
        )

    # 2. Filter Status Booking
    status = request.GET.get('status', '').strip()
    if status in dict(BookingCekUnit.STATUS_BOOKING):
        params['status'] = status
        daftar = daftar.filter(status=status)

    # 3. Filter Opsi Delivery
    opsi = request.GET.get('opsi', '').strip()
    if opsi in dict(BookingCekUnit.OPSI_DELIVERY):
        params['opsi'] = opsi
        daftar = daftar.filter(opsi_delivery=opsi)

    # 4. Filter Status Pembayaran
    pembayaran = request.GET.get('pembayaran', '').strip()
    if pembayaran in dict(Transaksi.STATUS_PEMBAYARAN):
        params['pembayaran'] = pembayaran
        daftar = daftar.filter(transaksi__status_pembayaran=pembayaran)

    # 5. Filter Rentang Tanggal Cek Unit
    tgl_dari = request.GET.get('tgl_dari', '').strip()
    if tgl_dari:
        params['tgl_dari'] = tgl_dari
        daftar = daftar.filter(tanggal_cek_unit__gte=tgl_dari)

    tgl_sampai = request.GET.get('tgl_sampai', '').strip()
    if tgl_sampai:
        params['tgl_sampai'] = tgl_sampai
        daftar = daftar.filter(tanggal_cek_unit__lte=tgl_sampai)

    # 6. Pagination
    page_obj = Paginator(daftar, ADMIN_BOOKING_PER_HALAMAN).get_page(request.GET.get('page'))

    # 7. Statistik & Tab Status
    total_booking = BookingCekUnit.objects.count()
    hitung_status = dict(BookingCekUnit.objects.values_list('status').annotate(n=Count('id')))
    tab_status = [('', 'Semua', total_booking)] + [
        (kode, label, hitung_status.get(kode, 0)) for kode, label in BookingCekUnit.STATUS_BOOKING
    ]

    context = {
        'page_obj': page_obj,
        'booking_list': page_obj.object_list,
        'total_hasil': page_obj.paginator.count,
        'querystring': urlencode(params),
        'nilai': params,
        'tab_status': tab_status,
        'pilihan_status': BookingCekUnit.STATUS_BOOKING,
        'pilihan_opsi': BookingCekUnit.OPSI_DELIVERY,
        'pilihan_pembayaran': Transaksi.STATUS_PEMBAYARAN,
        'total_booking': total_booking,
        'booking_menunggu_dp': hitung_status.get('menunggu_dp', 0),
        'booking_terkonfirmasi': hitung_status.get('terkonfirmasi', 0),
        'booking_selesai': hitung_status.get('selesai', 0),
        'booking_batal': hitung_status.get('batal', 0),
        'next_url': request.get_full_path(),
    }
    return render(request, 'dashboard/admin/booking_list.html', context)


@staf_required
def admin_booking_detail(request, id):
    """
    Halaman Detail Lengkap Booking Cek Unit:
    Menampilkan info pelanggan, kendaraan, jadwal booking, opsi delivery,
    transaksi/DP (jika ada), serta aksi kelola status.
    """
    booking = get_object_or_404(
        BookingCekUnit.objects.select_related('produk', 'transaksi'),
        id=id
    )

    # Cari akun user terdaftar yang sesuai email atau no_whatsapp
    user_terkait = None
    if booking.email or booking.no_whatsapp:
        filter_user = Q()
        if booking.email:
            filter_user |= Q(email__iexact=booking.email)
        if booking.no_whatsapp:
            filter_user |= Q(no_whatsapp=booking.no_whatsapp)
        user_terkait = User.objects.filter(filter_user).first()

    # Transaksi & pembayaran terkait
    transaksi = getattr(booking, 'transaksi', None)

    context = {
        'booking': booking,
        'produk': booking.produk,
        'transaksi': transaksi,
        'user_terkait': user_terkait,
        'pilihan_status': BookingCekUnit.STATUS_BOOKING,
    }
    return render(request, 'dashboard/admin/booking_detail.html', context)


@staf_required
@require_POST
def admin_booking_status(request, id):
    """
    Update Status Booking Cek Unit oleh Administrator:
    Validasi pilihan status di backend, POST-only, CSRF-protected.
    Tidak mengubah status pembayaran secara otomatis.
    """
    booking = get_object_or_404(BookingCekUnit, id=id)
    status_baru = request.POST.get('status', '').strip()
    status_dict = dict(BookingCekUnit.STATUS_BOOKING)

    if status_baru not in status_dict:
        messages.error(request, f"Status '{status_baru}' tidak valid.")
        return redirect('admin_booking_detail', id=booking.id)

    status_lama = booking.status
    if status_baru != status_lama:
        booking.status = status_baru
        booking.save(update_fields=['status'])
        messages.success(
            request,
            f"Status Booking #{booking.id} berhasil diperbarui dari '{status_dict.get(status_lama)}' menjadi '{status_dict.get(status_baru)}'."
        )
    else:
        messages.info(request, "Status tidak mengalami perubahan.")

    next_url = request.POST.get('next')
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
        return redirect(next_url)
    return redirect('admin_booking_detail', id=booking.id)



# ═══════════════════════════════════════════════════════════════════
# 9. ADMIN — CMS ARTIKEL & KATEGORI ARTIKEL
# ═══════════════════════════════════════════════════════════════════

ARTIKEL_PER_HALAMAN     = 15
KATEGORI_PER_HALAMAN    = 20
PUBLIK_ARTIKEL_PER_HAL  = 9


# ─── 9a. Kategori Artikel ────────────────────────────────────────

@staf_required
def admin_kategori_artikel(request):
    """Daftar Kategori Artikel dengan search dan pagination."""
    q  = request.GET.get('q', '').strip()
    qs = KategoriArtikel.objects.annotate(jumlah_artikel=Count('artikel'))
    if q:
        qs = qs.filter(nama__icontains=q)
    paginator = Paginator(qs.order_by('nama'), KATEGORI_PER_HALAMAN)
    page      = paginator.get_page(request.GET.get('page'))
    return render(request, 'dashboard/admin/kategori_artikel_list.html', {
        'page_obj':  page,
        'q':         q,
        'page_title': 'Kategori Artikel',
    })


@staf_required
def admin_kategori_tambah(request):
    """Tambah Kategori Artikel."""
    if request.method == 'POST':
        form = KategoriArtikelForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Kategori berhasil ditambahkan.")
            return redirect('admin_kategori_artikel')
    else:
        form = KategoriArtikelForm()
    return render(request, 'dashboard/admin/kategori_artikel_form.html', {
        'form':      form,
        'mode':      'tambah',
        'page_title': 'Tambah Kategori Artikel',
    })


@staf_required
def admin_kategori_edit(request, id):
    """Edit Kategori Artikel."""
    kategori = get_object_or_404(KategoriArtikel, id=id)
    if request.method == 'POST':
        form = KategoriArtikelForm(request.POST, instance=kategori)
        if form.is_valid():
            form.save()
            messages.success(request, "Kategori berhasil diperbarui.")
            return redirect('admin_kategori_artikel')
    else:
        form = KategoriArtikelForm(instance=kategori)
    return render(request, 'dashboard/admin/kategori_artikel_form.html', {
        'form':      form,
        'kategori':  kategori,
        'mode':      'edit',
        'page_title': f'Edit Kategori: {kategori.nama}',
    })


@staf_required
@require_POST
def admin_kategori_hapus(request, id):
    """Hapus Kategori Artikel — ditolak jika masih ada artikel terkait."""
    kategori = get_object_or_404(KategoriArtikel, id=id)
    if kategori.artikel.exists():
        messages.error(
            request,
            f"Kategori '{kategori.nama}' tidak dapat dihapus karena masih memiliki "
            f"{kategori.artikel.count()} artikel. Pindahkan atau hapus artikel terlebih dahulu."
        )
        return redirect('admin_kategori_artikel')
    nama = kategori.nama
    kategori.delete()
    messages.success(request, f"Kategori '{nama}' berhasil dihapus.")
    return redirect('admin_kategori_artikel')


# ─── 9b. Artikel & Berita ────────────────────────────────────────

@staf_required
def admin_artikel(request):
    """Daftar Artikel dengan search, filter status/kategori, dan pagination."""
    q         = request.GET.get('q', '').strip()
    status_f  = request.GET.get('status', '').strip()
    kat_f     = request.GET.get('kategori', '').strip()

    qs = Artikel.objects.select_related('kategori', 'penulis').order_by('-created_at')
    if q:
        qs = qs.filter(Q(judul__icontains=q) | Q(ringkasan__icontains=q))
    if status_f in ('draft', 'published'):
        qs = qs.filter(status=status_f)
    if kat_f.isdigit():
        qs = qs.filter(kategori_id=int(kat_f))

    paginator    = Paginator(qs, ARTIKEL_PER_HALAMAN)
    page         = paginator.get_page(request.GET.get('page'))
    kategori_all = KategoriArtikel.objects.order_by('nama')

    return render(request, 'dashboard/admin/artikel_list.html', {
        'page_obj':      page,
        'q':             q,
        'status_f':      status_f,
        'kat_f':         kat_f,
        'kategori_all':  kategori_all,
        'page_title':    'Artikel & Berita',
        'jumlah_total':  Artikel.objects.count(),
        'jumlah_pub':    Artikel.objects.filter(status='published').count(),
        'jumlah_draft':  Artikel.objects.filter(status='draft').count(),
    })


@staf_required
def admin_artikel_tambah(request):
    """Tambah Artikel baru."""
    if request.method == 'POST':
        form = ArtikelForm(request.POST, request.FILES)
        if form.is_valid():
            artikel = form.save(commit=False)
            artikel.penulis = request.user
            artikel.save()
            messages.success(request, f"Artikel '{artikel.judul}' berhasil ditambahkan.")
            return redirect('admin_artikel')
    else:
        form = ArtikelForm()
    return render(request, 'dashboard/admin/artikel_form.html', {
        'form':      form,
        'mode':      'tambah',
        'page_title': 'Tambah Artikel',
    })


@staf_required
def admin_artikel_edit(request, id):
    """Edit Artikel yang ada."""
    artikel = get_object_or_404(Artikel, id=id)
    if request.method == 'POST':
        form = ArtikelForm(request.POST, request.FILES, instance=artikel)
        if form.is_valid():
            form.save()
            messages.success(request, f"Artikel '{artikel.judul}' berhasil diperbarui.")
            return redirect('admin_artikel')
    else:
        form = ArtikelForm(instance=artikel)
    return render(request, 'dashboard/admin/artikel_form.html', {
        'form':      form,
        'artikel':   artikel,
        'mode':      'edit',
        'page_title': f'Edit Artikel: {artikel.judul}',
    })


@staf_required
def admin_artikel_hapus(request, id):
    """Konfirmasi & eksekusi hapus Artikel (GET=konfirmasi, POST=eksekusi)."""
    artikel = get_object_or_404(Artikel, id=id)
    if request.method == 'POST':
        judul = artikel.judul
        # Hapus gambar dari storage jika ada
        if artikel.gambar_utama:
            try:
                artikel.gambar_utama.storage.delete(artikel.gambar_utama.name)
            except Exception:
                pass
        artikel.delete()
        messages.success(request, f"Artikel '{judul}' berhasil dihapus.")
        return redirect('admin_artikel')
    return render(request, 'dashboard/admin/artikel_hapus.html', {
        'artikel':   artikel,
        'page_title': f'Hapus Artikel: {artikel.judul}',
    })


@staf_required
def admin_artikel_preview(request, id):
    """Preview artikel (draft maupun published) untuk administrator."""
    artikel = get_object_or_404(Artikel.objects.select_related('kategori', 'penulis'), id=id)
    return render(request, 'dashboard/admin/artikel_preview.html', {
        'artikel':   artikel,
        'page_title': f'Preview: {artikel.judul}',
    })


# ─── 9c. Halaman Publik Artikel ──────────────────────────────────

def artikel_publik(request):
    """Daftar artikel published, filter opsional per kategori slug."""
    kat_slug = request.GET.get('kategori', '').strip()
    qs       = Artikel.objects.filter(status='published').select_related('kategori', 'penulis').order_by('-created_at')

    kategori_aktif = None
    if kat_slug:
        kategori_aktif = get_object_or_404(KategoriArtikel, slug=kat_slug)
        qs = qs.filter(kategori=kategori_aktif)

    paginator      = Paginator(qs, PUBLIK_ARTIKEL_PER_HAL)
    page           = paginator.get_page(request.GET.get('page'))
    kategori_all   = KategoriArtikel.objects.annotate(
                         jml=Count('artikel', filter=Q(artikel__status='published'))
                     ).filter(jml__gt=0).order_by('nama')

    return render(request, 'artikel/artikel_list.html', {
        'page_obj':       page,
        'kategori_all':   kategori_all,
        'kategori_aktif': kategori_aktif,
    })


def artikel_detail(request, slug):
    """Detail satu artikel — hanya published yang bisa diakses publik."""
    artikel = get_object_or_404(
        Artikel.objects.select_related('kategori', 'penulis'),
        slug=slug, status='published'
    )
    # Artikel terkait dari kategori yang sama
    terkait = Artikel.objects.filter(
        status='published', kategori=artikel.kategori
    ).exclude(id=artikel.id).order_by('-created_at')[:3]

    return render(request, 'artikel/artikel_detail.html', {
        'artikel': artikel,
        'terkait': terkait,
    })


# ═══════════════════════════════════════════════════════════════════
# 10. HALAMAN WEBSITE — halaman informasi (Tentang Kami, S&K, dll.)
# ═══════════════════════════════════════════════════════════════════

HALAMAN_PER_HALAMAN = 15


@staf_required
def admin_halaman(request):
    """Daftar Halaman Website dengan search, filter status, dan pagination."""
    q        = request.GET.get('q', '').strip()
    status_f = request.GET.get('status', '').strip()

    qs = HalamanWebsite.objects.order_by('urutan', 'judul', 'id')
    if q:
        qs = qs.filter(
            Q(judul__icontains=q) | Q(slug__icontains=q) | Q(ringkasan__icontains=q)
        )
    if status_f in ('draft', 'published'):
        qs = qs.filter(status=status_f)

    page = Paginator(qs, HALAMAN_PER_HALAMAN).get_page(request.GET.get('page'))
    jumlah = HalamanWebsite.objects.aggregate(
        total=Count('id'),
        pub=Count('id', filter=Q(status='published')),
        draft=Count('id', filter=Q(status='draft')),
    )

    return render(request, 'dashboard/admin/halaman_list.html', {
        'page_obj':     page,
        'q':            q,
        'status_f':     status_f,
        'page_title':   'Halaman Website',
        'jumlah_total': jumlah['total'],
        'jumlah_pub':   jumlah['pub'],
        'jumlah_draft': jumlah['draft'],
    })


@staf_required
def admin_halaman_tambah(request):
    """Tambah Halaman Website baru."""
    if request.method == 'POST':
        form = HalamanWebsiteForm(request.POST)
        if form.is_valid():
            halaman = form.save()
            messages.success(request, f"Halaman '{halaman.judul}' berhasil ditambahkan.")
            return redirect('admin_halaman')
    else:
        form = HalamanWebsiteForm()
    return render(request, 'dashboard/admin/halaman_form.html', {
        'form':       form,
        'mode':       'tambah',
        'page_title': 'Tambah Halaman',
    })


@staf_required
def admin_halaman_edit(request, id):
    """Edit Halaman Website yang ada."""
    halaman = get_object_or_404(HalamanWebsite, id=id)
    if request.method == 'POST':
        form = HalamanWebsiteForm(request.POST, instance=halaman)
        if form.is_valid():
            halaman = form.save()
            messages.success(request, f"Halaman '{halaman.judul}' berhasil diperbarui.")
            return redirect('admin_halaman')
    else:
        form = HalamanWebsiteForm(instance=halaman)
    return render(request, 'dashboard/admin/halaman_form.html', {
        'form':       form,
        'halaman':    halaman,
        'mode':       'edit',
        'page_title': f'Edit Halaman: {halaman.judul}',
    })


@staf_required
def admin_halaman_hapus(request, id):
    """Konfirmasi & eksekusi hapus Halaman Website (GET=konfirmasi, POST=eksekusi)."""
    halaman = get_object_or_404(HalamanWebsite, id=id)
    if request.method == 'POST':
        judul = halaman.judul
        halaman.delete()
        messages.success(request, f"Halaman '{judul}' berhasil dihapus.")
        return redirect('admin_halaman')
    return render(request, 'dashboard/admin/halaman_hapus.html', {
        'halaman':    halaman,
        'page_title': f'Hapus Halaman: {halaman.judul}',
    })


@staf_required
def admin_halaman_preview(request, id):
    """Preview halaman (draft maupun terbit) untuk administrator."""
    halaman = get_object_or_404(HalamanWebsite, id=id)
    return render(request, 'dashboard/admin/halaman_preview.html', {
        'halaman':    halaman,
        'page_title': f'Preview: {halaman.judul}',
    })


def halaman_detail(request, slug):
    """Halaman informasi publik — hanya yang berstatus Diterbitkan yang bisa dibuka."""
    halaman = get_object_or_404(HalamanWebsite, slug=slug, status='published')
    return render(request, 'halaman/halaman_detail.html', {'halaman': halaman})



# ═══════════════════════════════════════════════════════════════════
# 11. HERO SLIDE — banner utama beranda
# ═══════════════════════════════════════════════════════════════════

HERO_PER_HALAMAN = 12


@staf_required
def admin_hero(request):
    """Daftar Hero Slide dengan search, filter status, dan pagination."""
    q        = request.GET.get('q', '').strip()
    status_f = request.GET.get('status', '').strip()

    qs = HeroSlide.objects.order_by('urutan', 'id')
    if q:
        qs = qs.filter(
            Q(judul__icontains=q) | Q(judul_sorot__icontains=q) | Q(label_promo__icontains=q)
        )
    if status_f in ('draft', 'published'):
        qs = qs.filter(status=status_f)

    page = Paginator(qs, HERO_PER_HALAMAN).get_page(request.GET.get('page'))
    jumlah = HeroSlide.objects.aggregate(
        total=Count('id'),
        pub=Count('id', filter=Q(status='published')),
        draft=Count('id', filter=Q(status='draft')),
    )

    return render(request, 'dashboard/admin/hero_list.html', {
        'page_obj':     page,
        'q':            q,
        'status_f':     status_f,
        'page_title':   'Hero Slide',
        'jumlah_total': jumlah['total'],
        'jumlah_pub':   jumlah['pub'],
        'jumlah_draft': jumlah['draft'],
        'maks_slide':   JUMLAH_MAKS_SLIDE,
    })


@staf_required
def admin_hero_tambah(request):
    """Tambah Hero Slide baru."""
    if request.method == 'POST':
        form = HeroSlideForm(request.POST, request.FILES)
        if form.is_valid():
            slide = form.save()
            messages.success(request, f"Slide '{slide.judul}' berhasil ditambahkan.")
            return redirect('admin_hero')
    else:
        form = HeroSlideForm()
    return render(request, 'dashboard/admin/hero_form.html', {
        'form':       form,
        'mode':       'tambah',
        'page_title': 'Tambah Hero Slide',
        'maks_slide': JUMLAH_MAKS_SLIDE,
    })


@staf_required
def admin_hero_edit(request, id):
    """Edit Hero Slide yang ada."""
    slide = get_object_or_404(HeroSlide, id=id)
    if request.method == 'POST':
        form = HeroSlideForm(request.POST, request.FILES, instance=slide)
        if form.is_valid():
            slide = form.save()
            messages.success(request, f"Slide '{slide.judul}' berhasil diperbarui.")
            return redirect('admin_hero')
    else:
        form = HeroSlideForm(instance=slide)
    return render(request, 'dashboard/admin/hero_form.html', {
        'form':       form,
        'slide':      slide,
        'mode':       'edit',
        'page_title': f'Edit Slide: {slide.judul}',
        'maks_slide': JUMLAH_MAKS_SLIDE,
    })


@staf_required
@require_POST
def admin_hero_status(request, id):
    """Ubah cepat status slide (Draft / Tayang) langsung dari daftar."""
    slide  = get_object_or_404(HeroSlide, id=id)
    status = request.POST.get('status', '')
    if status not in dict(HeroSlide.STATUS_CHOICES):
        messages.error(request, "Status slide tidak valid.")
    else:
        slide.status = status
        slide.save(update_fields=['status', 'updated_at'])
        messages.success(request, f"Slide “{slide.judul}” diubah menjadi {slide.get_status_display()}.")
    tujuan = request.POST.get('next', '')
    if tujuan and url_has_allowed_host_and_scheme(tujuan, allowed_hosts={request.get_host()}):
        return redirect(tujuan)
    return redirect('admin_hero')


@staf_required
def admin_hero_hapus(request, id):
    """Konfirmasi & eksekusi hapus Hero Slide (GET=konfirmasi, POST=eksekusi)."""
    slide = get_object_or_404(HeroSlide, id=id)
    if request.method == 'POST':
        judul = slide.judul
        # Hapus gambar dari storage jika ada
        if slide.gambar:
            try:
                slide.gambar.storage.delete(slide.gambar.name)
            except Exception:
                pass
        slide.delete()
        messages.success(request, f"Slide '{judul}' berhasil dihapus.")
        return redirect('admin_hero')
    return render(request, 'dashboard/admin/hero_hapus.html', {
        'slide':      slide,
        'page_title': f'Hapus Slide: {slide.judul}',
    })



# ═══════════════════════════════════════════════════════════════════
# 12. KERJA SAMA / MITRA — partner bisnis di beranda
# ═══════════════════════════════════════════════════════════════════

MITRA_PER_HALAMAN = 12


@staf_required
def admin_mitra(request):
    """Daftar Mitra dengan search, filter status, dan pagination."""
    q        = request.GET.get('q', '').strip()
    status_f = request.GET.get('status', '').strip()

    qs = Mitra.objects.order_by('urutan', 'nama')
    if q:
        qs = qs.filter(Q(nama__icontains=q) | Q(keterangan__icontains=q))
    if status_f in ('draft', 'published'):
        qs = qs.filter(status=status_f)

    page = Paginator(qs, MITRA_PER_HALAMAN).get_page(request.GET.get('page'))
    jumlah = Mitra.objects.aggregate(
        total=Count('id'),
        pub=Count('id', filter=Q(status='published')),
        draft=Count('id', filter=Q(status='draft')),
    )

    return render(request, 'dashboard/admin/mitra_list.html', {
        'page_obj':     page,
        'q':            q,
        'status_f':     status_f,
        'page_title':   'Kerja Sama / Mitra',
        'jumlah_total': jumlah['total'],
        'jumlah_pub':   jumlah['pub'],
        'jumlah_draft': jumlah['draft'],
        'maks_mitra':   JUMLAH_MAKS_MITRA,
    })


@staf_required
def admin_mitra_tambah(request):
    """Tambah Mitra baru."""
    if request.method == 'POST':
        form = MitraForm(request.POST, request.FILES)
        if form.is_valid():
            mitra = form.save()
            messages.success(request, f"Mitra '{mitra.nama}' berhasil ditambahkan.")
            return redirect('admin_mitra')
    else:
        form = MitraForm()
    return render(request, 'dashboard/admin/mitra_form.html', {
        'form':       form,
        'mode':       'tambah',
        'page_title': 'Tambah Mitra',
        'maks_mitra': JUMLAH_MAKS_MITRA,
    })


@staf_required
def admin_mitra_edit(request, id):
    """Edit Mitra yang ada."""
    mitra = get_object_or_404(Mitra, id=id)
    if request.method == 'POST':
        form = MitraForm(request.POST, request.FILES, instance=mitra)
        if form.is_valid():
            mitra = form.save()
            messages.success(request, f"Mitra '{mitra.nama}' berhasil diperbarui.")
            return redirect('admin_mitra')
    else:
        form = MitraForm(instance=mitra)
    return render(request, 'dashboard/admin/mitra_form.html', {
        'form':       form,
        'mitra':      mitra,
        'mode':       'edit',
        'page_title': f'Edit Mitra: {mitra.nama}',
        'maks_mitra': JUMLAH_MAKS_MITRA,
    })


@staf_required
@require_POST
def admin_mitra_status(request, id):
    """Ubah cepat status mitra (Draft / Tayang) langsung dari daftar."""
    mitra  = get_object_or_404(Mitra, id=id)
    status = request.POST.get('status', '')
    if status not in dict(Mitra.STATUS_CHOICES):
        messages.error(request, "Status mitra tidak valid.")
    else:
        mitra.status = status
        mitra.save(update_fields=['status', 'updated_at'])
        messages.success(request, f"Mitra \u201c{mitra.nama}\u201d diubah menjadi {mitra.get_status_display()}.")
    tujuan = request.POST.get('next', '')
    if tujuan and url_has_allowed_host_and_scheme(tujuan, allowed_hosts={request.get_host()}):
        return redirect(tujuan)
    return redirect('admin_mitra')


@staf_required
def admin_mitra_hapus(request, id):
    """Konfirmasi & eksekusi hapus Mitra (GET=konfirmasi, POST=eksekusi)."""
    mitra = get_object_or_404(Mitra, id=id)
    if request.method == 'POST':
        nama = mitra.nama
        # Hapus logo dari storage jika ada
        if mitra.logo:
            try:
                mitra.logo.storage.delete(mitra.logo.name)
            except Exception:
                pass
        mitra.delete()
        messages.success(request, f"Mitra '{nama}' berhasil dihapus.")
        return redirect('admin_mitra')
    return render(request, 'dashboard/admin/mitra_hapus.html', {
        'mitra':      mitra,
        'page_title': f'Hapus Mitra: {mitra.nama}',
    })



# ═══════════════════════════════════════════════════════════════════
# 13. PENGATURAN WEBSITE — informasi global (kontak, alamat, footer, sosmed)
# ═══════════════════════════════════════════════════════════════════

@staf_required
def admin_pengaturan(request):
    """Satu halaman untuk mengubah informasi global situs (hanya ada satu baris data)."""
    pengaturan = PengaturanWebsite.objects.filter(pk=1).first() or PengaturanWebsite()
    if request.method == 'POST':
        form = PengaturanWebsiteForm(request.POST, request.FILES, instance=pengaturan)
        if form.is_valid():
            form.save()
            messages.success(request, "Pengaturan website berhasil disimpan.")
            return redirect('admin_pengaturan')
    else:
        form = PengaturanWebsiteForm(instance=pengaturan)
    return render(request, 'dashboard/admin/pengaturan_form.html', {
        'form':       form,
        'page_title': 'Pengaturan Website',
        'terakhir':   pengaturan.updated_at if pengaturan.pk else None,
    })
