import json
import logging
import os
import uuid
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from django.conf import settings
from django.contrib.auth.models import Group
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth import login, logout, update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.core.paginator import Paginator
from django.db import DatabaseError, connection, transaction
from django.db.models import BooleanField, Case, Count, Exists, OuterRef, Q, Subquery, Value, When
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import get_valid_filename
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from functools import wraps
from urllib.parse import urlencode

from .midtrans_service import (
    MidtransStatusError,
    buat_snap_transaksi,
    proses_notifikasi_webhook,
    sinkronkan_status_midtrans,
)

from .models import (
    Produk, FotoMobil, TitipJual, InquiryC2C, FotoTitipJual, DokumenTitipJual,
    RiwayatStatusTitipJual, PermintaanTitipJual, SerahTerimaUnit,
    ChecklistInspeksiC2C, InspeksiC2C, HasilChecklistInspeksiC2C,
    TransaksiTitipJual, RiwayatTransaksiTitipJual,
    BookingCekUnit, Transaksi,
    KategoriArtikel, Artikel, HalamanWebsite, HeroSlide, Mitra, PengaturanWebsite,
    PesananPembelian, PembayaranPesanan, PengaturanPembelian, RiwayatPesanan,
    RefundPesanan, PERSENTASE_DP_KREDIT, PERSENTASE_ANGSURAN_KREDIT_TAHUNAN,
    normalisasi_whatsapp,
)
from .forms import (
    LoginForm, RegisterForm, UserProfileForm, AdminProfileForm, AdminUserForm, UbahPasswordForm,
    ProdukForm, PengajuanTitipJualForm, InquiryPembeliC2CForm, AdminInquiryC2CForm,
    HasilInspeksiC2CForm,
    LaporanPenjualanPemilikForm, LaporanPenjualanMBGForm,
    VerifikasiPenjualanC2CForm, SerahTerimaTitipJualForm,
    SelesaikanPenjualanC2CForm, validasi_foto_mobil,
    validasi_dokumen_titip_jual,
    KategoriArtikelForm, ArtikelForm, HalamanWebsiteForm, HeroSlideForm, MitraForm, PengaturanWebsiteForm,
    CheckoutPembelianForm, PembayaranManualPesananForm,
    KanalPembayaranPembelianForm, KonfirmasiTransferPesananForm,
    PermintaanPembatalanPesananForm, BookingCekUnitForm,
)

User = get_user_model()
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 1. HALAMAN PUBLIK
# ═══════════════════════════════════════════════════════════════════

JUMLAH_MAKS_SLIDE = 6           # jumlah maksimal slide hero yang tampil di beranda
JUMLAH_MAKS_MITRA = 12          # jumlah maksimal mitra yang tampil di beranda
KATALOG_PER_HALAMAN = 9       # jumlah kartu per halaman di katalog

# Aturan bisnis "tayang untuk publik": hanya unit berstatus Ready Stock
# (sama dengan aturan yang sudah dipakai beranda dan "mobil terkait").
STATUS_TAYANG_PUBLIK = 'ready'
DOKUMEN_TITIP_JUAL_ADMIN_UPLOAD = ('perjanjian', 'hasil_inspeksi')


def produk_tayang(termasuk_in_review=False):
    """
    Unit tayang untuk publik:
    - Default (termasuk_in_review=False): hanya unit berstatus 'ready' yang tayang (dipakai untuk Beranda).
    - Jika termasuk_in_review=True: menyertakan unit berstatus 'in_review' (sedang di-booking),
      namun unit terjual ('sold') atau dikembalikan ('returned') tetap tidak ditampilkan (dipakai untuk Katalog).
    """
    status_filter = ['ready', 'in_review'] if termasuk_in_review else ['ready']
    tabel_c2c = {
        TitipJual._meta.db_table,
        FotoTitipJual._meta.db_table,
        SerahTerimaUnit._meta.db_table,
        InspeksiC2C._meta.db_table,
    }
    if not tabel_c2c.issubset(set(connection.introspection.table_names())):
        return Produk.objects.filter(
            status_stok__in=status_filter,
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
        Produk.objects.filter(status_stok__in=status_filter)
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
    Beranda: hanya menampilkan unit yang berstatus Ready Stock dan belum terbooking di carousel.
    Unit dengan pesanan/booking aktif maupun unit terjual/dikembalikan kedua-duanya WAJIB TIDAK TAMPIL.
    """
    pesanan_aktif_unit = PesananPembelian.objects.filter(
        produk_id=OuterRef('pk'),
        status__in=PesananPembelian.STATUS_AKTIF,
    )
    booking_unit = BookingCekUnit.objects.filter(
        produk_id=OuterRef('pk'),
        status__in=('menunggu', 'menunggu_dp', 'terkonfirmasi', 'disetujui'),
    )
    unit_tayang = produk_tayang(termasuk_in_review=False).annotate(
        sedang_dipesan=Case(
            When(status_stok='in_review', then=Value(True)),
            default=Exists(pesanan_aktif_unit) | Exists(booking_unit),
            output_field=BooleanField(),
        ),
    ).filter(status_stok='ready', sedang_dipesan=False)
    katalog_mobil = unit_tayang.order_by('-created_at', '-id')[:5]

    # Total unit di katalog (termasuk unit yang sedang di-booking) untuk tombol navigasi ke katalog
    total_katalog = produk_tayang(termasuk_in_review=True).count()

    return render(request, 'beranda.html', {
        'katalog_mobil': katalog_mobil,
        'total_unit_tayang': total_katalog,
        'hero_slides': hero_slide_tayang(),
        'mitra_list': mitra_tayang(),
        'persentase_dp_kredit': PERSENTASE_DP_KREDIT,
        'persentase_angsuran_kredit_tahunan': PERSENTASE_ANGSURAN_KREDIT_TAHUNAN,
    })


def jual_titip(request):
    """Landing publik untuk layanan Titip-Jual, memakai pengajuan yang sudah ada."""
    url_pengajuan = reverse('titip_jual_tambah')
    if request.user.is_authenticated:
        url_mulai = url_pengajuan
    else:
        url_mulai = f"{reverse('login')}?{urlencode({'next': url_pengajuan})}"

    return render(request, 'jual_titip.html', {
        'url_mulai_titip_jual': url_mulai,
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
    Katalog publik: seluruh unit tayang (ready maupun yang sedang di-booking) + pencarian, filter, pengurutan, pagination.
    Unit yang sedang di-booking tetap ditampilkan (dengan penanda 'Unit sedang diproses'),
    sedangkan unit yang sudah terjual ('sold') atau dikembalikan ('returned') tidak ditampilkan.
    Semua parameter GET divalidasi; nilai tidak valid diabaikan (tidak error).
    """
    pesanan_aktif_unit = PesananPembelian.objects.filter(
        produk_id=OuterRef('pk'),
        status__in=PesananPembelian.STATUS_AKTIF,
    )
    booking_unit = BookingCekUnit.objects.filter(
        produk_id=OuterRef('pk'),
        status__in=('menunggu', 'menunggu_dp', 'terkonfirmasi', 'disetujui'),
    )
    dasar = produk_tayang(termasuk_in_review=True).annotate(
        sedang_dipesan=Case(
            When(status_stok='in_review', then=Value(True)),
            default=Exists(pesanan_aktif_unit) | Exists(booking_unit),
            output_field=BooleanField(),
        ),
    )
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
        'hero_slides': hero_slide_tayang(),
    })


def detail_produk(request, id):
    """
    Menampilkan halaman detail spesifikasi lengkap mobil MBG.
    """
    produk = produk_tayang(termasuk_in_review=True).filter(id=id).first()
    if produk is None:
        produk = get_object_or_404(Produk, pk=id)
    return render(request, 'detail_mobil.html', _konteks_detail_produk(request, produk))


def _produk_c2c_menerima_inquiry(produk, titip_jual=None):
    titip_jual = titip_jual or produk.titip_jual_sumber
    return bool(
        produk.tipe_penjual == 'C2C'
        and produk.status_stok == 'ready'
        and titip_jual
        and titip_jual.status == 'tayang'
        and titip_jual.produk_terkait_id == produk.pk
        and not titip_jual.transaksi_penjualan.filter(
            status__in=['menunggu_verifikasi', 'terverifikasi', 'selesai']
        ).exists()
    )


