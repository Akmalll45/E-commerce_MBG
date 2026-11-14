import os
import uuid
from decimal import Decimal, InvalidOperation
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404
from django.core.paginator import Paginator
from django.db import DatabaseError, connection, transaction
from django.db.models import Count, Exists, OuterRef, Q, Subquery
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import get_valid_filename
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST
from functools import wraps
from urllib.parse import urlencode

from .models import (
    Produk, FotoMobil, TitipJual, FotoTitipJual, DokumenTitipJual,
    RiwayatStatusTitipJual, PermintaanTitipJual, SerahTerimaUnit,
    ChecklistInspeksiC2C, InspeksiC2C, HasilChecklistInspeksiC2C,
    TransaksiTitipJual, RiwayatTransaksiTitipJual,
    BookingCekUnit, Transaksi,
    KategoriArtikel, Artikel, HalamanWebsite, HeroSlide, Mitra, PengaturanWebsite,
)
from .forms import (
    LoginForm, RegisterForm, UserProfileForm, AdminProfileForm, UbahPasswordForm,
    ProdukForm, PengajuanTitipJualForm, HasilInspeksiC2CForm,
    LaporanPenjualanPemilikForm, LaporanPenjualanMBGForm,
    VerifikasiPenjualanC2CForm, SerahTerimaTitipJualForm,
    SelesaikanPenjualanC2CForm, validasi_foto_mobil,
    validasi_dokumen_titip_jual,
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
DOKUMEN_TITIP_JUAL_ADMIN_UPLOAD = ('perjanjian', 'hasil_inspeksi')


def produk_tayang():
    """Only ready B2C units and fully verified, current C2C listings are public."""
    tabel_c2c = {
        TitipJual._meta.db_table,
        FotoTitipJual._meta.db_table,
        SerahTerimaUnit._meta.db_table,
        InspeksiC2C._meta.db_table,
    }
    if not tabel_c2c.issubset(set(connection.introspection.table_names())):
        return Produk.objects.filter(
            status_stok=STATUS_TAYANG_PUBLIK,
            tipe_penjual='B2C',
        )

    inspeksi_terakhir = InspeksiC2C.objects.filter(
        titip_jual_id=OuterRef('pk')
    ).order_by('-tanggal_waktu', '-pk')
    serah_terima_masuk = SerahTerimaUnit.objects.filter(
        titip_jual_id=OuterRef('pk'),
        jenis='masuk',
        nama_penyerah__gt='',
        nama_penerima__gt='',
        lokasi__gt='',
        odometer_saat_ini__isnull=False,
        jumlah_kunci__isnull=False,
        kelengkapan__gt='',
        foto_kondisi__gt='',
        dicatat_oleh__isnull=False,
    )
    pengajuan_layak = TitipJual.objects.filter(
        pk=OuterRef('titip_jual_sumber_id'),
        status='tayang',
        status_persetujuan_harga='disetujui',
        harga_kesepakatan__gt=0,
    ).filter(
        Exists(serah_terima_masuk),
        *[
            Exists(FotoTitipJual.objects.filter(
                titip_jual_id=OuterRef('pk'),
                jenis_foto=jenis,
                gambar__gt='',
            ))
            for jenis in ('depan', 'belakang', 'samping_kiri', 'samping_kanan', 'ruang_mesin')
        ],
    ).annotate(
        status_inspeksi_terakhir=Subquery(inspeksi_terakhir.values('status')[:1])
    ).filter(status_inspeksi_terakhir='lulus')
    return (
        Produk.objects.filter(status_stok=STATUS_TAYANG_PUBLIK)
        .annotate(c2c_layak=Exists(pengajuan_layak))
        .filter(Q(tipe_penjual='B2C') | Q(tipe_penjual='C2C', c2c_layak=True))
    )


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
    produk = get_object_or_404(produk_tayang(), id=id)
    mobil_terkait = produk_tayang().exclude(id=id)[:3]

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

    # Booking lama tetap dicari lewat kontak; pengajuan C2C hanya lewat FK pemilik.
    filter_q = Q()
    if user.email:
        filter_q |= Q(email__iexact=user.email)
    if user.no_whatsapp:
        filter_q |= Q(no_whatsapp=user.no_whatsapp)

    if filter_q:
        user_bookings = BookingCekUnit.objects.filter(filter_q).select_related('produk').order_by('-created_at')
        user_transaksi = Transaksi.objects.filter(booking__in=user_bookings).select_related('booking', 'booking__produk').order_by('-created_at')
    else:
        user_bookings = BookingCekUnit.objects.none()
        user_transaksi = Transaksi.objects.none()

    user_titip_jual = TitipJual.objects.filter(pengguna=user).order_by('-created_at')
    context = {
        'user': user,
        'user_bookings': user_bookings,
        'user_titip_jual': user_titip_jual,
        'user_transaksi': user_transaksi,
        'total_booking': user_bookings.count(),
        'total_titip_jual': user_titip_jual.count(),
        'titip_jual_dalam_proses': user_titip_jual.filter(
            status__in=['pending', 'revisi', 'dijadwalkan', 'inspeksi', 'negosiasi']
        ).count(),
        'titip_jual_dititipkan': user_titip_jual.filter(status__in=['disetujui', 'tayang']).count(),
        'titip_jual_terjual': user_titip_jual.filter(status='terjual').count(),
        'total_transaksi': user_transaksi.count(),
    }
    return render(request, 'dashboard/user/dashboard.html', context)


def _hapus_file_saat_commit(field_file):
    nama_file = field_file.name
    storage = field_file.storage
    transaction.on_commit(lambda: storage.delete(nama_file))


def _simpan_berkas_titip_jual(request, pengajuan, form):
    for field_name, jenis in form.FOTO_WAJIB_FIELDS.items():
        foto = form.cleaned_data.get(field_name)
        if not foto:
            continue
        foto_lama = list(pengajuan.galeri_foto.filter(jenis_foto=jenis))
        FotoTitipJual.objects.create(
            titip_jual=pengajuan,
            gambar=foto,
            jenis_foto=jenis,
            keterangan=dict(FotoTitipJual.JENIS_FOTO)[jenis],
            urutan=list(form.FOTO_WAJIB_FIELDS).index(field_name),
        )
        for item in foto_lama:
            _hapus_file_saat_commit(item.gambar)
            item.delete()

    for urutan, foto in enumerate(form.cleaned_data.get('foto_tambahan', []), start=5):
        FotoTitipJual.objects.create(
            titip_jual=pengajuan,
            gambar=foto,
            jenis_foto='tambahan',
            keterangan='Foto tambahan',
            urutan=urutan,
        )

    for field_name, jenis in (
        ('dokumen_stnk', 'stnk'),
        ('dokumen_bpkb', 'bpkb'),
        ('dokumen_ktp', 'ktp_pemilik'),
    ):
        berkas = form.cleaned_data.get(field_name)
        if not berkas:
            continue
        dokumen_lama = list(pengajuan.dokumen.filter(jenis=jenis))
        ekstensi = os.path.splitext(berkas.name)[1].lower()
        DokumenTitipJual.objects.create(
            titip_jual=pengajuan,
            jenis=jenis,
            nama_file=f'{uuid.uuid4().hex}{ekstensi}',
            file=berkas,
            diunggah_oleh=request.user,
        )
        for item in dokumen_lama:
            _hapus_file_saat_commit(item.file)
            item.delete()


def _foto_wajib_form_list(form, pengajuan):
    foto_by_type = {}
    if pengajuan:
        foto_by_type = {
            foto.jenis_foto: foto
            for foto in pengajuan.galeri_foto.exclude(jenis_foto='tambahan')
        }
    return [
        {
            'field': form[field_name],
            'jenis': jenis,
            'existing': foto_by_type.get(jenis),
        }
        for field_name, jenis in form.FOTO_WAJIB_FIELDS.items()
    ]


@login_required(login_url='login')
def titip_jual_daftar(request):
    pengajuan = TitipJual.objects.filter(pengguna=request.user).order_by('-created_at')
    halaman = Paginator(pengajuan, 10).get_page(request.GET.get('page'))
    return render(request, 'dashboard/user/titip_jual_list.html', {
        'pengajuan_list': halaman,
        'total_pengajuan': pengajuan.count(),
        'dalam_proses': pengajuan.filter(
            status__in=['pending', 'revisi', 'dijadwalkan', 'inspeksi', 'negosiasi']
        ).count(),
        'dititipkan': pengajuan.filter(status__in=['disetujui', 'tayang']).count(),
        'terjual': pengajuan.filter(status='terjual').count(),
    })


@login_required(login_url='login')
def titip_jual_tambah(request):
    if request.method == 'POST':
        form = PengajuanTitipJualForm(request.POST, request.FILES, user=request.user)
        if form.is_valid():
            with transaction.atomic():
                pengajuan = form.save(commit=False)
                pengajuan.pengguna = request.user
                pengajuan.status = 'draft'
                pengajuan.save()
                _simpan_berkas_titip_jual(request, pengajuan, form)
                pengajuan.lakukan_transisi(
                    'pending',
                    oleh_user=request.user,
                    catatan='Pengajuan dikirim oleh pemilik.',
                )
            messages.success(request, 'Pengajuan Titip-Jual berhasil dikirim untuk diverifikasi.')
            return redirect('titip_jual_detail', pengajuan_id=pengajuan.pk)
        messages.error(request, 'Periksa kembali data kendaraan, lima foto wajib, dan dokumen.')
    else:
        form = PengajuanTitipJualForm(user=request.user)
    return render(request, 'dashboard/user/titip_jual_form.html', {
        'form': form,
        'pengajuan': None,
        'foto_wajib_list': _foto_wajib_form_list(form, None),
    })


@login_required(login_url='login')
def titip_jual_ubah(request, pengajuan_id):
    pengajuan = get_object_or_404(
        TitipJual.objects.filter(pengguna=request.user),
        pk=pengajuan_id,
    )
    if pengajuan.status not in ('draft', 'revisi', 'ditolak', 'kadaluarsa'):
        messages.error(request, 'Pengajuan ini tidak dapat diubah pada tahap saat ini.')
        return redirect('titip_jual_detail', pengajuan_id=pengajuan.pk)

    if request.method == 'POST':
        form = PengajuanTitipJualForm(
            request.POST, request.FILES, instance=pengajuan, user=request.user
        )
        if form.is_valid():
            with transaction.atomic():
                pengajuan = form.save(commit=False)
                pengajuan.save()
                _simpan_berkas_titip_jual(request, pengajuan, form)
                pengajuan.lakukan_transisi(
                    'pending',
                    oleh_user=request.user,
                    catatan='Pemilik memperbarui pengajuan.',
                )
            messages.success(request, 'Perbaikan pengajuan berhasil dikirim.')
            return redirect('titip_jual_detail', pengajuan_id=pengajuan.pk)
        messages.error(request, 'Periksa kembali data kendaraan, lima foto wajib, dan dokumen.')
    else:
        form = PengajuanTitipJualForm(instance=pengajuan, user=request.user)
    return render(request, 'dashboard/user/titip_jual_form.html', {
        'form': form,
        'pengajuan': pengajuan,
        'foto_wajib_list': _foto_wajib_form_list(form, pengajuan),
    })


@login_required(login_url='login')
def titip_jual_detail(request, pengajuan_id):
    pengajuan = get_object_or_404(
        TitipJual.objects.select_related('produk_terkait').prefetch_related(
            'galeri_foto', 'dokumen', 'riwayat_status__diubah_oleh',
            'permintaan_pemilik', 'inspeksi_c2c__pemeriksa',
            'inspeksi_c2c__hasil_checklist__poin',
            'transaksi_penjualan__riwayat__diubah_oleh',
        ).filter(pengguna=request.user),
        pk=pengajuan_id,
    )
    transaksi = pengajuan.transaksi_penjualan.all()
    transaksi_aktif = transaksi.filter(
        status__in=['menunggu_verifikasi', 'terverifikasi', 'selesai']
    ).exists()
    serah_terima = pengajuan.serah_terima.select_related(
        'dicatat_oleh', 'transaksi_penjualan'
    ).all()
    return render(request, 'dashboard/user/titip_jual_detail.html', {
        'pengajuan': pengajuan,
        'foto_wajib': pengajuan.galeri_foto.exclude(jenis_foto='tambahan'),
        'foto_tambahan': pengajuan.galeri_foto.filter(jenis_foto='tambahan'),
        'dokumen_list': pengajuan.dokumen.all(),
        'riwayat_status': pengajuan.riwayat_status.all(),
        'permintaan_list': pengajuan.permintaan_pemilik.all(),
        'inspeksi_list': pengajuan.inspeksi_c2c.all(),
        'transaksi_list': transaksi,
        'serah_terima_list': serah_terima,
        'dapat_laporkan_penjualan': pengajuan.status == 'tayang' and not transaksi_aktif,
        'dapat_minta_pembatalan': (
            pengajuan.status in (
                'pending', 'revisi', 'dijadwalkan', 'inspeksi',
                'negosiasi', 'disetujui', 'tayang',
            )
            and not transaksi_aktif
        ),
        'perjanjian_verifikasi': pengajuan.dokumen.filter(
            jenis='perjanjian', sudah_diverifikasi=True
        ).exists(),
        'dapat_diubah': pengajuan.status in ('draft', 'revisi', 'ditolak', 'kadaluarsa'),
    })


@login_required(login_url='login')
@require_POST
def titip_jual_tindakan_pemilik(request, pengajuan_id):
    pengajuan = get_object_or_404(
        TitipJual.objects.filter(pengguna=request.user),
        pk=pengajuan_id,
    )
    tindakan = request.POST.get('aksi') or request.POST.get('tindakan')
    catatan = request.POST.get('catatan', '').strip()
    tahap_harga = (
        pengajuan.status == 'negosiasi'
        and pengajuan.status_persetujuan_harga == 'menunggu'
    )

    if tindakan in ('setujui_harga', 'tolak_harga') and tahap_harga:
        with transaction.atomic():
            if tindakan == 'setujui_harga':
                pengajuan.harga_kesepakatan = pengajuan.harga_rekomendasi
                pengajuan.status_persetujuan_harga = 'disetujui'
                pengajuan.tanggal_persetujuan_harga = timezone.now()
                pengajuan.save(update_fields=[
                    'harga_kesepakatan', 'status_persetujuan_harga',
                    'tanggal_persetujuan_harga', 'updated_at',
                ])
                pengajuan.lakukan_transisi(
                    'disetujui', oleh_user=request.user,
                    catatan='Pemilik menyetujui rekomendasi harga.',
                )
            else:
                pengajuan.status_persetujuan_harga = 'ditolak'
                pengajuan.save(update_fields=['status_persetujuan_harga', 'updated_at'])
                PermintaanTitipJual.objects.create(
                    titip_jual=pengajuan,
                    jenis='ubah_harga',
                    catatan=catatan or 'Pemilik menolak rekomendasi harga dan meminta pembahasan ulang.',
                    dibuat_oleh=request.user,
                )
        messages.success(
            request,
            'Persetujuan harga berhasil disimpan.' if tindakan == 'setujui_harga'
            else 'Penolakan harga dan permintaan pembahasan ulang berhasil dikirim.',
        )
    elif tindakan == 'ubah_harga' and pengajuan.status == 'negosiasi':
        try:
            harga_diminta = Decimal(request.POST.get('harga_diminta', '').replace(',', '').strip())
            if harga_diminta <= 0 or harga_diminta >= Decimal('1000000000000'):
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            messages.error(request, 'Harga baru harus lebih besar dari nol dan maksimal 999.999.999.999.')
            return redirect('titip_jual_detail', pengajuan_id=pengajuan.pk)
        if pengajuan.permintaan_pemilik.filter(jenis='ubah_harga', status='menunggu').exists():
            messages.error(request, 'Permintaan perubahan harga sebelumnya masih menunggu tindakan admin.')
        else:
            PermintaanTitipJual.objects.create(
                titip_jual=pengajuan,
                jenis='ubah_harga',
                harga_diminta=harga_diminta,
                catatan=catatan,
                dibuat_oleh=request.user,
            )
            messages.success(request, 'Permintaan perubahan harga berhasil dikirim ke admin.')
    elif tindakan == 'pembatalan' and pengajuan.status in (
        'pending', 'revisi', 'dijadwalkan', 'inspeksi', 'negosiasi', 'disetujui', 'tayang'
    ):
        if pengajuan.transaksi_penjualan.filter(
            status__in=['menunggu_verifikasi', 'terverifikasi', 'selesai']
        ).exists():
            messages.error(request, 'Selesaikan atau tunggu verifikasi laporan penjualan sebelum meminta pembatalan.')
        elif pengajuan.permintaan_pemilik.filter(jenis='pembatalan', status='menunggu').exists():
            messages.error(request, 'Permintaan pembatalan sebelumnya masih menunggu tindakan admin.')
        else:
            PermintaanTitipJual.objects.create(
                titip_jual=pengajuan,
                jenis='pembatalan',
                catatan=catatan,
                dibuat_oleh=request.user,
            )
            messages.success(request, 'Permintaan pembatalan berhasil dikirim ke admin.')
    elif tindakan == 'lapor_penjualan_pemilik':
        form = LaporanPenjualanPemilikForm(request.POST)
        if pengajuan.status != 'tayang':
            messages.error(request, 'Laporan penjualan hanya dapat diajukan untuk kendaraan yang sedang tayang.')
        elif not form.is_valid():
            messages.error(request, 'Periksa nama/kontak pembeli, harga, tanggal, dan catatan transaksi.')
        else:
            with transaction.atomic():
                pengajuan_terkunci = get_object_or_404(
                    TitipJual.objects.select_for_update().filter(pengguna=request.user),
                    pk=pengajuan.pk,
                )
                aktif = pengajuan_terkunci.transaksi_penjualan.filter(
                    status__in=['menunggu_verifikasi', 'terverifikasi', 'selesai']
                ).exists()
                if pengajuan_terkunci.status != 'tayang' or aktif:
                    messages.error(request, 'Pengajuan sudah berubah atau memiliki laporan penjualan aktif.')
                elif (
                    pengajuan_terkunci.harga_jual_minimum
                    and form.cleaned_data['harga_final'] < pengajuan_terkunci.harga_jual_minimum
                ):
                    messages.error(request, 'Harga transaksi di bawah batas minimum yang disetujui pemilik.')
                else:
                    transaksi_baru = TransaksiTitipJual.objects.create(
                        titip_jual=pengajuan_terkunci,
                        sumber='pemilik',
                        nama_pembeli=form.cleaned_data['nama_pembeli'],
                        kontak_pembeli=form.cleaned_data['kontak_pembeli'],
                        harga_final=form.cleaned_data['harga_final'],
                        tanggal_kesepakatan=form.cleaned_data['tanggal_kesepakatan'],
                        catatan_pelapor=form.cleaned_data['catatan_pelapor'],
                        status='menunggu_verifikasi',
                        dilaporkan_oleh=request.user,
                    )
                    RiwayatTransaksiTitipJual.objects.create(
                        transaksi=transaksi_baru,
                        status_dari='',
                        status_ke='menunggu_verifikasi',
                        diubah_oleh=request.user,
                        catatan='Pemilik melaporkan pembeli yang ditemukan secara mandiri.',
                    )
                    if pengajuan_terkunci.produk_terkait_id:
                        pengajuan_terkunci.produk_terkait.status_stok = 'in_review'
                        pengajuan_terkunci.produk_terkait.save(
                            update_fields=['status_stok', 'updated_at']
                        )
                    messages.success(request, 'Laporan penjualan dikirim dan menunggu verifikasi MBG.')
    else:
        messages.error(request, 'Tindakan tidak tersedia pada tahap pengajuan saat ini.')
    return redirect('titip_jual_detail', pengajuan_id=pengajuan.pk)


@login_required(login_url='login')
@require_GET
def dokumen_titip_jual_unduh(request, dokumen_id):
    dokumen = get_object_or_404(
        DokumenTitipJual.objects.select_related('titip_jual'),
        pk=dokumen_id,
    )
    if request.user.is_staff or request.user.is_superuser:
        if not request.user.is_superuser and not request.user.has_perm('toko.view_titipjual'):
            raise Http404
    elif dokumen.titip_jual.pengguna_id != request.user.pk:
        raise Http404
    try:
        berkas = dokumen.file.open('rb')
    except (FileNotFoundError, OSError, ValueError):
        raise Http404
    ekstensi = os.path.splitext(dokumen.file.name)[1].lower()
    if ekstensi not in ('.pdf', '.jpg', '.png', '.webp'):
        ekstensi = '.bin'
    nama_file = get_valid_filename(f'dokumen-{dokumen.jenis}{ekstensi}')
    response = FileResponse(
        berkas,
        as_attachment=True,
        filename=nama_file,
        content_type='application/octet-stream',
    )
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


@login_required(login_url='login')
@require_GET
def serah_terima_foto_unduh(request, serah_terima_id):
    serah_terima = get_object_or_404(
        SerahTerimaUnit.objects.select_related('titip_jual'),
        pk=serah_terima_id,
    )
    if request.user.is_staff or request.user.is_superuser:
        if not request.user.is_superuser and not request.user.has_perm('toko.view_titipjual'):
            raise Http404
    elif serah_terima.titip_jual.pengguna_id != request.user.pk:
        raise Http404
    if not serah_terima.foto_kondisi:
        raise Http404
    try:
        berkas = serah_terima.foto_kondisi.open('rb')
    except (FileNotFoundError, OSError, ValueError):
        raise Http404
    response = FileResponse(
        berkas,
        as_attachment=False,
        filename=f'serah-terima-{serah_terima.pk}{os.path.splitext(serah_terima.foto_kondisi.name)[1].lower()}',
        content_type='application/octet-stream',
    )
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    return response


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
    titip_jual_pending = TitipJual.objects.filter(status__in=['pending', 'revisi']).count()
    titip_jual_disetujui = TitipJual.objects.filter(status__in=['disetujui', 'tayang']).count()

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


def c2c_permission_required(permission):
    """Require the corresponding Django model permission for sensitive C2C screens."""
    def decorator(view):
        @wraps(view)
        @login_required(login_url='login')
        def pembungkus(request, *args, **kwargs):
            if not (request.user.is_staff or request.user.is_superuser):
                messages.error(request, "Akses ditolak. Halaman ini hanya untuk staf MBG.")
                return redirect('dashboard_user')
            if not request.user.is_superuser and not request.user.has_perm(
                f'toko.{permission}_titipjual'
            ):
                raise PermissionDenied
            return view(request, *args, **kwargs)
        return pembungkus
    return decorator


TAHAP_TITIP_JUAL_ADMIN = {
    'verifikasi': ('Verifikasi & Revisi', ['pending', 'revisi']),
    'inspeksi': ('Jadwal Inspeksi', ['dijadwalkan', 'inspeksi']),
    'harga': ('Persetujuan Harga', ['negosiasi']),
    'showroom': ('Kendaraan di Showroom', ['disetujui', 'tayang']),
    'penjualan': ('Penjualan & Penyelesaian', ['terjual', 'dikembalikan', 'ditolak', 'kadaluarsa']),
}


def _query_titip_jual_admin(request, tahap=None):
    daftar = TitipJual.objects.select_related('pengguna', 'produk_terkait').order_by('-created_at', '-id')
    if tahap:
        if tahap not in TAHAP_TITIP_JUAL_ADMIN:
            raise Http404
        if tahap == 'penjualan':
            daftar = daftar.filter(
                Q(status__in=TAHAP_TITIP_JUAL_ADMIN[tahap][1])
                | Q(transaksi_penjualan__status__in=['menunggu_verifikasi', 'terverifikasi'])
            ).distinct()
        else:
            daftar = daftar.filter(status__in=TAHAP_TITIP_JUAL_ADMIN[tahap][1])

    q = ' '.join(request.GET.get('q', '').split())[:100]
    if q:
        for kata in q.split(' ')[:5]:
            daftar = daftar.filter(
                Q(nama_pemilik__icontains=kata)
                | Q(no_whatsapp__icontains=kata)
                | Q(email__icontains=kata)
                | Q(merek__icontains=kata)
                | Q(model__icontains=kata)
                | Q(plat_nomor__icontains=kata)
            )
    status = request.GET.get('status', '')
    if status in dict(TitipJual.STATUS_PENGAJUAN):
        daftar = daftar.filter(status=status)
    return daftar, q, status


@c2c_permission_required('view')
def admin_titip_jual(request):
    return _halaman_admin_titip_jual(request, None)


@c2c_permission_required('view')
def admin_titip_jual_tahap(request, tahap):
    if tahap not in TAHAP_TITIP_JUAL_ADMIN:
        raise Http404
    return _halaman_admin_titip_jual(request, tahap)


def _halaman_admin_titip_jual(request, tahap):
    daftar, q, status = _query_titip_jual_admin(request, tahap)
    halaman = Paginator(daftar, 15).get_page(request.GET.get('page'))
    return render(request, 'dashboard/admin/titip_jual_list.html', {
        'pengajuan_list': halaman,
        'tahap': tahap,
        'judul_tahap': TAHAP_TITIP_JUAL_ADMIN.get(tahap, ('Semua Pengajuan', []))[0],
        'status_choices': TitipJual.STATUS_PENGAJUAN,
        'status_filter': status,
        'q': q,
        'titip_jual_pending': TitipJual.objects.filter(status__in=['pending', 'revisi']).count(),
        'tahap_titip_jual': TAHAP_TITIP_JUAL_ADMIN,
    })


def _buat_atau_perbarui_produk_c2c(pengajuan):
    inspeksi = pengajuan.inspeksi_c2c.order_by('-tanggal_waktu', '-pk').first()
    if not inspeksi or inspeksi.status != 'lulus':
        raise ValueError('Kendaraan wajib lulus checklist inspeksi sebelum ditayangkan.')
    if pengajuan.status_persetujuan_harga != 'disetujui':
        raise ValueError('Persetujuan harga oleh pemilik wajib tersedia sebelum ditayangkan.')
    if not pengajuan.foto_wajib_tersedia():
        raise ValueError('Lima foto wajib harus tersedia sebelum kendaraan ditayangkan.')
    if not pengajuan.serah_terima_masuk_lengkap():
        raise ValueError('Serah-terima masuk yang lengkap wajib dicatat sebelum kendaraan ditayangkan.')
    foto_utama = pengajuan.galeri_foto.exclude(jenis_foto='tambahan').order_by('urutan', 'id').first()
    if not foto_utama:
        raise ValueError('Lima foto wajib harus tersedia sebelum kendaraan ditayangkan.')
    harga = pengajuan.harga_kesepakatan
    if not harga or harga <= 0:
        raise ValueError('Harga final yang disetujui pemilik wajib tersedia sebelum kendaraan ditayangkan.')

    product = pengajuan.produk_terkait
    if product is None:
        product = Produk(
            sku=f'C2C-TJ-{pengajuan.pk:08d}',
            nama=f'{pengajuan.merek} {pengajuan.model} {pengajuan.tahun}'.strip()[:200],
            merek=pengajuan.merek,
            kategori='Titip-Jual C2C',
            tahun=pengajuan.tahun,
            transmisi=pengajuan.transmisi if pengajuan.transmisi in dict(Produk.TRANSMISI_CHOICES) else 'AT',
            jarak_tempuh=pengajuan.jarak_tempuh,
            warna=pengajuan.warna or 'Belum diinformasikan',
            plat_nomor=pengajuan.plat_nomor or 'Belum diinformasikan',
            harga=harga,
            tipe_penjual='C2C',
            status_stok='ready',
            lulus_inspeksi=True,
            ringkasan_inspeksi=pengajuan.hasil_inspeksi or 'Lulus checklist inspeksi MBG.',
            gambar_utama=foto_utama.gambar.name,
            deskripsi_singkat=f'Kendaraan titip-jual pengguna MBG. Harga rekomendasi Rp {harga:,.0f}.',
            deskripsi_detail=(
                f'{pengajuan.kondisi_eksterior}\n\nHasil inspeksi MBG:\n'
                f'{pengajuan.hasil_inspeksi or "Belum ada catatan tambahan."}'
            ),
            titip_jual_sumber=pengajuan,
        )
    else:
        product.nama = f'{pengajuan.merek} {pengajuan.model} {pengajuan.tahun}'.strip()[:200]
        product.merek = pengajuan.merek
        product.tahun = pengajuan.tahun
        product.transmisi = pengajuan.transmisi if pengajuan.transmisi in dict(Produk.TRANSMISI_CHOICES) else 'AT'
        product.jarak_tempuh = pengajuan.jarak_tempuh
        product.warna = pengajuan.warna or 'Belum diinformasikan'
        product.plat_nomor = pengajuan.plat_nomor or 'Belum diinformasikan'
        product.harga = harga
        product.tipe_penjual = 'C2C'
        product.status_stok = 'ready'
        product.gambar_utama = foto_utama.gambar.name
        product.deskripsi_detail = (
            f'{pengajuan.kondisi_eksterior}\n\nHasil inspeksi MBG:\n'
            f'{pengajuan.hasil_inspeksi or "Belum ada catatan tambahan."}'
        )
        product.titip_jual_sumber = pengajuan
    product.save()
    if pengajuan.produk_terkait_id != product.pk:
        pengajuan.produk_terkait = product
        pengajuan.save(update_fields=['produk_terkait', 'updated_at'])
    return product


def _proses_permintaan_pemilik(request, pengajuan):
    permintaan = get_object_or_404(
        PermintaanTitipJual.objects.filter(titip_jual=pengajuan, status='menunggu'),
        pk=request.POST.get('permintaan_id'),
    )
    keputusan = request.POST.get('keputusan')
    catatan_admin = request.POST.get('catatan_admin', '').strip()
    if keputusan not in ('setujui', 'tolak'):
        raise ValueError('Keputusan permintaan tidak valid.')

    with transaction.atomic():
        pengajuan = TitipJual.objects.select_for_update().get(pk=pengajuan.pk)
        permintaan = PermintaanTitipJual.objects.select_for_update().get(
            pk=permintaan.pk, status='menunggu'
        )
        if permintaan.jenis == 'pembatalan':
            if keputusan == 'setujui':
                if pengajuan.transaksi_penjualan.filter(
                    status__in=['menunggu_verifikasi', 'terverifikasi']
                ).exists():
                    raise ValueError('Pembatalan tidak dapat disetujui saat transaksi penjualan aktif.')
                if not pengajuan.dapat_transisi_ke('dikembalikan'):
                    raise ValueError('Pembatalan tidak dapat disetujui pada tahap ini.')
                pengajuan.lakukan_transisi(
                    'dikembalikan', oleh_user=request.user,
                    catatan=catatan_admin or 'Pembatalan disetujui oleh admin.',
                )
                if pengajuan.produk_terkait_id:
                    pengajuan.produk_terkait.status_stok = 'returned'
                    pengajuan.produk_terkait.save(update_fields=['status_stok', 'updated_at'])
            permintaan.status = 'disetujui' if keputusan == 'setujui' else 'ditolak'
        else:
            if pengajuan.status != 'negosiasi':
                raise ValueError('Perubahan harga hanya dapat diproses pada tahap negosiasi.')
            if keputusan == 'setujui':
                harga = permintaan.harga_diminta
                if harga is None:
                    try:
                        harga = Decimal(request.POST.get('harga_rekomendasi', '').replace(',', '').strip())
                    except (InvalidOperation, ValueError):
                        raise ValueError('Masukkan rekomendasi harga baru yang valid.')
                if harga <= 0 or harga >= Decimal('1000000000000'):
                    raise ValueError('Harga rekomendasi harus lebih besar dari nol dan maksimal 999.999.999.999.')
                pengajuan.harga_rekomendasi = harga
                pengajuan.harga_kesepakatan = None
                pengajuan.status_persetujuan_harga = 'menunggu'
                pengajuan.tanggal_persetujuan_harga = None
                pengajuan.save(update_fields=[
                    'harga_rekomendasi', 'harga_kesepakatan',
                    'status_persetujuan_harga', 'tanggal_persetujuan_harga', 'updated_at',
                ])
            else:
                pengajuan.status_persetujuan_harga = 'ditolak'
                pengajuan.save(update_fields=['status_persetujuan_harga', 'updated_at'])
            permintaan.status = 'disetujui' if keputusan == 'setujui' else 'ditolak'
        permintaan.catatan_admin = catatan_admin
        permintaan.diproses_oleh = request.user
        permintaan.processed_at = timezone.now()
        permintaan.save(update_fields=[
            'status', 'catatan_admin', 'diproses_oleh', 'processed_at',
        ])


@c2c_permission_required('view')
def admin_titip_jual_detail(request, pengajuan_id):
    pengajuan = get_object_or_404(
        TitipJual.objects.select_related('pengguna', 'produk_terkait').prefetch_related(
            'galeri_foto', 'dokumen__diunggah_oleh', 'riwayat_status__diubah_oleh',
            'permintaan_pemilik__dibuat_oleh', 'serah_terima',
            'inspeksi_c2c__pemeriksa', 'inspeksi_c2c__hasil_checklist__poin',
            'transaksi_penjualan__dilaporkan_oleh',
            'transaksi_penjualan__diverifikasi_oleh',
            'transaksi_penjualan__diselesaikan_oleh',
            'transaksi_penjualan__riwayat__diubah_oleh',
        ),
        pk=pengajuan_id,
    )
    checklist_inspeksi = ChecklistInspeksiC2C.objects.filter(aktif=True).order_by('urutan', 'pk')
    form_inspeksi = HasilInspeksiC2CForm(poin_inspeksi=checklist_inspeksi)
    form_serah_terima = SerahTerimaTitipJualForm()
    return render(request, 'dashboard/admin/titip_jual_detail.html', {
        'pengajuan': pengajuan,
        'foto_list': pengajuan.galeri_foto.all(),
        'dokumen_list': pengajuan.dokumen.all(),
        'riwayat_status': pengajuan.riwayat_status.all(),
        'permintaan_list': pengajuan.permintaan_pemilik.all(),
        'inspeksi_list': pengajuan.inspeksi_c2c.all(),
        'transaksi_list': pengajuan.transaksi_penjualan.all(),
        'transaksi_aktif_list': pengajuan.transaksi_penjualan.filter(
            status__in=['menunggu_verifikasi', 'terverifikasi']
        ),
        'transaksi_terverifikasi_list': pengajuan.transaksi_penjualan.filter(
            status='terverifikasi'
        ),
        'perjanjian_terverifikasi': pengajuan.dokumen.filter(
            jenis='perjanjian', sudah_diverifikasi=True
        ).exists(),
        'dapat_catat_penjualan_mbg': (
            pengajuan.status == 'tayang'
            and not pengajuan.transaksi_penjualan.filter(
                status__in=['menunggu_verifikasi', 'terverifikasi', 'selesai']
            ).exists()
        ),
        'checklist_inspeksi': checklist_inspeksi,
        'form_inspeksi': form_inspeksi,
        'form_serah_terima': form_serah_terima,
        'checklist_rows': [
            (
                poin,
                form_inspeksi[f'poin_{poin.pk}'],
                form_inspeksi[f'catatan_{poin.pk}'],
            )
            for poin in checklist_inspeksi
        ],
        'status_choices': TitipJual.STATUS_PENGAJUAN,
        'dokumen_upload_choices': [
            choice for choice in DokumenTitipJual.JENIS_DOKUMEN
            if choice[0] in DOKUMEN_TITIP_JUAL_ADMIN_UPLOAD
        ],
        'titip_jual_pending': TitipJual.objects.filter(status__in=['pending', 'revisi']).count(),
    })


@c2c_permission_required('change')
@require_POST
def admin_titip_jual_tindakan(request, pengajuan_id):
    pengajuan = get_object_or_404(
        TitipJual.objects.select_related('produk_terkait').prefetch_related('galeri_foto'),
        pk=pengajuan_id,
    )
    tindakan = request.POST.get('aksi') or request.POST.get('tindakan')
    catatan = request.POST.get('catatan', '').strip()
    try:
        with transaction.atomic():
            if tindakan == 'status':
                status_baru = request.POST.get('status', '')
                if status_baru in ('revisi', 'ditolak', 'dikembalikan') and not catatan:
                    raise ValueError('Catatan untuk pemilik wajib diisi untuk status ini.')
                if status_baru == 'disetujui' and pengajuan.status_persetujuan_harga != 'disetujui':
                    raise ValueError('Persetujuan harga pemilik diperlukan sebelum penitipan disetujui.')
                if status_baru == 'tayang':
                    if pengajuan.transaksi_penjualan.filter(
                        status__in=['menunggu_verifikasi', 'terverifikasi', 'selesai']
                    ).exists():
                        raise ValueError('Unit memiliki laporan/transaksi penjualan aktif atau selesai.')
                    product = _buat_atau_perbarui_produk_c2c(pengajuan)
                    if product.status_stok != 'ready':
                        raise ValueError('Produk C2C tidak dapat ditayangkan dalam status stok saat ini.')
                if status_baru == 'revisi':
                    pengajuan.catatan_revisi = catatan
                    pengajuan.save(update_fields=['catatan_revisi', 'updated_at'])
                elif status_baru in ('ditolak', 'dikembalikan'):
                    pengajuan.alasan_penolakan = catatan
                    pengajuan.save(update_fields=['alasan_penolakan', 'updated_at'])
                if not (status_baru == 'tayang' and pengajuan.status == 'tayang'):
                    pengajuan.lakukan_transisi(
                        status_baru, oleh_user=request.user, catatan=catatan,
                    )
                if status_baru == 'dikembalikan' and pengajuan.produk_terkait_id:
                    pengajuan.produk_terkait.status_stok = 'returned'
                    pengajuan.produk_terkait.save(update_fields=['status_stok', 'updated_at'])

            elif tindakan == 'jadwalkan_inspeksi':
                jadwal = parse_datetime(request.POST.get('jadwal_inspeksi', '').strip())
                if not jadwal:
                    raise ValueError('Tanggal dan waktu inspeksi harus valid.')
                if timezone.is_naive(jadwal):
                    jadwal = timezone.make_aware(jadwal, timezone.get_current_timezone())
                pengajuan.tanggal_inspeksi_dijadwalkan = jadwal
                pengajuan.save(update_fields=['tanggal_inspeksi_dijadwalkan', 'updated_at'])
                pengajuan.lakukan_transisi(
                    'dijadwalkan', oleh_user=request.user,
                    catatan=catatan or f'Inspeksi dijadwalkan pada {jadwal:%d-%m-%Y %H:%M}.',
                )

            elif tindakan == 'mulai_inspeksi':
                pengajuan.lakukan_transisi(
                    'inspeksi', oleh_user=request.user,
                    catatan=catatan or 'Inspeksi kendaraan dimulai.',
                )

            elif tindakan == 'hasil_inspeksi':
                if pengajuan.status not in ('inspeksi', 'tayang'):
                    raise ValueError('Hasil inspeksi hanya dapat dicatat saat pemeriksaan berlangsung atau audit ulang.')
                if not pengajuan.foto_wajib_tersedia():
                    raise ValueError('Lima foto wajib harus tersedia sebelum inspeksi diproses.')
                poin_inspeksi = list(ChecklistInspeksiC2C.objects.filter(
                    aktif=True
                ).order_by('urutan', 'pk'))
                form_inspeksi = HasilInspeksiC2CForm(
                    request.POST, poin_inspeksi=poin_inspeksi,
                )
                if not form_inspeksi.is_valid():
                    raise ValueError('Checklist inspeksi belum valid: ' + form_inspeksi.errors.as_text())
                hasil = 'lulus' if form_inspeksi.lulus else 'gagal'
                harga = form_inspeksi.cleaned_data.get('harga_rekomendasi') if hasil == 'lulus' else None
                laporan = InspeksiC2C.objects.create(
                    titip_jual=pengajuan,
                    status=hasil,
                    pemeriksa=request.user,
                    catatan=form_inspeksi.cleaned_data.get('catatan', '').strip(),
                    harga_rekomendasi=harga,
                )
                for poin in poin_inspeksi:
                    HasilChecklistInspeksiC2C.objects.create(
                        inspeksi=laporan,
                        poin=poin,
                        hasil=form_inspeksi.cleaned_data[f'poin_{poin.pk}'],
                        catatan=form_inspeksi.cleaned_data.get(f'catatan_{poin.pk}', '').strip(),
                    )
                pengajuan.hasil_inspeksi = (
                    form_inspeksi.cleaned_data.get('catatan', '').strip()
                    or ('Checklist inspeksi lulus.' if hasil == 'lulus' else 'Checklist inspeksi belum lulus.')
                )
                update_fields = ['hasil_inspeksi', 'updated_at']
                if hasil == 'lulus' and pengajuan.status == 'inspeksi':
                    pengajuan.harga_rekomendasi = harga
                    pengajuan.status_persetujuan_harga = 'menunggu'
                    pengajuan.tanggal_persetujuan_harga = None
                    update_fields.extend([
                        'harga_rekomendasi', 'status_persetujuan_harga',
                        'tanggal_persetujuan_harga',
                    ])
                pengajuan.save(update_fields=update_fields)
                if hasil == 'lulus' and pengajuan.status == 'inspeksi':
                    pengajuan.lakukan_transisi(
                        'negosiasi', oleh_user=request.user,
                        catatan='Checklist inspeksi lulus; hasil dan rekomendasi harga dikirim ke pemilik.',
                    )
                elif hasil == 'gagal' and pengajuan.produk_terkait_id:
                    pengajuan.produk_terkait.status_stok = 'in_review'
                    pengajuan.produk_terkait.save(update_fields=['status_stok', 'updated_at'])
                elif (
                    hasil == 'lulus'
                    and pengajuan.status == 'tayang'
                    and not pengajuan.transaksi_penjualan.filter(
                        status__in=['menunggu_verifikasi', 'terverifikasi']
                    ).exists()
                ):
                    _buat_atau_perbarui_produk_c2c(pengajuan)

            elif tindakan == 'verifikasi_dokumen':
                dokumen = get_object_or_404(
                    DokumenTitipJual.objects.filter(titip_jual=pengajuan),
                    pk=request.POST.get('dokumen_id'),
                )
                dokumen.sudah_diverifikasi = request.POST.get('sudah_diverifikasi') == '1'
                dokumen.catatan_verifikasi = request.POST.get('catatan_verifikasi', '').strip()
                dokumen.diverifikasi_oleh = request.user
                dokumen.tanggal_verifikasi = timezone.now()
                dokumen.save(update_fields=[
                    'sudah_diverifikasi', 'catatan_verifikasi',
                    'diverifikasi_oleh', 'tanggal_verifikasi',
                ])

            elif tindakan == 'proses_permintaan':
                _proses_permintaan_pemilik(request, pengajuan)

            elif tindakan == 'serah_terima':
                form_serah_terima = SerahTerimaTitipJualForm(request.POST, request.FILES)
                if not form_serah_terima.is_valid():
                    raise ValueError('Data serah-terima tidak valid: ' + form_serah_terima.errors.as_text())
                data_serah = form_serah_terima.cleaned_data
                jenis = data_serah['jenis']
                tujuan_keluar = data_serah['tujuan_keluar']
                transaksi_penjualan = None
                if jenis == 'masuk' and pengajuan.status not in ('disetujui', 'tayang'):
                    raise ValueError('Serah-terima masuk hanya dapat dicatat setelah harga disetujui.')
                if jenis == 'keluar' and tujuan_keluar == 'pemilik':
                    if pengajuan.transaksi_penjualan.filter(
                        status__in=['menunggu_verifikasi', 'terverifikasi']
                    ).exists():
                        raise ValueError('Selesaikan atau tolak transaksi aktif sebelum mengembalikan unit kepada pemilik.')
                if jenis == 'keluar' and tujuan_keluar == 'pembeli':
                    transaksi_id = data_serah.get('transaksi_id')
                    transaksi_penjualan = get_object_or_404(
                        TransaksiTitipJual.objects.filter(
                            titip_jual=pengajuan, status='terverifikasi'
                        ),
                        pk=transaksi_id,
                    )
                if jenis == 'keluar' and tujuan_keluar == 'pemilik' and pengajuan.status not in (
                    'disetujui', 'tayang'
                ):
                    raise ValueError('Serah-terima pengembalian hanya dapat dicatat pada tahap penitipan.')
                if jenis == 'keluar' and tujuan_keluar == 'pembeli' and pengajuan.status != 'tayang':
                    raise ValueError('Serah-terima kepada pembeli hanya dapat dicatat untuk unit yang tayang.')
                kelengkapan = data_serah['kelengkapan_dokumen']
                catatan_kondisi = data_serah['catatan_kondisi'].strip()
                daftar_kelengkapan = ', '.join(
                    dict(SerahTerimaUnit.DOKUMEN_CHOICES)[kode] for kode in kelengkapan
                )
                SerahTerimaUnit.objects.create(
                    titip_jual=pengajuan,
                    jenis=jenis,
                    tujuan_keluar=tujuan_keluar if jenis == 'keluar' else '',
                    tanggal_waktu=timezone.make_aware(
                        data_serah['tanggal_waktu'], timezone.get_current_timezone()
                    ) if timezone.is_naive(data_serah['tanggal_waktu']) else data_serah['tanggal_waktu'],
                    lokasi=data_serah['lokasi'].strip(),
                    nama_penyerah=data_serah['nama_penyerah'].strip(),
                    nama_penerima=data_serah['nama_penerima'].strip(),
                    odometer_saat_ini=data_serah['odometer_saat_ini'],
                    jumlah_kunci=data_serah['jumlah_kunci'],
                    kondisi_unit=data_serah['kondisi_unit'],
                    catatan_kondisi=catatan_kondisi,
                    kelengkapan=daftar_kelengkapan,
                    kelengkapan_dokumen=kelengkapan,
                    foto_kondisi=data_serah['foto_kondisi'],
                    transaksi_penjualan=transaksi_penjualan,
                    dicatat_oleh=request.user,
                    ttd_penyerah='Ditandatangani' if request.POST.get('ttd_penyerah') == '1' else '',
                    ttd_penerima='Ditandatangani' if request.POST.get('ttd_penerima') == '1' else '',
                )

            elif tindakan == 'verifikasi_penjualan_pemilik':
                transaksi_penjualan = get_object_or_404(
                    TransaksiTitipJual.objects.filter(
                        titip_jual=pengajuan,
                        sumber='pemilik',
                        status='menunggu_verifikasi',
                    ),
                    pk=request.POST.get('transaksi_id'),
                )
                form_verifikasi = VerifikasiPenjualanC2CForm(request.POST)
                if not form_verifikasi.is_valid():
                    raise ValueError('Keputusan verifikasi tidak valid: ' + form_verifikasi.errors.as_text())
                keputusan = form_verifikasi.cleaned_data['keputusan']
                catatan_verifikasi = form_verifikasi.cleaned_data['catatan'].strip()
                if keputusan == 'verifikasi':
                    if pengajuan.status != 'tayang':
                        raise ValueError('Pengajuan tidak lagi berada pada tahap penjualan.')
                    if (
                        pengajuan.harga_jual_minimum
                        and transaksi_penjualan.harga_final < pengajuan.harga_jual_minimum
                    ):
                        raise ValueError('Harga transaksi di bawah batas minimum yang disetujui pemilik.')
                    transaksi_penjualan.lakukan_transisi(
                        'terverifikasi', oleh_user=request.user,
                        catatan=catatan_verifikasi or 'Laporan penjualan pemilik diverifikasi MBG.',
                    )
                    if pengajuan.produk_terkait_id:
                        pengajuan.produk_terkait.status_stok = 'in_review'
                        pengajuan.produk_terkait.save(update_fields=['status_stok', 'updated_at'])
                else:
                    transaksi_penjualan.lakukan_transisi(
                        'ditolak', oleh_user=request.user, catatan=catatan_verifikasi,
                    )
                    if (
                        pengajuan.produk_terkait_id
                        and pengajuan.status == 'tayang'
                        and not pengajuan.transaksi_penjualan.filter(
                            status__in=['menunggu_verifikasi', 'terverifikasi']
                        ).exists()
                    ):
                        pengajuan.produk_terkait.status_stok = 'ready'
                        pengajuan.produk_terkait.save(update_fields=['status_stok', 'updated_at'])

            elif tindakan == 'catat_penjualan_mbg':
                pengajuan = TitipJual.objects.select_for_update().get(pk=pengajuan.pk)
                if pengajuan.status != 'tayang':
                    raise ValueError('Penjualan melalui MBG hanya dapat dicatat untuk unit yang sedang tayang.')
                if pengajuan.transaksi_penjualan.filter(
                    status__in=['menunggu_verifikasi', 'terverifikasi', 'selesai']
                ).exists():
                    raise ValueError('Pengajuan sudah memiliki laporan/transaksi aktif atau selesai.')
                form_penjualan = LaporanPenjualanMBGForm(request.POST)
                if not form_penjualan.is_valid():
                    raise ValueError('Data penjualan MBG tidak valid: ' + form_penjualan.errors.as_text())
                harga_final = form_penjualan.cleaned_data['harga_final']
                if pengajuan.harga_jual_minimum and harga_final < pengajuan.harga_jual_minimum:
                    raise ValueError('Harga transaksi di bawah batas minimum yang disetujui pemilik.')
                transaksi_penjualan = TransaksiTitipJual.objects.create(
                    titip_jual=pengajuan,
                    sumber='mbg',
                    nama_pembeli=form_penjualan.cleaned_data['nama_pembeli'],
                    kontak_pembeli=form_penjualan.cleaned_data['kontak_pembeli'],
                    harga_final=harga_final,
                    tanggal_kesepakatan=form_penjualan.cleaned_data['tanggal_kesepakatan'],
                    catatan_pelapor=form_penjualan.cleaned_data['catatan_pelapor'],
                    status='terverifikasi',
                    dilaporkan_oleh=request.user,
                    diverifikasi_oleh=request.user,
                    tanggal_verifikasi=timezone.now(),
                    catatan_verifikasi='Penjualan dicatat oleh MBG.',
                )
                RiwayatTransaksiTitipJual.objects.create(
                    transaksi=transaksi_penjualan,
                    status_dari='',
                    status_ke='terverifikasi',
                    diubah_oleh=request.user,
                    catatan='Penjualan dengan pembeli yang ditemukan MBG dicatat.',
                )
                if pengajuan.produk_terkait_id:
                    pengajuan.produk_terkait.status_stok = 'in_review'
                    pengajuan.produk_terkait.save(update_fields=['status_stok', 'updated_at'])

            elif tindakan == 'selesaikan_penjualan':
                transaksi_penjualan = get_object_or_404(
                    TransaksiTitipJual.objects.filter(titip_jual=pengajuan, status='terverifikasi'),
                    pk=request.POST.get('transaksi_id'),
                )
                form_selesai = SelesaikanPenjualanC2CForm(request.POST)
                if not form_selesai.is_valid():
                    raise ValueError('Data penyelesaian tidak valid: ' + form_selesai.errors.as_text())
                if pengajuan.status != 'tayang':
                    raise ValueError('Pengajuan tidak berada pada tahap penjualan.')
                perjanjian_valid = pengajuan.dokumen.filter(
                    jenis='perjanjian', sudah_diverifikasi=True
                ).exists()
                if not perjanjian_valid:
                    raise ValueError('Perjanjian titip-jual yang sudah diverifikasi wajib diperiksa sebelum penyelesaian.')
                serah_keluar = getattr(transaksi_penjualan, 'serah_terima_keluar', None)
                if (
                    not serah_keluar
                    or serah_keluar.jenis != 'keluar'
                    or serah_keluar.tujuan_keluar != 'pembeli'
                    or not serah_keluar.lengkap
                ):
                    raise ValueError('Serah-terima lengkap kepada pembeli wajib dicatat sebelum penyelesaian.')
                if form_selesai.cleaned_data['komisi_final'] > transaksi_penjualan.harga_final:
                    raise ValueError('Komisi tidak boleh lebih besar dari harga transaksi.')
                now = timezone.now()
                transaksi_penjualan.komisi_final = form_selesai.cleaned_data['komisi_final']
                transaksi_penjualan.komisi_diperiksa_berdasarkan = (
                    form_selesai.cleaned_data['komisi_diperiksa_berdasarkan']
                )
                transaksi_penjualan.komisi_diperiksa_oleh = request.user
                transaksi_penjualan.tanggal_komisi_diperiksa = now
                transaksi_penjualan.catatan_administrasi = form_selesai.cleaned_data['catatan_administrasi'].strip()
                transaksi_penjualan.administrasi_diselesaikan_oleh = request.user
                transaksi_penjualan.tanggal_administrasi_selesai = now
                transaksi_penjualan.save(update_fields=[
                    'komisi_final', 'komisi_diperiksa_berdasarkan',
                    'komisi_diperiksa_oleh', 'tanggal_komisi_diperiksa',
                    'catatan_administrasi', 'administrasi_diselesaikan_oleh',
                    'tanggal_administrasi_selesai', 'updated_at',
                ])
                transaksi_penjualan.lakukan_transisi(
                    'selesai', oleh_user=request.user,
                    catatan='Penjualan, komisi sesuai perjanjian, dan administrasi diselesaikan.',
                )
                pengajuan.lakukan_transisi(
                    'terjual', oleh_user=request.user,
                    catatan=f'Penjualan C2C selesai; sumber pembeli: {transaksi_penjualan.get_sumber_display()}.',
                )
                if pengajuan.produk_terkait_id:
                    pengajuan.produk_terkait.status_stok = 'sold'
                    pengajuan.produk_terkait.save(update_fields=['status_stok', 'updated_at'])

            elif tindakan == 'unggah_dokumen':
                berkas = request.FILES.get('file')
                jenis = request.POST.get('jenis_dokumen')
                if jenis not in DOKUMEN_TITIP_JUAL_ADMIN_UPLOAD or not berkas:
                    raise ValueError('Pilih jenis dokumen dan file yang valid.')
                validasi_dokumen_titip_jual(berkas)
                ekstensi = os.path.splitext(berkas.name)[1].lower()
                DokumenTitipJual.objects.create(
                    titip_jual=pengajuan,
                    jenis=jenis,
                    nama_file=f'{uuid.uuid4().hex}{ekstensi}',
                    file=berkas,
                    diunggah_oleh=request.user,
                )

            else:
                raise ValueError('Tindakan C2C tidak dikenali.')
        messages.success(request, 'Perubahan pengajuan Titip-Jual berhasil disimpan.')
    except (ValueError, ValidationError, OverflowError) as error:
        messages.error(request, str(error))
    return redirect('admin_titip_jual_detail', pengajuan_id=pengajuan.pk)


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
            if form.cleaned_data['tipe_penjual'] == 'C2C':
                form.add_error(
                    'tipe_penjual',
                    'Unit C2C hanya dapat dibuat dan dikelola melalui proses Titip-Jual.',
                )
                return form, None
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
    if produk.tipe_penjual == 'C2C' or produk.titip_jual_sumber_id:
        messages.error(request, 'Produk C2C hanya dapat dikelola melalui panel Titip-Jual C2C.')
        if produk.titip_jual_sumber_id:
            return redirect('admin_titip_jual_detail', pengajuan_id=produk.titip_jual_sumber_id)
        return redirect('admin_katalog')
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
    if produk.tipe_penjual == 'C2C' or produk.titip_jual_sumber_id:
        messages.error(request, 'Status unit C2C hanya dapat diubah melalui transisi Titip-Jual.')
    elif status == 'returned':
        messages.error(request, 'Status pengembalian hanya dapat dicatat melalui serah-terima C2C.')
    elif status not in dict(Produk.STATUS_STOK_CHOICES):
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
    if produk.tipe_penjual == 'C2C' or produk.titip_jual_sumber_id:
        messages.error(request, 'Produk C2C tidak dapat dihapus dari katalog umum.')
        return redirect('admin_katalog')
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
    if produk.tipe_penjual == 'C2C' or produk.titip_jual_sumber_id:
        messages.error(request, 'Foto produk C2C dikelola melalui pengajuan Titip-Jual.')
        return redirect('admin_katalog')
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
    if produk.tipe_penjual == 'C2C' or produk.titip_jual_sumber_id:
        messages.error(request, 'Foto produk C2C dikelola melalui pengajuan Titip-Jual.')
        return redirect('admin_katalog')
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