def _konteks_detail_produk(request, produk, inquiry_form=None, booking_form=None):
    pesanan_aktif_terkait = PesananPembelian.objects.filter(
        produk_id=OuterRef('pk'),
        status__in=PesananPembelian.STATUS_AKTIF,
    )
    booking_terkait = BookingCekUnit.objects.filter(
        produk_id=OuterRef('pk'),
        status__in=('menunggu', 'menunggu_dp', 'terkonfirmasi', 'disetujui'),
    )
    mobil_terkait = (
        produk_tayang()
        .exclude(id=produk.id)
        .annotate(
            sedang_dipesan=Exists(pesanan_aktif_terkait) | Exists(booking_terkait)
        )
        .filter(sedang_dipesan=False)[:3]
    )
    pesanan_aktif = None
    unit_dapat_dipesan = False
    pesanan_aktif_milik_pengguna = False
    unit_dipesan_pelanggan_lain = False
    unit_memiliki_booking = False
    if produk.tipe_penjual == 'B2C':
        pesanan_aktif = PesananPembelian.pesanan_aktif_unit(produk)
        unit_dapat_dipesan = produk.status_stok == 'ready' and pesanan_aktif is None
        unit_memiliki_booking = bool(
            pesanan_aktif or produk.status_stok == 'in_review'
        )
        pesanan_aktif_milik_pengguna = bool(
            pesanan_aktif
            and request.user.is_authenticated
            and pesanan_aktif.pelanggan_id == request.user.id
        )
        unit_dipesan_pelanggan_lain = bool(
            not pesanan_aktif_milik_pengguna
            and (pesanan_aktif or produk.status_stok == 'in_review')
        )

    inquiry_bisa_dikirim = (
        produk.tipe_penjual == 'C2C'
        and _produk_c2c_menerima_inquiry(produk)
    )
    booking_saya = None
    if request.user.is_authenticated:
        booking_saya = BookingCekUnit.objects.filter(
            produk=produk,
            user=request.user,
            status__in=['menunggu', 'menunggu_dp', 'terkonfirmasi', 'disetujui']
        ).first()

    # Kumpulkan seluruh galeri foto kendaraan (mendukung C2C dari TitipJual & B2C dari FotoMobil)
    galeri_foto_list = []
    if produk.tipe_penjual == 'C2C' and getattr(produk, 'titip_jual_sumber_id', None) and produk.titip_jual_sumber:
        for f in produk.titip_jual_sumber.galeri_foto.all().order_by('urutan', 'id'):
            if f.gambar:
                galeri_foto_list.append({
                    'url': f.gambar.url,
                    'label': f.get_jenis_foto_display(),
                })
        # Pastikan gambar_utama terdaftar jika belum masuk
        if produk.gambar_utama and not any(item['url'] == produk.gambar_utama.url for item in galeri_foto_list):
            galeri_foto_list.insert(0, {
                'url': produk.gambar_utama.url,
                'label': 'Tampak Depan',
            })
    else:
        if produk.gambar_utama:
            galeri_foto_list.append({
                'url': produk.gambar_utama.url,
                'label': 'Foto Utama',
            })
        for f in produk.galeri_foto.all():
            if f.gambar:
                url_f = f.gambar.url
                if not any(item['url'] == url_f for item in galeri_foto_list):
                    galeri_foto_list.append({
                        'url': url_f,
                        'label': f.keterangan or 'Detail Kendaraan',
                    })

    return {
        'produk': produk,
        'mobil': produk,
        'mobil_terkait': mobil_terkait,
        'pesanan_aktif': pesanan_aktif,
        'unit_dapat_dipesan': unit_dapat_dipesan,
        'pesanan_aktif_milik_pengguna': pesanan_aktif_milik_pengguna,
        'unit_dipesan_pelanggan_lain': unit_dipesan_pelanggan_lain,
        'unit_memiliki_booking': unit_memiliki_booking,
        'booking_saya': booking_saya,
        'galeri_foto_list': galeri_foto_list,
        'url_status_ketersediaan': reverse('status_ketersediaan_unit', args=[produk.id]),
        'inquiry_bisa_dikirim': inquiry_bisa_dikirim,
        'inquiry_form': inquiry_form or (
            InquiryPembeliC2CForm(user=request.user) if inquiry_bisa_dikirim else None
        ),
        'booking_form': booking_form or BookingCekUnitForm(user=request.user),
        'booking_sukses_data': request.session.pop('booking_sukses_data', None),
    }


def ajukan_jadwal_cek_unit(request, id):
    """
    Pengajuan Jadwal Cek Unit (O2O) oleh pelanggan untuk mobil tertentu.
    Status awal selalu 'menunggu' (Menunggu Konfirmasi Admin), tidak otomatis disetujui.
    Admin yang akan memverifikasi dan menyetujui jadwal tersebut di panel admin.
    Hanya unit berstatus 'ready' dan tanpa pesanan aktif yang dapat dijadwalkan cek unit.
    """
    produk = get_object_or_404(Produk, pk=id)
    pesanan_aktif = PesananPembelian.pesanan_aktif_unit(produk) if produk.tipe_penjual == 'B2C' else None
    if produk.status_stok != 'ready' or pesanan_aktif is not None:
        messages.error(
            request,
            'Maaf, unit mobil ini sedang di-booking/dipesan oleh pelanggan lain. '
            'Pengajuan jadwal cek unit ditutup sementara sampai status unit kembali Ready oleh admin.'
        )
        return redirect('detail_produk', id=produk.id)

    if request.method == 'POST':
        form = BookingCekUnitForm(request.POST, user=request.user)
        if form.is_valid():
            booking = form.save(commit=False)
            booking.produk = produk
            if request.user.is_authenticated:
                booking.user = request.user
            booking.status = 'menunggu'
            booking.save()

            request.session['booking_sukses_data'] = {
                'id': booking.id,
                'mobil': produk.nama,
                'sku': produk.sku,
                'tanggal': booking.tanggal_cek_unit.strftime('%d %B %Y'),
                'jam': str(booking.jam_cek_unit)[:5],
                'opsi': booking.get_opsi_delivery_display(),
                'nama': booking.nama_pemesan,
                'whatsapp': booking.no_whatsapp,
            }
            messages.success(
                request,
                f"Pengajuan jadwal cek unit #{booking.id} berhasil dikirim! "
                "Jadwal Anda sedang menunggu verifikasi admin Garasi MBG."
            )
            return redirect(f"{reverse('detail_produk', args=[produk.id])}#jadwal-sukses")
        else:
            messages.error(request, 'Terdapat kesalahan pada isian formulir jadwal. Silakan periksa kembali.')
            return render(
                request, 'detail_mobil.html',
                _konteks_detail_produk(request, produk, booking_form=form)
            )

    return redirect('detail_produk', id=produk.id)


@require_POST
def kirim_inquiry_c2c(request, id):
    produk = get_object_or_404(
        produk_tayang().select_related('titip_jual_sumber').filter(tipe_penjual='C2C'),
        pk=id,
    )
    form = InquiryPembeliC2CForm(request.POST, user=request.user)
    if not form.is_valid():
        return render(
            request, 'detail_mobil.html',
            _konteks_detail_produk(request, produk, inquiry_form=form),
        )

    titip_jual = produk.titip_jual_sumber
    if not titip_jual:
        raise Http404
    data = form.cleaned_data
    with transaction.atomic():
        titip_jual = get_object_or_404(
            TitipJual.objects.select_for_update(), pk=titip_jual.pk,
        )
        produk = get_object_or_404(
            Produk.objects.select_for_update(), pk=produk.pk, tipe_penjual='C2C',
        )
        if (
            not produk_tayang().filter(pk=produk.pk).exists()
            or not _produk_c2c_menerima_inquiry(produk, titip_jual)
        ):
            raise Http404

        batas_duplikat = timezone.now() - timedelta(hours=24)
        inquiry_ada = InquiryC2C.objects.filter(
            produk=produk,
            whatsapp=data['whatsapp'],
            created_at__gte=batas_duplikat,
        ).exists()
        if inquiry_ada:
            messages.info(
                request,
                'Inquiry untuk kendaraan ini sudah tercatat dalam 24 jam terakhir.',
            )
        else:
            InquiryC2C.objects.create(
                produk=produk,
                titip_jual=titip_jual,
                pengguna=request.user if request.user.is_authenticated else None,
                nama_pembeli=data['nama_pembeli'],
                whatsapp=data['whatsapp'],
                email=data['email'],
                pesan=data['pesan'],
            )

    pesan_whatsapp = (
        f"Halo, saya {data['nama_pembeli']} ingin bertanya tentang "
        f"{produk.nama} (SKU {produk.sku})."
    )
    if data['pesan']:
        pesan_whatsapp += f"\nPertanyaan: {data['pesan']}"
    pesan_whatsapp += f"\nWhatsApp saya: {data['whatsapp']}"
    if data['email']:
        pesan_whatsapp += f"\nEmail saya: {data['email']}"
    nomor_pemilik = normalisasi_whatsapp(titip_jual.no_whatsapp)
    return redirect(f'https://wa.me/{nomor_pemilik}?{urlencode({"text": pesan_whatsapp})}')


@require_GET
def status_ketersediaan_unit(request, id):
    """Return only public availability information for a B2C unit."""
    produk = get_object_or_404(Produk, pk=id, tipe_penjual='B2C')
    pesanan_aktif = PesananPembelian.pesanan_aktif_unit(produk)
    pesanan_milik_pengguna = bool(
        pesanan_aktif
        and request.user.is_authenticated
        and pesanan_aktif.pelanggan_id == request.user.id
    )
    tersedia = produk.status_stok == 'ready' and pesanan_aktif is None

    if tersedia:
        state = 'available'
        pesan = 'Unit tersedia untuk dipesan.'
    elif pesanan_aktif and pesanan_milik_pengguna:
        state = 'reserved_by_you'
        pesan = 'Anda sudah memiliki pesanan aktif untuk unit ini.'
    elif pesanan_aktif or produk.status_stok == 'in_review':
        state = 'booked'
        pesan = 'Unit sedang dalam proses pemesanan oleh pelanggan lain.'
    else:
        state = 'unavailable'
        pesan = 'Unit ini tidak tersedia untuk dipesan.'

    response = {
        'status': 'success',
        'state': state,
        'available': tersedia,
        'reserved_by_you': pesanan_milik_pengguna,
        'message': pesan,
    }
    if pesanan_milik_pengguna:
        response['order_url'] = reverse(
            'pesanan_detail', args=[pesanan_aktif.nomor_pesanan],
        )
    return JsonResponse(response)


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
    filter_q = Q(user=user)
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
    user_inquiry_c2c = (
        InquiryC2C.objects.filter(titip_jual__pengguna=user)
        .select_related('produk', 'titip_jual')
        .order_by('-created_at', '-pk')
    )
    user_pesanan_pembelian = (
        PesananPembelian.objects.filter(pelanggan=user)
        .select_related('produk')
        .prefetch_related('pembayaran')
        .order_by('-created_at', '-pk')
    )
    user_booking_pembelian = user_pesanan_pembelian.filter(jenis_pembelian='booking')
    user_pembelian_langsung = user_pesanan_pembelian.filter(jenis_pembelian='beli_langsung')
    context = {
        'user': user,
        'user_bookings': user_bookings,
        'user_titip_jual': user_titip_jual,
        'user_inquiry_c2c': user_inquiry_c2c,
        'user_transaksi': user_transaksi,
        'user_pesanan_pembelian': user_pesanan_pembelian,
        'user_booking_pembelian': user_booking_pembelian,
        'user_pembelian_langsung': user_pembelian_langsung,
        'total_booking': user_bookings.count(),
        'total_titip_jual': user_titip_jual.count(),
        'total_pesanan_pembelian': user_pesanan_pembelian.count(),
        'total_booking_pembelian': user_booking_pembelian.count(),
        'total_pembelian_langsung': user_pembelian_langsung.count(),
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
    if dokumen.titip_jual.pengguna_id != request.user.pk and not (
        request.user.is_superuser
        or (
            request.user.is_staff
            and request.user.has_perm('toko.manage_documents_titipjual')
        )
    ):
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
    if serah_terima.titip_jual.pengguna_id != request.user.pk and not (
        request.user.is_superuser
        or (
            request.user.is_staff
            and request.user.has_perm('toko.manage_handover_titipjual')
        )
    ):
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
@require_GET
def bukti_transfer_pesanan_lihat(request, pembayaran_id):
    pembayaran = get_object_or_404(
        PembayaranPesanan.objects.select_related('pesanan'),
        pk=pembayaran_id,
    )
    if pembayaran.pesanan.pelanggan_id != request.user.pk and not (
        request.user.is_staff or request.user.is_superuser
    ):
        raise Http404
    if not pembayaran.bukti_transfer:
        raise Http404
    try:
        berkas = pembayaran.bukti_transfer.open('rb')
    except (FileNotFoundError, OSError, ValueError):
        raise Http404

    ekstensi = os.path.splitext(pembayaran.bukti_transfer.name)[1].lower()
    jenis_konten = {
        '.pdf': 'application/pdf',
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.webp': 'image/webp',
    }.get(ekstensi, 'application/octet-stream')
    response = FileResponse(
        berkas,
        as_attachment=False,
        filename=f'bukti-transfer-{pembayaran.pk}{ekstensi}',
        content_type=jenis_konten,
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
    Statistik bisnis riil dari database (Katalog, Pengguna, Booking, Titip-Jual, Pesanan).
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
    pembayaran_pesanan_menunggu_verifikasi = PembayaranPesanan.objects.filter(
        status='menunggu_verifikasi',
    ).count()

    can_view_c2c = (
        request.user.is_superuser
        or request.user.has_perm('toko.view_titipjual')
    )
    if can_view_c2c:
        total_titip_jual = TitipJual.objects.count()
        titip_jual_pending = TitipJual.objects.filter(status__in=['pending', 'revisi']).count()
        titip_jual_disetujui = TitipJual.objects.filter(status__in=['disetujui', 'tayang']).count()
    else:
        total_titip_jual = None
        titip_jual_pending = None
        titip_jual_disetujui = None

    # Statistik transaksi pembelian B2C dan pembayaran yang terkait langsung ke pesanan.
    total_transaksi = PesananPembelian.objects.count()
    transaksi_berhasil = PembayaranPesanan.objects.filter(status='terverifikasi').count()
    transaksi_pending = PesananPembelian.objects.filter(
        status__in=('menunggu_booking_fee', 'menunggu_kredit', 'menunggu_dp_kredit'),
    ).count()

    # Ringkasan data terbaru untuk quick monitoring
    recent_booking = BookingCekUnit.objects.select_related('produk').order_by('-created_at')[:5]
    recent_titip_jual = (
        TitipJual.objects.order_by('-created_at')[:5]
        if can_view_c2c else TitipJual.objects.none()
    )
    recent_transaksi = list(
        PesananPembelian.objects.select_related('produk', 'pelanggan')
        .prefetch_related('pembayaran')
        .order_by('-created_at', '-pk')[:5]
    )
    for pesanan in recent_transaksi:
        pembayaran = list(pesanan.pembayaran.all())
        pesanan.pembayaran_terakhir = pembayaran[0] if pembayaran else None
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
        'pembayaran_pesanan_menunggu_verifikasi': pembayaran_pesanan_menunggu_verifikasi,
        'total_titip_jual': total_titip_jual,
        'titip_jual_pending': titip_jual_pending,
        'titip_jual_disetujui': titip_jual_disetujui,
        'can_view_c2c': can_view_c2c,
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


def _pastikan_izin_pengguna(request, izin):
    if not request.user.is_superuser and not request.user.has_perm(f'toko.{izin}_customuser'):
        raise PermissionDenied


def _konteks_izin_pengguna(request):
    return {
        'boleh_tambah_pengguna': request.user.is_superuser or request.user.has_perm('toko.add_customuser'),
        'boleh_ubah_pengguna': request.user.is_superuser or request.user.has_perm('toko.change_customuser'),
        'boleh_kelola_group': request.user.is_superuser or request.user.has_perm('auth.change_group'),
    }


@staf_required
def admin_pengguna(request):
    _pastikan_izin_pengguna(request, 'view')
    daftar = User.objects.prefetch_related('groups').order_by('-date_joined', '-pk')
    params = {}

    q = ' '.join(request.GET.get('q', '').split())[:100]
    if q:
        params['q'] = q
        for kata in q.split()[:5]:
            daftar = daftar.filter(
                Q(username__icontains=kata)
                | Q(first_name__icontains=kata)
                | Q(last_name__icontains=kata)
                | Q(email__icontains=kata)
            )

    status = request.GET.get('status', '')
    if status in ('aktif', 'nonaktif'):
        params['status'] = status
        daftar = daftar.filter(is_active=(status == 'aktif'))

    group_id = request.GET.get('group', '')
    if group_id.isdigit() and Group.objects.filter(pk=group_id).exists():
        params['group'] = group_id
        daftar = daftar.filter(groups__pk=group_id)

    halaman = Paginator(daftar.distinct(), 20).get_page(request.GET.get('page'))
    context = {
        'page_obj': halaman,
        'pengguna_list': halaman.object_list,
        'total_hasil': halaman.paginator.count,
        'querystring': urlencode(params),
        'nilai': params,
        'groups': Group.objects.order_by('name'),
        'total_pengguna': User.objects.count(),
        'pengguna_aktif': User.objects.filter(is_active=True).count(),
        'total_staff': User.objects.filter(is_staff=True).count(),
        'total_superuser': User.objects.filter(is_superuser=True).count(),
    }
    context.update(_konteks_izin_pengguna(request))
    return render(request, 'dashboard/admin/pengguna_list.html', context)


@staf_required
def admin_pengguna_tambah(request):
    _pastikan_izin_pengguna(request, 'add')
    can_manage_groups = request.user.is_superuser or request.user.has_perm('auth.change_group')
    form = AdminUserForm(
        request.POST if request.method == 'POST' else None,
        can_manage_groups=can_manage_groups,
        actor=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        pengguna = form.save()
        messages.success(request, f'Akun {pengguna.username} berhasil ditambahkan.')
        return redirect('admin_pengguna_detail', pengguna_id=pengguna.pk)
    return render(request, 'dashboard/admin/pengguna_form.html', {
        'form': form,
        'judul': 'Tambah Pengguna',
        'pengguna': None,
        'boleh_kelola_group': can_manage_groups,
    })


@staf_required
def admin_pengguna_detail(request, pengguna_id):
    _pastikan_izin_pengguna(request, 'view')
    pengguna = get_object_or_404(User.objects.prefetch_related('groups'), pk=pengguna_id)
    context = {'pengguna': pengguna}
    context.update(_konteks_izin_pengguna(request))
    return render(request, 'dashboard/admin/pengguna_detail.html', context)


@staf_required
def admin_pengguna_edit(request, pengguna_id):
    _pastikan_izin_pengguna(request, 'change')
    pengguna = get_object_or_404(User, pk=pengguna_id)
    can_manage_groups = request.user.is_superuser or request.user.has_perm('auth.change_group')
    form = AdminUserForm(
        request.POST if request.method == 'POST' else None,
        instance=pengguna,
        can_manage_groups=can_manage_groups,
        actor=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'Akun {pengguna.username} berhasil diperbarui.')
        return redirect('admin_pengguna_detail', pengguna_id=pengguna.pk)
    return render(request, 'dashboard/admin/pengguna_form.html', {
        'form': form,
        'judul': 'Ubah Data Pengguna',
        'pengguna': pengguna,
        'boleh_kelola_group': can_manage_groups,
    })


@staf_required
@require_POST
def admin_pengguna_status(request, pengguna_id):
    _pastikan_izin_pengguna(request, 'change')
    pengguna = get_object_or_404(User, pk=pengguna_id)
    if pengguna.pk == request.user.pk:
        messages.error(request, 'Anda tidak dapat menonaktifkan akun yang sedang digunakan.')
    elif (
        pengguna.is_active
        and pengguna.is_superuser
        and User.objects.filter(is_active=True, is_superuser=True).exclude(pk=pengguna.pk).count() == 0
    ):
        messages.error(request, 'Akun superuser aktif terakhir tidak dapat dinonaktifkan.')
    else:
        pengguna.is_active = not pengguna.is_active
        pengguna.save(update_fields=['is_active'])
        label = 'diaktifkan' if pengguna.is_active else 'dinonaktifkan'
        messages.success(request, f'Akun {pengguna.username} berhasil {label}.')
    return redirect('admin_pengguna')


def _periksa_izin_tindakan_c2c(request, tindakan, pengajuan):
    izin_aksi = {
        'status': 'change_status_titipjual',
        'jadwalkan_inspeksi': 'inspect_titipjual',
        'mulai_inspeksi': 'inspect_titipjual',
        'hasil_inspeksi': 'inspect_titipjual',
        'verifikasi_dokumen': 'manage_documents_titipjual',
        'unggah_dokumen': 'manage_documents_titipjual',
        'serah_terima': 'manage_handover_titipjual',
        'verifikasi_penjualan_pemilik': 'manage_sales_titipjual',
        'catat_penjualan_mbg': 'manage_sales_titipjual',
        'selesaikan_penjualan': 'manage_sales_titipjual',
    }
    if tindakan == 'proses_permintaan':
        permintaan = get_object_or_404(
            PermintaanTitipJual.objects.filter(
                titip_jual=pengajuan,
                status='menunggu',
            ),
            pk=request.POST.get('permintaan_id'),
        )
        permission = (
            'manage_price_titipjual'
            if permintaan.jenis == 'ubah_harga'
            else 'change_status_titipjual'
        )
    else:
        permission = izin_aksi.get(tindakan)
    if permission is None or (
        not request.user.is_superuser
        and not request.user.has_perm(f'toko.{permission}')
    ):
        raise PermissionDenied


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


@c2c_permission_required('view')
def admin_inquiry_c2c(request):
    daftar = (
        InquiryC2C.objects.select_related(
            'produk', 'titip_jual', 'titip_jual__pengguna', 'ditangani_oleh',
        )
        .order_by('-created_at', '-pk')
    )
    q = ' '.join(request.GET.get('q', '').split())[:100]
    if q:
        for kata in q.split()[:5]:
            daftar = daftar.filter(
                Q(nama_pembeli__icontains=kata)
                | Q(whatsapp__icontains=kata)
                | Q(email__icontains=kata)
                | Q(produk__nama__icontains=kata)
                | Q(produk__sku__icontains=kata)
                | Q(titip_jual__nama_pemilik__icontains=kata)
                | Q(titip_jual__pengguna__username__icontains=kata)
            )
    status_filter = request.GET.get('status', '')
    if status_filter in dict(InquiryC2C.STATUS_CHOICES):
        daftar = daftar.filter(status=status_filter)

    halaman = Paginator(daftar, 15).get_page(request.GET.get('page'))
    return render(request, 'dashboard/admin/inquiry_c2c_list.html', {
        'inquiry_list': halaman,
        'q': q,
        'status_filter': status_filter,
        'status_choices': InquiryC2C.STATUS_CHOICES,
        'can_manage_inquiry_c2c': (
            request.user.is_superuser
            or request.user.has_perm('toko.change_status_titipjual')
        ),
    })


@c2c_permission_required('view')
@require_POST
def admin_inquiry_c2c_status(request, inquiry_id):
    if (
        not request.user.is_superuser
        and not request.user.has_perm('toko.change_status_titipjual')
    ):
        raise PermissionDenied
    inquiry = get_object_or_404(InquiryC2C, pk=inquiry_id)
    form = AdminInquiryC2CForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Perubahan inquiry tidak valid: ' + form.errors.as_text())
        return redirect('admin_inquiry_c2c')

    inquiry.status = form.cleaned_data['status']
    inquiry.catatan_admin = form.cleaned_data['catatan_admin'].strip()
    inquiry.ditangani_oleh = request.user
    inquiry.save(update_fields=[
        'status', 'catatan_admin', 'ditangani_oleh', 'updated_at',
    ])
    messages.success(request, 'Inquiry calon pembeli berhasil diperbarui.')
    return redirect('admin_inquiry_c2c')


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
            lulus_inspeksi=inspeksi.status == 'lulus',
            ringkasan_inspeksi=pengajuan.hasil_inspeksi or 'Checklist inspeksi lulus.',
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
        product.lulus_inspeksi = inspeksi.status == 'lulus'
        product.ringkasan_inspeksi = (
            pengajuan.hasil_inspeksi or 'Checklist inspeksi lulus.'
        )
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


@c2c_permission_required('view')
@require_POST
def admin_titip_jual_tindakan(request, pengajuan_id):
    pengajuan = get_object_or_404(
        TitipJual.objects.select_related('produk_terkait').prefetch_related('galeri_foto'),
        pk=pengajuan_id,
    )
    tindakan = request.POST.get('aksi') or request.POST.get('tindakan')
    _periksa_izin_tindakan_c2c(request, tindakan, pengajuan)
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
                    pengajuan.produk_terkait.lulus_inspeksi = False
                    pengajuan.produk_terkait.save(
                        update_fields=['status_stok', 'lulus_inspeksi', 'updated_at']
                    )
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
    Daftar seluruh jadwal Booking Cek Unit MBG:
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
    if status == 'menunggu':
        params['status'] = status
        daftar = daftar.filter(status__in=['menunggu', 'menunggu_dp'])
    elif status in ('terkonfirmasi', 'disetujui'):
        params['status'] = status
        daftar = daftar.filter(status__in=['terkonfirmasi', 'disetujui'])
    elif status in dict(BookingCekUnit.STATUS_BOOKING):
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
    jml_menunggu = hitung_status.get('menunggu', 0) + hitung_status.get('menunggu_dp', 0)
    jml_disetujui = hitung_status.get('terkonfirmasi', 0) + hitung_status.get('disetujui', 0)
    jml_ditolak = hitung_status.get('ditolak', 0)
    jml_selesai = hitung_status.get('selesai', 0)
    jml_batal = hitung_status.get('batal', 0)

    tab_status = [
        ('', 'Semua', total_booking),
        ('menunggu', 'Menunggu Verifikasi', jml_menunggu),
        ('terkonfirmasi', 'Disetujui', jml_disetujui),
        ('selesai', 'Selesai', jml_selesai),
        ('ditolak', 'Ditolak', jml_ditolak),
    ]

    context = {
        'page_obj': page_obj,
        'booking_list': page_obj.object_list,
        'total_hasil': page_obj.paginator.count,
        'querystring': urlencode(params),
        'nilai': params,
        'tab_status': tab_status,
        'pilihan_status': [
            ('menunggu', 'Menunggu Verifikasi Admin'),
            ('terkonfirmasi', 'Jadwal Disetujui'),
            ('ditolak', 'Jadwal Ditolak'),
            ('selesai', 'Selesai (Sudah Dikunjungi)'),
            ('batal', 'Dibatalkan'),
        ],
        'pilihan_opsi': BookingCekUnit.OPSI_DELIVERY,
        'pilihan_pembayaran': Transaksi.STATUS_PEMBAYARAN,
        'total_booking': total_booking,
        'booking_menunggu': jml_menunggu,
        'booking_menunggu_dp': jml_menunggu,
        'booking_terkonfirmasi': jml_disetujui,
        'booking_selesai': jml_selesai,
        'booking_ditolak': jml_ditolak,
        'booking_batal': jml_batal,
        'jumlah_booking_kendaraan': PesananPembelian.objects.filter(
            jenis_pembelian='booking',
        ).count(),
        'next_url': request.get_full_path(),
    }
    return render(request, 'dashboard/admin/booking_list.html', context)


@staf_required
def admin_pesanan_pembelian(request):
    pembayaran_menunggu = PembayaranPesanan.objects.filter(
        status='menunggu_verifikasi',
    ).count()
    daftar = PesananPembelian.objects.select_related(
        'pelanggan', 'produk',
    ).prefetch_related('pembayaran', 'refund').order_by('-created_at', '-pk')

    filter_status = request.GET.get('status', '').strip()
    filter_jenis = request.GET.get('jenis', '').strip()
    if filter_status == 'menunggu_verifikasi':
        daftar = daftar.filter(
            pembayaran__status='menunggu_verifikasi',
        ).distinct()
    elif filter_status == 'beli_langsung':
        daftar = daftar.filter(jenis_pembelian='beli_langsung')
    elif filter_status in dict(PesananPembelian.STATUS_CHOICES):
        daftar = daftar.filter(status=filter_status)
    if filter_jenis in dict(PesananPembelian.JENIS_PEMBELIAN_CHOICES):
        daftar = daftar.filter(jenis_pembelian=filter_jenis)

    page_obj = Paginator(daftar, 25).get_page(request.GET.get('page'))
    for pesanan in page_obj.object_list:
        pesanan.form_pembayaran_manual = PembayaranManualPesananForm(pesanan=pesanan)
        pembayaran = list(pesanan.pembayaran.all())
        refund_terbuka = any(
            refund.status in RefundPesanan.STATUS_TERBUKA
            for refund in pesanan.refund.all()
        )
        pesanan.dapat_dibatalkan_admin = (
            pesanan.status in PesananPembelian.STATUS_DAPAT_DIBATALKAN
            and not refund_terbuka
            and not any(item.status == 'menunggu_verifikasi' for item in pembayaran)
        )
    return render(request, 'dashboard/admin/pesanan_pembelian_list.html', {
        'page_obj': page_obj,
        'pesanan_list': page_obj.object_list,
        'total_hasil': page_obj.paginator.count,
        'pembayaran_pesanan_menunggu_verifikasi': pembayaran_menunggu,
        'filter_status': filter_status,
        'filter_jenis': filter_jenis,
        'pilihan_status': PesananPembelian.STATUS_CHOICES,
    })


@staf_required
@require_POST
def admin_tinjau_pengajuan_kredit(request, pesanan_id):
    pesanan = get_object_or_404(PesananPembelian, pk=pesanan_id)
    disetujui = request.POST.get('aksi') == 'setujui'
    if request.POST.get('aksi') not in ('setujui', 'tolak'):
        messages.error(request, 'Tindakan peninjauan pengajuan kredit tidak valid.')
        return redirect('admin_pesanan_pembelian')
    try:
        pesanan.tinjau_pengajuan_kredit(
            oleh_user=request.user,
            disetujui=disetujui,
            alasan=request.POST.get('alasan', ''),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            (
                f'Pengajuan kredit pesanan {pesanan.nomor_pesanan} disetujui. '
                f'Pelanggan dapat mengirim DP 20% sebesar Rp {pesanan.nominal_dp_kredit:,.0f}.'
                if disetujui
                else f'Pengajuan kredit pesanan {pesanan.nomor_pesanan} ditolak dan unit tersedia kembali.'
            ),
        )
    return redirect('admin_pesanan_pembelian')


@staf_required
@require_POST
def admin_catat_pembayaran_pesanan(request, pesanan_id):
    with transaction.atomic():
        pesanan = get_object_or_404(PesananPembelian, pk=pesanan_id)
        PesananPembelian._kunci(pesanan.pk)
        pesanan.refresh_from_db()
        form = PembayaranManualPesananForm(request.POST, pesanan=pesanan)
        if not form.is_valid():
            messages.error(request, 'Pembayaran manual tidak dapat dicatat: ' + '; '.join(
                f'{field}: {", ".join(errors)}' for field, errors in form.errors.items()
            ))
            return redirect('admin_pesanan_pembelian')

        pembayaran = PembayaranPesanan(
            pesanan=pesanan,
            jenis=form.cleaned_data['jenis'],
            nominal=form.cleaned_data['nominal'],
            saluran=form.cleaned_data['saluran'],
            nomor_referensi=form.cleaned_data['nomor_referensi'],
            catatan_verifikasi=form.cleaned_data['catatan'],
            tanggal_bayar=timezone.now(),
            status='menunggu_verifikasi',
        )
        try:
            pembayaran.full_clean()
            pembayaran.save()
        except ValidationError as exc:
            messages.error(request, f'Pembayaran manual tidak dapat dicatat: {exc}')
            return redirect('admin_pesanan_pembelian')
    messages.success(
        request,
        f'{pembayaran.get_jenis_display()} Rp {pembayaran.nominal:,.0f} tercatat dan menunggu verifikasi admin.',
    )
    return redirect('admin_pesanan_pembelian')


@staf_required
@require_POST
def admin_verifikasi_pembayaran_pesanan(request, pembayaran_id):
    pembayaran = get_object_or_404(
        PembayaranPesanan.objects.select_related('pesanan'),
        pk=pembayaran_id,
    )
    if pembayaran.jenis != 'booking_fee' and pembayaran.saluran not in ('transfer', 'tunai'):
        messages.error(request, 'Pembayaran selain Booking Fee hanya dapat diverifikasi sebagai kanal manual.')
    else:
        try:
            pembayaran.verifikasi(oleh_user=request.user)
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f'{pembayaran.get_jenis_display()} pesanan {pembayaran.pesanan.nomor_pesanan} diverifikasi.',
            )
    return redirect('admin_pesanan_pembelian')


@staf_required
@require_POST
def admin_tolak_pembayaran_pesanan(request, pembayaran_id):
    pembayaran = get_object_or_404(PembayaranPesanan, pk=pembayaran_id)
    try:
        pembayaran.tolak(
            oleh_user=request.user,
            catatan=request.POST.get('catatan', ''),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Pembayaran ditolak dan alasan penolakan telah dicatat.')
    return redirect('admin_pesanan_pembelian')


@staf_required
@require_POST
def admin_setujui_refund_pesanan(request, refund_id):
    refund = get_object_or_404(RefundPesanan.objects.select_related('pesanan'), pk=refund_id)
    try:
        refund.setujui(
            oleh_user=request.user,
            catatan=request.POST.get('catatan', ''),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f'Pembatalan pesanan {refund.pesanan.nomor_pesanan} disetujui. Pengembalian dana masih perlu dicatat setelah dilakukan.',
        )
    return redirect('admin_pesanan_pembelian')


@staf_required
@require_POST
def admin_tolak_refund_pesanan(request, refund_id):
    refund = get_object_or_404(RefundPesanan, pk=refund_id)
    try:
        refund.tolak(
            oleh_user=request.user,
            catatan=request.POST.get('catatan', ''),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Permintaan refund ditolak dan alasannya telah dicatat.')
    return redirect('admin_pesanan_pembelian')


@staf_required
@require_POST
def admin_catat_pengembalian_refund(request, refund_id):
    refund = get_object_or_404(RefundPesanan, pk=refund_id)
    try:
        refund.catat_pengembalian(
            oleh_user=request.user,
            nomor_referensi=request.POST.get('nomor_referensi', ''),
            catatan=request.POST.get('catatan', ''),
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Pengembalian dana dicatat pada riwayat refund.')
    return redirect('admin_pesanan_pembelian')


@staf_required
@require_POST
def admin_selesaikan_pesanan_pembelian(request, pesanan_id):
    pesanan = get_object_or_404(PesananPembelian, pk=pesanan_id)
    try:
        pesanan.selesaikan(
            oleh_user=request.user,
            catatan='Administrasi dan serah terima kendaraan diselesaikan.',
        )
    except ValueError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f'Pesanan {pesanan.nomor_pesanan} selesai; kendaraan ditandai terjual.',
        )
    return redirect('admin_pesanan_pembelian')


@staf_required
@require_POST
def admin_batalkan_pesanan_pembelian(request, pesanan_id):
    pesanan = get_object_or_404(PesananPembelian, pk=pesanan_id)
    alasan = (request.POST.get('alasan') or '').strip()
    if not alasan:
        messages.error(request, 'Alasan pembatalan wajib dicatat.')
        return redirect('admin_pesanan_pembelian')

    if pesanan.pembayaran.filter(status='menunggu_verifikasi').exists():
        messages.error(
            request,
            'Pesanan belum dapat dibatalkan karena ada pembayaran yang menunggu verifikasi.',
        )
        return redirect('admin_pesanan_pembelian')

    try:
        if pesanan.total_terverifikasi() > 0:
            refund = pesanan.ajukan_pembatalan(request.user, alasan)
            messages.success(
                request,
                f'Permintaan pembatalan {pesanan.nomor_pesanan} dicatat. '
                f'Setujui refund Rp {refund.nominal_diajukan:,.0f} untuk membuka unit.',
            )
        else:
            pesanan.batalkan_tanpa_pembayaran(request.user, alasan)
            messages.success(
                request,
                f'Pesanan {pesanan.nomor_pesanan} dibatalkan dan unit kembali tersedia.',
            )
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect('admin_pesanan_pembelian')


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
    Admin memverifikasi jadwal (setujui, tolak, selesai, dll.) disertai catatan admin.
    """
    booking = get_object_or_404(BookingCekUnit, id=id)
    status_baru = request.POST.get('status', '').strip()
    catatan_admin = request.POST.get('catatan_admin', '').strip()
    status_dict = dict(BookingCekUnit.STATUS_BOOKING)

    if status_baru not in status_dict:
        messages.error(request, f"Status '{status_baru}' tidak valid.")
        return redirect('admin_booking_detail', id=booking.id)

    booking.status = status_baru
    if catatan_admin:
        booking.catatan_admin = catatan_admin

    if status_baru in ('terkonfirmasi', 'disetujui'):
        booking.diverifikasi_oleh = request.user
        booking.tanggal_verifikasi = timezone.now()
        # Otomatis kunci unit mobil menjadi 'in_review' jika saat ini 'ready'
        if booking.produk.status_stok == 'ready':
            booking.produk.status_stok = 'in_review'
            booking.produk.save(update_fields=['status_stok', 'updated_at'])
    elif status_baru == 'ditolak':
        booking.diverifikasi_oleh = request.user
        booking.tanggal_verifikasi = timezone.now()

    fields_to_update = ['status', 'catatan_admin', 'updated_at']
    if booking.diverifikasi_oleh:
        fields_to_update.extend(['diverifikasi_oleh', 'tanggal_verifikasi'])
    booking.save(update_fields=list(set(fields_to_update)))

    messages.success(
        request,
        f"Status Jadwal Cek Unit #{booking.id} berhasil diperbarui menjadi '{status_dict.get(status_baru)}'."
    )

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


# ═══════════════════════════════════════════════════════════════════
# 14. ALUR PEMBELIAN MOBIL OLEH PELANGGAN (SPRINT 2 — CUSTOMER PURCHASE FLOW)
# ═══════════════════════════════════════════════════════════════════

def _alihkan_jika_ada_pesanan_aktif(request, produk):
    """
    Satu kendaraan hanya boleh punya satu pesanan aktif. Jika unit sudah dipegang pesanan
    aktif, kembalikan redirect yang sesuai (ke pesanan milik pengguna sendiri, atau ke detail
    unit jika dipegang pelanggan lain); jika tidak, kembalikan None. Ini hanya pemeriksaan
    awal untuk pengalaman pengguna; penjaga sebenarnya ada di PesananPembelian.buat_dari_produk.
    """
    aktif = PesananPembelian.pesanan_aktif_unit(produk)
    if aktif is None:
        return None
    if aktif.pelanggan_id == request.user.id:
        messages.info(
            request,
            f'Anda sudah memiliki pesanan aktif untuk unit ini ({aktif.nomor_pesanan}).',
        )
        return redirect('pesanan_detail', nomor_pesanan=aktif.nomor_pesanan)
    messages.error(request, 'Kendaraan ini sedang dalam proses pemesanan oleh pelanggan lain.')
    return redirect('detail_produk', id=produk.id)


def _rincian_simulasi_kredit(harga, booking_fee):
    harga = Decimal(harga)
    booking_fee = Decimal(booking_fee)
    dp_awal = (harga * PERSENTASE_DP_KREDIT / Decimal('100')).quantize(
        Decimal('1'), rounding=ROUND_HALF_UP,
    )
    nominal_angsuran = (
        harga * PERSENTASE_ANGSURAN_KREDIT_TAHUNAN / Decimal('100')
    ).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    booking_fee_untuk_dp = min(booking_fee, dp_awal)
    saldo_setelah_dp = max(harga - max(dp_awal, booking_fee), Decimal('0'))
    tenor_tahun = (
        int((saldo_setelah_dp / nominal_angsuran).to_integral_value(rounding=ROUND_CEILING))
        if saldo_setelah_dp and nominal_angsuran
        else 0
    )
    angsuran_terakhir = (
        saldo_setelah_dp - nominal_angsuran * (tenor_tahun - 1)
        if tenor_tahun
        else Decimal('0')
    )
    return {
        'persentase_dp': PERSENTASE_DP_KREDIT,
        'nominal_dp': dp_awal,
        'nominal_dp_tambahan': max(dp_awal - booking_fee_untuk_dp, Decimal('0')),
        'persentase_angsuran_tahunan': PERSENTASE_ANGSURAN_KREDIT_TAHUNAN,
        'nominal_angsuran_tahunan': nominal_angsuran,
        'saldo_setelah_dp': saldo_setelah_dp,
        'tenor_tahun': tenor_tahun,
        'nominal_angsuran_terakhir': angsuran_terakhir,
    }


def _jenis_pembayaran_transfer_tersedia(pesanan):
    if pesanan.saluran_pembayaran != 'transfer':
        return None
    if pesanan.jenis_pembelian == 'beli_langsung' and pesanan.metode_pembayaran == 'kredit':
        if pesanan.status == 'menunggu_dp_kredit':
            return 'dp'
        if pesanan.status == 'kredit_aktif' and pesanan.sisa_pembayaran() > 0:
            return 'angsuran'
        return None
    if pesanan.status == 'menunggu_booking_fee':
        return pesanan.jenis_pembayaran_midtrans
    return None


def _pembayaran_menghalangi_konfirmasi(pesanan, jenis, kunci=False):
    status = ('menunggu_verifikasi',) if jenis == 'angsuran' else (
        'menunggu_verifikasi', 'terverifikasi',
    )
    pembayaran = pesanan.pembayaran.filter(jenis=jenis, status__in=status)
    if kunci:
        pembayaran = pembayaran.select_for_update()
    return pembayaran.exists()


@login_required(login_url='login')
def checkout_pembelian(request, id):
    """
    Halaman formulir checkout pembelian kendaraan stok MBG (B2C).
    Hanya untuk pengguna yang sudah login.
    Menampilkan rincian kendaraan, identitas pembeli, opsi pembayaran & penyerahan,
    serta perhitungan Booking Fee dari PengaturanPembelian.
    """
    produk = get_object_or_404(Produk, pk=id)
    jenis_pembelian = request.GET.get('jenis_pembelian', 'booking')
    if jenis_pembelian not in dict(PesananPembelian.JENIS_PEMBELIAN_CHOICES):
        messages.error(request, 'Jenis pemesanan tidak valid. Silakan pilih kembali dari halaman kendaraan.')
        return redirect('detail_produk', id=produk.id)

    # Validasi kelayakan unit untuk dibeli
    if produk.tipe_penjual != 'B2C':
        messages.error(request, 'Hanya unit stok MBG (B2C) yang dapat dibeli melalui alur ini.')
        return redirect('detail_produk', id=produk.id)

    if produk.status_stok != 'ready':
        if produk.status_stok == 'in_review':
            pesan = 'Kendaraan ini sedang dalam proses pemesanan oleh pelanggan lain.'
        elif produk.status_stok == 'sold':
            pesan = 'Kendaraan ini sudah terjual (sold out).'
        else:
            pesan = 'Kendaraan ini tidak tersedia untuk dipesan.'
        messages.error(request, pesan)
        return redirect('detail_produk', id=produk.id)

    # Unit yang sudah dipegang pesanan aktif (milik sendiri atau pelanggan lain) tidak dapat dipesan lagi.
    alihkan = _alihkan_jika_ada_pesanan_aktif(request, produk)
    if alihkan is not None:
        return alihkan

    pengaturan_pembelian = PengaturanPembelian.ambil()
    nominal_booking_fee = pengaturan_pembelian.hitung_booking_fee(produk.harga)

    session_key = f'checkout_{produk.id}'

    if request.method == 'POST':
        form = CheckoutPembelianForm(request.POST, jenis_pembelian=jenis_pembelian)
        if form.is_valid():
            # Simpan data checkout yang tervalidasi ke session untuk konfirmasi
            request.session[session_key] = form.cleaned_data
            return redirect('konfirmasi_pembelian', id=produk.id)
    else:
        # Pre-fill data pemesan dari session sebelumnya atau profil akun pengguna
        stored_data = request.session.get(session_key)
        if stored_data:
            form = CheckoutPembelianForm(
                initial=stored_data,
                jenis_pembelian=jenis_pembelian,
            )
        else:
            initial_data = {
                'nama_pelanggan': request.user.get_full_name() or request.user.username,
                'no_whatsapp': request.user.no_whatsapp or '',
                'email': request.user.email or '',
                'alamat_pengantaran': request.user.alamat or '',
                'metode_pembayaran': 'cash',
                'opsi_penyerahan': 'showroom',
            }
            form = CheckoutPembelianForm(
                initial=initial_data,
                jenis_pembelian=jenis_pembelian,
            )

    sisa_estimasi = max(produk.harga - nominal_booking_fee, Decimal('0'))
    rincian_kredit = _rincian_simulasi_kredit(
        produk.harga,
        nominal_booking_fee if jenis_pembelian == 'booking' else Decimal('0'),
    )

    # Jangan memakai key 'pengaturan' di sini: itu milik context processor `pengaturan_situs`
    # (PengaturanWebsite: WhatsApp, alamat, dll. untuk navbar/footer) dan akan tertimpa.
    return render(request, 'pembelian/checkout.html', {
        'produk': produk,
        'form': form,
        'pengaturan_pembelian': pengaturan_pembelian,
        'persentase_booking_fee': pengaturan_pembelian.persentase_booking_fee,
        'nominal_booking_fee': nominal_booking_fee,
        'sisa_estimasi': sisa_estimasi,
        'nominal_pembelian_langsung': produk.harga_promo if produk.harga_promo else produk.harga,
        'diskon_subsidi': (produk.harga - produk.harga_promo) if produk.harga_promo else None,
        'jenis_pembelian': jenis_pembelian,
        'rincian_kredit': rincian_kredit,
    })


@login_required(login_url='login')
def konfirmasi_pembelian(request, id):
    """
    Halaman konfirmasi pesanan sebelum dibuat secara permanen di database.
    Menampilkan ringkasan unit, identitas pemesan, rincian biaya, dan syarat booking fee.
    Saat dikonfirmasi (POST), pesanan disimpan secara atomic dengan row-lock select_for_update.
    """
    produk = get_object_or_404(Produk, pk=id)
    session_key = f'checkout_{produk.id}'
    checkout_data = request.session.get(session_key)

    if not checkout_data:
        messages.warning(request, 'Silakan lengkapi formulir pemesanan terlebih dahulu.')
        return redirect('checkout_pembelian', id=produk.id)

    # Validasi ketersediaan unit
    if produk.tipe_penjual != 'B2C' or produk.status_stok != 'ready':
        messages.error(request, 'Maaf, unit kendaraan ini sudah tidak tersedia untuk dipesan.')
        return redirect('detail_produk', id=produk.id)

    alihkan = _alihkan_jika_ada_pesanan_aktif(request, produk)
    if alihkan is not None:
        return alihkan

    pengaturan_pembelian = PengaturanPembelian.ambil()
    nominal_booking_fee = pengaturan_pembelian.hitung_booking_fee(produk.harga)
    sisa_estimasi = max(produk.harga - nominal_booking_fee, Decimal('0'))
    rincian_kredit = _rincian_simulasi_kredit(
        produk.harga,
        nominal_booking_fee
        if checkout_data.get('jenis_pembelian') == 'booking'
        else Decimal('0'),
    ) if checkout_data.get('metode_pembayaran') == 'kredit' else None
    form_kanal = KanalPembayaranPembelianForm({
        'saluran_pembayaran': checkout_data.get('saluran_pembayaran', 'midtrans'),
    })

    if request.method == 'POST':
        if not form_kanal.is_valid():
            messages.error(request, 'Kanal pembayaran tidak valid. Silakan pilih kembali di checkout.')
            return redirect('checkout_pembelian', id=produk.id)
        # Pencegahan double booking dan race conditions dengan transaksi database + row-level lock
        try:
            with transaction.atomic():
                produk_terkunci = Produk.objects.select_for_update().get(pk=produk.pk)
                if produk_terkunci.tipe_penjual != 'B2C' or produk_terkunci.status_stok != 'ready':
                    messages.error(request, 'Maaf, kendaraan ini baru saja tidak tersedia untuk dipesan.')
                    return redirect('detail_produk', id=produk.id)

                pesanan = PesananPembelian.buat_dari_produk(
                    produk=produk_terkunci,
                    pelanggan=request.user,
                    nama_pelanggan=checkout_data['nama_pelanggan'],
                    no_whatsapp=checkout_data['no_whatsapp'],
                    metode_pembayaran=checkout_data['metode_pembayaran'],
                    jenis_pembelian=checkout_data.get('jenis_pembelian', 'booking'),
                    saluran_pembayaran=(
                        'transfer'
                        if checkout_data.get('jenis_pembelian') == 'beli_langsung'
                        and checkout_data.get('metode_pembayaran') == 'kredit'
                        else form_kanal.cleaned_data['saluran_pembayaran']
                    ),
                    opsi_penyerahan=checkout_data['opsi_penyerahan'],
                    email=checkout_data.get('email', ''),
                    alamat_pengantaran=checkout_data.get('alamat_pengantaran', ''),
                )

            # Hapus data form di session setelah berhasil dibuat
            request.session.pop(session_key, None)
            messages.success(
                request,
                f'Pesanan {pesanan.nomor_pesanan} berhasil dibuat! '
                + (
                    'Pengajuan Kredit menunggu peninjauan admin. DP baru dibayar setelah pengajuan disetujui.'
                    if pesanan.metode_pembayaran == 'kredit'
                    and pesanan.jenis_pembelian == 'beli_langsung'
                    else (
                        'Silakan transfer sesuai instruksi resmi dan kirim konfirmasi untuk diperiksa admin.'
                        if pesanan.saluran_pembayaran == 'transfer'
                        else (
                            'Bayar 100% harga OTR melalui Midtrans untuk validasi otomatis dan penguncian unit.'
                            if pesanan.jenis_pembelian == 'beli_langsung'
                            else 'Silakan bayar Booking Fee; unit dikunci setelah verifikasi admin.'
                        )
                    )
                )
            )
            return redirect('pesanan_berhasil', nomor_pesanan=pesanan.nomor_pesanan)

        except (ValueError, ValidationError) as e:
            # Kalah balapan dengan pesanan lain untuk unit yang sama (atau klik ganda): arahkan
            # ke pesanan yang sudah ada alih-alih membiarkan pengguna mengulang dari checkout.
            alihkan = _alihkan_jika_ada_pesanan_aktif(request, produk)
            if alihkan is not None:
                return alihkan
            messages.error(request, f'Gagal membuat pesanan: {e}')
            return redirect('checkout_pembelian', id=produk.id)

    return render(request, 'pembelian/konfirmasi.html', {
        'produk': produk,
        'checkout_data': checkout_data,
        'pengaturan_pembelian': pengaturan_pembelian,
        'persentase_booking_fee': pengaturan_pembelian.persentase_booking_fee,
        'nominal_booking_fee': nominal_booking_fee,
        'sisa_estimasi': sisa_estimasi,
        'nominal_pembelian_langsung': produk.harga_promo if produk.harga_promo else produk.harga,
        'diskon_subsidi': (produk.harga - produk.harga_promo) if produk.harga_promo else None,
        'rincian_kredit': rincian_kredit,
        'form_kanal': form_kanal,
    })


@login_required(login_url='login')
def pesanan_berhasil(request, nomor_pesanan):
    """
    Halaman status pembayaran dan pengajuan pesanan.
    """
    pesanan = get_object_or_404(
        PesananPembelian.objects.select_related('produk', 'pelanggan'),
        nomor_pesanan=nomor_pesanan,
    )

    # Batasi akses hanya pemilik pesanan atau staf MBG
    if not (request.user.is_staff or request.user.is_superuser or pesanan.pelanggan_id == request.user.id):
        raise PermissionDenied("Anda tidak memiliki akses ke pesanan ini.")

    pembayaran_awal = pesanan.pembayaran.filter(
        jenis=pesanan.jenis_pembayaran_midtrans,
    ).order_by('-created_at', '-pk').first()
    jenis_transfer = _jenis_pembayaran_transfer_tersedia(pesanan)
    dapat_konfirmasi_transfer = (
        pesanan.pelanggan_id == request.user.id
        and jenis_transfer is not None
        and not _pembayaran_menghalangi_konfirmasi(pesanan, jenis_transfer)
    )
    tahap_pembayaran_selesai = pesanan.status in {
        'booking_aktif', 'kredit_aktif', 'selesai',
    } or (
        pesanan.jenis_pembelian == 'beli_langsung'
        and pesanan.metode_pembayaran == 'kredit'
        and pesanan.status in {'menunggu_kredit', 'menunggu_dp_kredit'}
    )
    return render(request, 'pembelian/pesanan_berhasil.html', {
        'pesanan': pesanan,
        'produk': pesanan.produk,
        'pembayaran_booking_fee': pembayaran_awal,
        'tahap_pembayaran_selesai': tahap_pembayaran_selesai,
        'form_konfirmasi_transfer': (
            KonfirmasiTransferPesananForm() if dapat_konfirmasi_transfer else None
        ),
        'jenis_pembayaran_transfer': jenis_transfer,
        'nominal_pembayaran_transfer': pesanan.nominal_pembayaran_berikutnya,
        'midtrans_client_key': getattr(settings, 'MIDTRANS_CLIENT_KEY', ''),
        'midtrans_snap_js_url': getattr(settings, 'MIDTRANS_SNAP_JS_URL', 'https://app.sandbox.midtrans.com/snap/snap.js'),
        'midtrans_is_production': getattr(settings, 'MIDTRANS_IS_PRODUCTION', False),
        'rincian_kredit': (
            _rincian_simulasi_kredit(pesanan.harga_kendaraan, pesanan.nominal_booking_fee)
            if pesanan.metode_pembayaran == 'kredit'
            and pesanan.jenis_pembelian == 'booking'
            else (
                _rincian_simulasi_kredit(pesanan.harga_kendaraan, Decimal('0'))
                if pesanan.metode_pembayaran == 'kredit'
                else None
            )
        ),
    })


@login_required(login_url='login')
def pesanan_detail(request, nomor_pesanan):
    """
    Halaman detail status pesanan pembelian untuk pelanggan.
    Menampilkan timeline status, riwayat pembayaran, sisa tagihan, dan rincian unit.
    """
    pesanan = get_object_or_404(
        PesananPembelian.objects.select_related('produk', 'pelanggan').prefetch_related('riwayat', 'pembayaran'),
        nomor_pesanan=nomor_pesanan,
    )

    # Batasi akses hanya pemilik pesanan atau staf MBG
    if not (request.user.is_staff or request.user.is_superuser or pesanan.pelanggan_id == request.user.id):
        raise PermissionDenied("Anda tidak memiliki akses ke pesanan ini.")

    if request.method == 'POST':
        form_pembatalan = PermintaanPembatalanPesananForm(request.POST)
        if form_pembatalan.is_valid():
            try:
                if pesanan.total_terverifikasi() > 0:
                    pesanan.ajukan_pembatalan(request.user, form_pembatalan.cleaned_data['alasan'])
                    messages.success(request, 'Permintaan pembatalan dan refund telah dikirim kepada admin.')
                else:
                    pesanan.batalkan_tanpa_pembayaran(
                        request.user,
                        form_pembatalan.cleaned_data['alasan'],
                    )
                    messages.success(request, 'Pesanan dibatalkan karena belum ada pembayaran terverifikasi.')
                return redirect('pesanan_detail', nomor_pesanan=pesanan.nomor_pesanan)
            except ValueError as exc:
                form_pembatalan.add_error(None, str(exc))
    else:
        form_pembatalan = PermintaanPembatalanPesananForm()

    pembayaran_awal = pesanan.pembayaran.filter(
        jenis=pesanan.jenis_pembayaran_midtrans,
    ).order_by('-created_at', '-pk').first()
    jenis_transfer = _jenis_pembayaran_transfer_tersedia(pesanan)
    dapat_konfirmasi_transfer = (
        pesanan.pelanggan_id == request.user.id
        and jenis_transfer is not None
        and not _pembayaran_menghalangi_konfirmasi(pesanan, jenis_transfer)
    )
    return render(request, 'pembelian/pesanan_detail.html', {
        'pesanan': pesanan,
        'produk': pesanan.produk,
        'pembayaran_booking_fee': pembayaran_awal,
        'form_konfirmasi_transfer': (
            KonfirmasiTransferPesananForm() if dapat_konfirmasi_transfer else None
        ),
        'jenis_pembayaran_transfer': jenis_transfer,
        'nominal_pembayaran_transfer': pesanan.nominal_pembayaran_berikutnya,
        'midtrans_client_key': getattr(settings, 'MIDTRANS_CLIENT_KEY', ''),
        'midtrans_snap_js_url': getattr(settings, 'MIDTRANS_SNAP_JS_URL', 'https://app.sandbox.midtrans.com/snap/snap.js'),
        'midtrans_is_production': getattr(settings, 'MIDTRANS_IS_PRODUCTION', False),
        'rincian_kredit': (
            _rincian_simulasi_kredit(pesanan.harga_kendaraan, pesanan.nominal_booking_fee)
            if pesanan.metode_pembayaran == 'kredit' and pesanan.jenis_pembelian == 'booking'
            else (
                _rincian_simulasi_kredit(pesanan.harga_kendaraan, Decimal('0'))
                if pesanan.metode_pembayaran == 'kredit'
                else None
            )
        ),
        'form_pembatalan': form_pembatalan,
        'refund_list': pesanan.refund.all(),
        'dapat_mengajukan_pembatalan': (
            pesanan.status in PesananPembelian.STATUS_DAPAT_DIBATALKAN
            and not pesanan.refund.filter(status__in=RefundPesanan.STATUS_TERBUKA).exists()
        ),
    })


@login_required(login_url='login')
@require_POST
def konfirmasi_transfer_pesanan(request, nomor_pesanan):
    """Catat konfirmasi transfer pelanggan dengan nominal yang dihitung dari pesanan."""
    pesanan = get_object_or_404(PesananPembelian, nomor_pesanan=nomor_pesanan)
    if pesanan.pelanggan_id != request.user.id:
        raise PermissionDenied("Hanya pemilik pesanan yang dapat mengonfirmasi transfer.")

    form = KonfirmasiTransferPesananForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(
            request,
            'Nomor referensi dan bukti transfer wajib diisi dengan format yang valid.',
        )
        referer = request.META.get('HTTP_REFERER', '')
        if 'berhasil' in referer:
            return redirect('pesanan_berhasil', nomor_pesanan=pesanan.nomor_pesanan)
        return redirect('pesanan_detail', nomor_pesanan=pesanan.nomor_pesanan)

    try:
        with transaction.atomic():
            _, pesanan = PesananPembelian._kunci(pesanan.pk)
            if pesanan.saluran_pembayaran != 'transfer':
                raise ValueError('Pesanan ini menggunakan Midtrans, bukan transfer bank manual.')
            jenis_pembayaran = _jenis_pembayaran_transfer_tersedia(pesanan)
            if jenis_pembayaran is None:
                raise ValueError('Pesanan ini tidak sedang menunggu pembayaran transfer.')
            if _pembayaran_menghalangi_konfirmasi(pesanan, jenis_pembayaran, kunci=True):
                raise ValueError('Konfirmasi pembayaran untuk pesanan ini sudah tercatat.')

            pembayaran = PembayaranPesanan(
                pesanan=pesanan,
                jenis=jenis_pembayaran,
                nominal=pesanan.nominal_pembayaran_berikutnya,
                saluran='transfer',
                nomor_referensi=form.cleaned_data['nomor_referensi'],
                bukti_transfer=form.cleaned_data['bukti_transfer'],
                tanggal_bayar=timezone.now(),
                status='menunggu_verifikasi',
            )
            pembayaran.full_clean()
            pembayaran.save()
    except (ValueError, ValidationError) as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            'Konfirmasi transfer tercatat. Pembayaran akan ditinjau admin sebelum unit dikunci.',
        )
    referer = request.META.get('HTTP_REFERER', '')
    if 'berhasil' in referer:
        return redirect('pesanan_berhasil', nomor_pesanan=pesanan.nomor_pesanan)
    return redirect('pesanan_detail', nomor_pesanan=pesanan.nomor_pesanan)


# ═══════════════════════════════════════════════════════════════════
# 14. INTEGRASI MIDTRANS SNAP SANDBOX (Sprint 3)
# ═══════════════════════════════════════════════════════════════════

@login_required(login_url='login')
@require_POST
def midtrans_snap_token(request, nomor_pesanan):
    """
    Endpoint backend untuk membuat atau mengambil Midtrans Snap Token secara aman.
    Server Key tidak pernah diekspos ke browser; hanya Client Key dan Snap Token yang dikembalikan.
    Mencegah transaksi ganda jika pengguna mengulang pembayaran.
    """
    pesanan = get_object_or_404(PesananPembelian, nomor_pesanan=nomor_pesanan)

    # Batasi akses hanya pemilik pesanan atau staf MBG
    if not (request.user.is_staff or request.user.is_superuser or pesanan.pelanggan_id == request.user.id):
        raise PermissionDenied("Anda tidak memiliki akses ke pesanan ini.")

    if pesanan.saluran_pembayaran != 'midtrans':
        return JsonResponse(
            {'status': 'error', 'message': 'Pesanan ini menggunakan transfer bank manual.'},
            status=400,
        )

    try:
        token, redirect_url, order_id = buat_snap_transaksi(pesanan)
        return JsonResponse({
            'status': 'success',
            'snap_token': token,
            'redirect_url': redirect_url,
            'order_id': order_id,
            'client_key': getattr(settings, 'MIDTRANS_CLIENT_KEY', ''),
        })
    except ValueError as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=400)
    except DatabaseError:
        raise
    except Exception as exc:
        logger.error('Unexpected Snap-token error [%s]', type(exc).__name__)
        return JsonResponse(
            {'status': 'error', 'message': 'Terjadi kesalahan saat menyiapkan pembayaran.'},
            status=500,
        )


@login_required(login_url='login')
@require_GET
def midtrans_payment_status(request, nomor_pesanan):
    """Sinkronkan dan tampilkan status tagihan Midtrans sesuai jenis pembelian."""
    pesanan = get_object_or_404(PesananPembelian, nomor_pesanan=nomor_pesanan)
    if not (
        request.user.is_staff or request.user.is_superuser
        or pesanan.pelanggan_id == request.user.id
    ):
        raise PermissionDenied("Anda tidak memiliki akses ke pesanan ini.")

    pembayaran = pesanan.pembayaran.filter(
        jenis=pesanan.jenis_pembayaran_midtrans,
    ).order_by('-created_at', '-pk').first()
    status_sinkronisasi_gagal = False
    status_terminal = ('settlement', 'capture', 'expire', 'cancel', 'deny')
    if (
        pesanan.status == 'menunggu_booking_fee'
        and pesanan.midtrans_order_id
        and pesanan.midtrans_transaction_status not in status_terminal
        and (not pembayaran or pembayaran.status == 'ditolak')
    ):
        try:
            sinkronkan_status_midtrans(pesanan)
        except MidtransStatusError:
            status_sinkronisasi_gagal = True
        else:
            pesanan.refresh_from_db()
            pembayaran = pesanan.pembayaran.filter(
                jenis=pesanan.jenis_pembayaran_midtrans,
            ).order_by('-created_at', '-pk').first()

    status_pembayaran = pembayaran.status if pembayaran else ''
    status_midtrans = pesanan.midtrans_transaction_status
    if status_sinkronisasi_gagal:
        pesan = 'Status pembayaran belum dapat disinkronkan. Halaman akan mencoba kembali.'
    elif (
        pesanan.jenis_pembelian == 'beli_langsung'
        and status_pembayaran == 'terverifikasi'
    ):
        pesan = 'Pembelian langsung lunas dan terverifikasi otomatis. Unit dikunci untuk proses administrasi dan serah terima.'
    elif status_pembayaran == 'menunggu_verifikasi':
        pesan = 'Pembayaran diterima Midtrans dan sedang menunggu verifikasi staf MBG.'
    elif status_pembayaran == 'terverifikasi':
        pesan = 'Booking Fee telah diverifikasi. Unit kendaraan sudah dikunci.'
    elif status_midtrans == 'pending':
        pesan = 'Menunggu pembayaran pada metode yang Anda pilih di Midtrans.'
    elif status_midtrans == 'challenge':
        pesan = 'Transaksi sedang ditinjau Midtrans. Status akan diperbarui otomatis.'
    elif status_midtrans in ('expire', 'cancel', 'deny'):
        pesan = 'Transaksi berakhir tanpa pembayaran. Silakan mulai pembayaran kembali.'
    elif status_pembayaran == 'ditolak':
        pesan = 'Transaksi tidak berhasil. Anda dapat mencoba pembayaran kembali.'
    elif pesanan.midtrans_snap_token:
        pesan = 'Sesi pembayaran siap. Selesaikan pembayaran di jendela Midtrans.'
    elif pesanan.status == 'booking_aktif':
        pesan = 'Booking Fee telah diverifikasi dan unit kendaraan sudah dikunci.'
    else:
        pesan = 'Belum ada pembayaran yang dikonfirmasi Midtrans.'

    return JsonResponse({
        'status': 'success',
        'order_status': pesanan.status,
        'transaction_status': status_midtrans,
        'payment_status': status_pembayaran,
        'purchase_type': pesanan.jenis_pembelian,
        'amount_due': int(pesanan.nominal_pembayaran_midtrans),
        'message': pesan,
        'status_sync_error': status_sinkronisasi_gagal,
        'can_pay': (
            pesanan.status == 'menunggu_booking_fee'
            and status_pembayaran not in ('menunggu_verifikasi', 'terverifikasi')
        ),
    })


@csrf_exempt
@require_POST
def midtrans_webhook(request):
    """
    Webhook HTTP POST dari Midtrans untuk notifikasi status pembayaran.
    Memvalidasi signature SHA512, order ID, dan nominal pembayaran secara idempotent.
    """
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON body.'}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({'status': 'error', 'message': 'Webhook body must be a JSON object.'}, status=400)

    hasil = proses_notifikasi_webhook(payload)
    if hasil.get('success'):
        return JsonResponse({'status': 'ok', 'result': hasil}, status=200)

    status_code = 400
    if hasil.get('error') == 'invalid_signature':
        status_code = 403
    elif hasil.get('error') == 'order_not_found':
        status_code = 404

    return JsonResponse(hasil, status=status_code)


@require_GET
def robots_txt(request):
    """Instruksi crawler search engine dan lokasi sitemap.xml."""
    base_url = f"{request.scheme}://{request.get_host()}"
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /panel/",
        "Disallow: /admin/",
        "Disallow: /dashboard/",
        "Disallow: /pembelian/",
        "Disallow: /login/",
        "Disallow: /register/",
        f"Sitemap: {base_url}/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain; charset=utf-8")


@require_GET
def sitemap_xml(request):
    """Sitemap XML dinamis berisi halaman publik, unit mobil aktif, artikel, dan halaman CMS."""
    from xml.sax.saxutils import escape

    base_url = f"{request.scheme}://{request.get_host()}"
    urls = [
        (f"{base_url}{reverse('beranda')}", "1.0", "daily", None),
        (f"{base_url}{reverse('katalog')}", "0.9", "daily", None),
        (f"{base_url}{reverse('jual_titip')}", "0.8", "weekly", None),
        (f"{base_url}{reverse('artikel_publik')}", "0.8", "daily", None),
    ]

    for produk in produk_tayang(termasuk_in_review=True).only('id', 'updated_at'):
        lastmod = produk.updated_at.strftime('%Y-%m-%d') if produk.updated_at else None
        urls.append((f"{base_url}{reverse('detail_produk', args=[produk.id])}", "0.8", "daily", lastmod))

    for art in Artikel.objects.filter(status='published').only('slug', 'updated_at', 'created_at'):
        dt = art.updated_at or art.created_at
        lastmod = dt.strftime('%Y-%m-%d') if dt else None
        urls.append((f"{base_url}{reverse('artikel_detail', args=[art.slug])}", "0.7", "weekly", lastmod))

    for hal in HalamanWebsite.objects.filter(status='published').only('slug', 'updated_at'):
        lastmod = hal.updated_at.strftime('%Y-%m-%d') if hal.updated_at else None
        urls.append((f"{base_url}{reverse('halaman_detail', args=[hal.slug])}", "0.6", "monthly", lastmod))

    items = []
    for loc, priority, changefreq, lastmod in urls:
        entry = [f"  <url>", f"    <loc>{escape(loc)}</loc>"]
        if lastmod:
            entry.append(f"    <lastmod>{lastmod}</lastmod>")
        entry.append(f"    <changefreq>{changefreq}</changefreq>")
        entry.append(f"    <priority>{priority}</priority>")
        entry.append("  </url>")
        items.append("\n".join(entry))

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(items)
        + "\n</urlset>"
    )
    return HttpResponse(xml, content_type="application/xml; charset=utf-8")
