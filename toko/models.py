from django.conf import settings
from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, URLValidator
from django.contrib.auth.models import AbstractUser
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from katalog_mbg.storage import PrivateTitipJualStorage
import os
import re
import uuid


def validasi_link_tombol(nilai):
    """
    Link tombol hero hanya boleh: alamat internal (/katalog/), anchor (#titip-jual),
    atau URL http(s). Skema lain (javascript:, data:, dll.) ditolak karena link ini
    dirender langsung ke atribut href di beranda.
    """
    nilai = (nilai or '').strip()
    if not nilai:
        return
    if nilai.startswith('#'):
        return
    if nilai.startswith('/') and not nilai.startswith('//') and '\\' not in nilai:
        return
    if nilai.lower().startswith(('http://', 'https://')):
        URLValidator(schemes=['http', 'https'])(nilai)
        return
    raise ValidationError(
        "Link harus diawali '/' (halaman internal, mis. /katalog/), '#' (anchor), "
        "atau 'https://' (alamat luar)."
    )


def validasi_situs_mitra(nilai):
    """
    Situs web mitra hanya boleh URL http(s) lengkap. Nilai ini dirender ke atribut href
    di beranda, jadi skema lain (javascript:, data:, dll.) dan alamat internal ditolak.
    """
    nilai = (nilai or '').strip()
    if not nilai:
        return
    if not nilai.lower().startswith(('http://', 'https://')):
        raise ValidationError("Alamat situs harus diawali 'https://' atau 'http://'.")
    URLValidator(schemes=['http', 'https'])(nilai)


def normalisasi_whatsapp(teks):
    """
    Ubah input bebas ("0812-3456-7890", "+62 812 3456 7890", "812...") menjadi angka
    format internasional tanpa tanda plus ("6281234567890") untuk tautan wa.me.
    """
    angka = re.sub(r'\D', '', teks or '')
    if angka.startswith('0'):
        angka = '62' + angka[1:]
    elif angka.startswith('8'):
        angka = '62' + angka
    return angka


def validasi_whatsapp(nilai):
    """Nomor WhatsApp tersimpan harus berformat 62xxxxxxxxxx (8–13 digit setelah 62)."""
    if not re.fullmatch(r'62\d{8,13}', nilai or ''):
        raise ValidationError(
            "Nomor WhatsApp tidak valid. Gunakan nomor Indonesia, mis. 0812-3456-7890."
        )


def foto_profil_upload_path(instance, filename):
    """
    Simpan foto di folder per-user dengan nama acak (UUID) agar tidak
    bentrok dan tidak mudah ditebak. Contoh: profil/12/3f2a...c9.jpg
    """
    ekstensi = os.path.splitext(filename)[1].lower()
    return f'profil/{instance.pk or "baru"}/{uuid.uuid4().hex}{ekstensi}'

class CustomUser(AbstractUser):
    no_whatsapp = models.CharField(max_length=20, blank=True, null=True)
    alamat = models.TextField(blank=True, null=True)
    foto_profil = models.ImageField(upload_to=foto_profil_upload_path, blank=True)
                                    
    def __str__(self):
        return self.username


# 1. MODEL KATALOG MOBIL (PRODUCTS)

class Produk(models.Model):
    TIPE_PENJUAL_CHOICES = [
        ('B2C', 'Stok MBG (B2C)'),
        ('C2C', 'Titip-Jual Pengguna (C2C)'),
    ]

    STATUS_STOK_CHOICES = [
        ('ready', 'Ready Stock'),
        ('in_review', 'In Review (Sedang Di-booking)'),
        ('sold', 'Terjual (Sold Out)'),
        ('returned', 'Dikembalikan ke Pemilik C2C'),
    ]

    TRANSMISI_CHOICES = [
        ('AT', 'Otomatis (AT)'),
        ('MT', 'Manual (MT)'),
    ]

    # Master Data
    sku = models.CharField(max_length=50, unique=True, help_text="Contoh: MBG-SUV-HYD-001")
    nama = models.CharField(max_length=200, help_text="Contoh: Hyundai Palisade 2.2 Signature AT 2021")
    merek = models.CharField(max_length=100, help_text="Contoh: Hyundai, Toyota, Honda")
    kategori = models.CharField(max_length=50, help_text="Contoh: SUV, MPV, City Car")
    tahun = models.IntegerField()
    transmisi = models.CharField(max_length=2, choices=TRANSMISI_CHOICES, default='AT')
    jarak_tempuh = models.IntegerField(help_text="Dalam Kilometer (KM)")
    warna = models.CharField(max_length=50)
    lokasi = models.CharField(max_length=100, default='Makassar')
    plat_nomor = models.CharField(max_length=20, help_text="Contoh: DD 1234 XX (Pajak Hidup)")

    # Harga & Promo (LK 5)
    harga = models.DecimalField(max_digits=12, decimal_places=0, help_text="Harga OTR Normal")
    harga_promo = models.DecimalField(max_digits=12, decimal_places=0, null=True, blank=True, help_text="Harga Subsidi DP/Promo")
    
    # Status & Tipe Penjual (LK 2 & LK 6)
    tipe_penjual = models.CharField(max_length=3, choices=TIPE_PENJUAL_CHOICES, default='B2C')
    status_stok = models.CharField(max_length=10, choices=STATUS_STOK_CHOICES, default='ready')
    
    # Fitur Inspeksi & Kepercayaan (LK 5)
    lulus_inspeksi = models.BooleanField(default=True, help_text="Lulus 150 Titik Inspeksi Transparan MBG")
    ringkasan_inspeksi = models.TextField(default="100% Bebas Banjir & Bebas Bekas Tabrak Struktural.")

    # Media & Deskripsi
    gambar_utama = models.ImageField(upload_to='mobil/')
    deskripsi_singkat = models.TextField(help_text="2-3 Kalimat untuk Card/Preview")
    deskripsi_detail = models.TextField(help_text="Spesifikasi & Storytelling Lengkap")

    # Relasi ke TitipJual (diisi saat unit C2C masuk katalog)
    # Menggunakan string 'TitipJual' untuk menghindari circular reference
    titip_jual_sumber = models.ForeignKey(
        'TitipJual', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='produk_katalog',
        verbose_name="Sumber Titip-Jual",
        help_text="Terisi jika unit ini berasal dari pengajuan Titip-Jual (C2C)"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Katalog Mobil"

    def __str__(self):
        return f"{self.nama} ({self.get_status_stok_display()})"



# 2. MODEL FOTO GALERI TAMBAHAN (OPTIONAL)

class FotoMobil(models.Model):
    produk = models.ForeignKey(Produk, on_delete=models.CASCADE, related_name='galeri_foto')
    gambar = models.ImageField(upload_to='mobil/galeri/')
    keterangan = models.CharField(max_length=100, blank=True, help_text="Misal: Interior, Mesin, Bagasi")

    def __str__(self):
        return f"Foto {self.keterangan} - {self.produk.nama}"



# 3. MODEL TITIP-JUAL C2C ("JUAL MOBIL SAYA")

def dokumen_titipjual_path(instance, filename):
    """Simpan dokumen dengan nama UUID agar tidak mudah ditebak (privat)."""
    ekstensi = os.path.splitext(filename)[1].lower()
    return f'titip_jual/dokumen/{instance.titip_jual_id or "baru"}/{uuid.uuid4().hex}{ekstensi}'


def foto_titipjual_path(instance, filename):
    """Foto kendaraan yang dititipkan, disimpan per-pengajuan."""
    ekstensi = os.path.splitext(filename)[1].lower()
    return f'titip_jual/foto/{instance.titip_jual_id or "baru"}/{uuid.uuid4().hex}{ekstensi}'


def serah_terima_titipjual_path(instance, filename):
    """Foto kondisi serah-terima disimpan privat dengan nama acak."""
    ekstensi = os.path.splitext(filename)[1].lower()
    return f'titip_jual/serah_terima/{instance.titip_jual_id or "baru"}/{uuid.uuid4().hex}{ekstensi}'


class TitipJual(models.Model):
    # ── Status pengajuan & aturan transisi yang diizinkan ──────────
    STATUS_PENGAJUAN = [
        ('draft',       'Draft (Belum Dikirim)'),
        ('pending',     'Menunggu Verifikasi MBG'),
        ('revisi',      'Perlu Revisi Pemilik'),
        ('dijadwalkan', 'Jadwal Inspeksi Dikonfirmasi'),
        ('inspeksi',    'Sedang Inspeksi di Showroom'),
        ('negosiasi',   'Negosiasi Harga'),
        ('disetujui',   'Disetujui & Dititipkan'),
        ('tayang',      'Tayang di Katalog'),
        ('terjual',     'Unit Terjual'),
        ('dikembalikan','Dikembalikan ke Pemilik'),
        ('ditolak',     'Ditolak'),
        ('kadaluarsa',  'Kadaluarsa / Tidak Ada Respons'),
    ]

    # Peta transisi yang diizinkan: status_saat_ini → [status_tujuan yang valid]
    TRANSISI_DIIZINKAN = {
        'draft':       ['pending'],
        'pending':     ['dijadwalkan', 'revisi', 'ditolak', 'kadaluarsa', 'dikembalikan'],
        'revisi':      ['pending', 'ditolak', 'dikembalikan'],
        'dijadwalkan': ['inspeksi', 'revisi', 'ditolak', 'dikembalikan'],
        'inspeksi':    ['negosiasi', 'revisi', 'ditolak', 'dikembalikan'],
        'negosiasi':   ['disetujui', 'ditolak', 'dikembalikan'],
        'disetujui':   ['tayang', 'dikembalikan'],
        'tayang':      ['terjual', 'dikembalikan'],
        'terjual':     [],            # terminal
        'dikembalikan':[],            # terminal
        'ditolak':     ['pending'],   # boleh ajukan ulang
        'kadaluarsa':  ['pending'],
    }

    TRANSMISI_CHOICES = [
        ('AT', 'Otomatis (AT)'),
        ('MT', 'Manual (MT)'),
        ('CVT', 'CVT'),
        ('DCT', 'DCT'),
    ]

    BAHAN_BAKAR_CHOICES = [
        ('bensin',  'Bensin'),
        ('diesel',  'Diesel'),
        ('listrik', 'Listrik / EV'),
        ('hybrid',  'Hybrid'),
    ]

    STATUS_PERSETUJUAN_HARGA = [
        ('belum_diajukan', 'Belum Ada Rekomendasi'),
        ('menunggu', 'Menunggu Jawaban Pemilik'),
        ('disetujui', 'Disetujui Pemilik'),
        ('ditolak', 'Ditolak Pemilik'),
    ]

    # ── Relasi ke akun pengguna ─────────────────────────────────────
    pengguna = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='titip_jual',
        verbose_name="Akun Pengguna"
    )

    # ── Identitas pemilik ──────────────────────────────────────────
    nama_pemilik = models.CharField(max_length=100, verbose_name="Nama Pemilik")
    no_whatsapp  = models.CharField(
        max_length=20, verbose_name="Nomor WhatsApp",
        help_text="Format: 08xx-xxxx-xxxx atau +62xxx"
    )
    email        = models.EmailField(blank=True, null=True, verbose_name="Email")
    alamat_pemilik = models.TextField(blank=True, verbose_name="Alamat Pemilik")
    no_ktp       = models.CharField(
        max_length=20, blank=True, verbose_name="Nomor KTP",
        help_text="Data lama belum terenkripsi; batasi akses hanya untuk staf berizin."
    )

    # ── Detail kendaraan ───────────────────────────────────────────
    merek        = models.CharField(max_length=100, default='', verbose_name="Merek", help_text="Contoh: Honda, Toyota")
    model        = models.CharField(max_length=100, default='', verbose_name="Model", help_text="Contoh: Jazz RS, Avanza G")
    varian       = models.CharField(max_length=100, blank=True, verbose_name="Varian/Tipe")
    tahun        = models.IntegerField(default=0, verbose_name="Tahun Produksi")
    transmisi    = models.CharField(max_length=20, choices=TRANSMISI_CHOICES, default='AT', verbose_name="Transmisi")
    bahan_bakar  = models.CharField(max_length=10, choices=BAHAN_BAKAR_CHOICES, default='bensin', verbose_name="Bahan Bakar")
    warna        = models.CharField(max_length=50, default='', verbose_name="Warna")
    plat_nomor   = models.CharField(max_length=20, blank=True, verbose_name="Plat Nomor")
    jarak_tempuh = models.IntegerField(
        default=0, validators=[MinValueValidator(0)],
        verbose_name="Jarak Tempuh (KM)"
    )
    no_rangka    = models.CharField(max_length=50, blank=True, verbose_name="Nomor Rangka (VIN)")
    no_mesin     = models.CharField(max_length=50, blank=True, verbose_name="Nomor Mesin")

    # ── Kondisi & catatan ──────────────────────────────────────────
    kondisi_eksterior = models.TextField(blank=True, verbose_name="Kondisi Eksterior")
    kondisi_interior  = models.TextField(blank=True, verbose_name="Kondisi Interior")
    kondisi_mesin     = models.TextField(blank=True, verbose_name="Kondisi Mesin")
    riwayat_servis    = models.TextField(blank=True, verbose_name="Riwayat Servis")
    catatan_tambahan  = models.TextField(blank=True, verbose_name="Catatan Tambahan")

    # ── Harga & komisi ─────────────────────────────────────────────
    harga_harapan      = models.DecimalField(
        max_digits=12, decimal_places=0, verbose_name="Harga Harapan Pemilik (Rp)"
    )
    harga_kesepakatan  = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True,
        verbose_name="Harga Kesepakatan MBG (Rp)",
        help_text="Harga yang disepakati antara pemilik dan MBG"
    )
    persen_komisi      = models.DecimalField(
        max_digits=5, decimal_places=2, default=5.00,
        verbose_name="Komisi MBG (%)",
        help_text="Persentase komisi MBG dari harga terjual"
    )
    harga_jual_minimum = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True,
        verbose_name="Harga Jual Minimum (Rp)",
        help_text="Batas bawah harga yang disetujui pemilik"
    )

    # ── Status & waktu ─────────────────────────────────────────────
    status       = models.CharField(
        max_length=20, choices=STATUS_PENGAJUAN, default='draft',
        db_index=True, verbose_name="Status Pengajuan"
    )
    catatan_admin = models.TextField(
        blank=True, verbose_name="Catatan Internal Admin",
        help_text="Hanya terlihat oleh admin MBG"
    )
    alasan_penolakan = models.TextField(
        blank=True, verbose_name="Alasan Penolakan/Dikembalikan",
        help_text="Dikirimkan ke pemilik sebagai notifikasi"
    )
    catatan_revisi = models.TextField(blank=True, verbose_name="Catatan Revisi untuk Pemilik")
    tanggal_inspeksi_dijadwalkan = models.DateTimeField(
        null=True, blank=True, verbose_name="Jadwal Inspeksi"
    )
    hasil_inspeksi = models.TextField(blank=True, verbose_name="Hasil Inspeksi MBG")
    harga_rekomendasi = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True,
        verbose_name="Rekomendasi Harga MBG (Rp)"
    )
    status_persetujuan_harga = models.CharField(
        max_length=20, choices=STATUS_PERSETUJUAN_HARGA,
        default='belum_diajukan', verbose_name="Persetujuan Harga Pemilik"
    )
    tanggal_persetujuan_harga = models.DateTimeField(
        null=True, blank=True, verbose_name="Tanggal Persetujuan Harga"
    )
    tanggal_disetujui = models.DateTimeField(null=True, blank=True, verbose_name="Tanggal Disetujui")
    tanggal_terjual   = models.DateTimeField(null=True, blank=True, verbose_name="Tanggal Terjual")
    tanggal_kadaluarsa = models.DateField(
        null=True, blank=True, verbose_name="Batas Waktu Penitipan",
        help_text="Jika melewati tanggal ini tanpa terjual, unit dikembalikan"
    )

    # ── Relasi ke Produk (saat unit masuk katalog) ─────────────────
    produk_terkait = models.OneToOneField(
        'Produk', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='titip_jual_asal',
        verbose_name="Produk Katalog",
        help_text="Terisi otomatis saat pengajuan disetujui & unit masuk katalog"
    )

    # Foto utama (backward-compat dengan data lama)
    foto_kendaraan = models.ImageField(
        upload_to='titip_jual/', blank=True, null=True,
        verbose_name="Foto Utama Kendaraan (Legacy)"
    )

    # Backward-compat: field lama merek_model (read-only setelah migrasi)
    merek_model = models.CharField(
        max_length=150, blank=True,
        verbose_name="Merek & Model (Legacy)",
        help_text="Field lama, dipertahankan untuk data historis"
    )

    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Pengajuan Titip-Jual (C2C)"
        verbose_name_plural = "Pengajuan Titip-Jual (C2C)"
        ordering            = ['-created_at']
        indexes             = [
            models.Index(fields=['status']),
            models.Index(fields=['pengguna', 'status']),
        ]

    def __str__(self):
        nama_kendaraan = f"{self.merek} {self.model}".strip() or self.merek_model or "Kendaraan"
        return f"#{self.id} {nama_kendaraan} ({self.tahun}) — {self.nama_pemilik} [{self.get_status_display()}]"

    @property
    def nama_kendaraan(self):
        """Nama kendaraan yang bersih untuk ditampilkan."""
        bagian = [b for b in [self.merek, self.model, self.varian] if b]
        return ' '.join(bagian) if bagian else self.merek_model or '—'

    def transisi_diizinkan(self):
        """Daftar status yang dapat dituju dari status saat ini."""
        return self.TRANSISI_DIIZINKAN.get(self.status, [])

    def dapat_transisi_ke(self, status_baru):
        """Cek apakah transisi ke status_baru valid."""
        return status_baru in self.transisi_diizinkan()

    def foto_wajib_tersedia(self):
        wajib = {'depan', 'belakang', 'samping_kiri', 'samping_kanan', 'ruang_mesin'}
        tersedia = set(
            self.galeri_foto.exclude(gambar='').values_list('jenis_foto', flat=True)
        )
        return wajib.issubset(tersedia)

    def serah_terima_masuk_lengkap(self):
        return self.serah_terima.filter(
            jenis='masuk',
            nama_penyerah__gt='',
            nama_penerima__gt='',
            lokasi__gt='',
            odometer_saat_ini__isnull=False,
            jumlah_kunci__isnull=False,
            kelengkapan__gt='',
            foto_kondisi__gt='',
            dicatat_oleh__isnull=False,
        ).exists()

    def lakukan_transisi(self, status_baru, oleh_user=None, catatan=''):
        """
        Eksekusi perubahan status dengan validasi transisi.
        Buat entri RiwayatStatusTitipJual secara otomatis.
        Raise ValueError jika transisi tidak diizinkan.
        """
        with transaction.atomic():
            TitipJual.objects.select_for_update().get(pk=self.pk)
            self.refresh_from_db()
            if not self.dapat_transisi_ke(status_baru):
                raise ValueError(
                    f"Transisi dari '{self.get_status_display()}' ke "
                    f"'{dict(self.STATUS_PENGAJUAN).get(status_baru, status_baru)}' tidak diizinkan."
                )
            if status_baru in ('dijadwalkan', 'inspeksi', 'negosiasi', 'disetujui', 'tayang'):
                if not self.foto_wajib_tersedia():
                    raise ValueError('Lima foto wajib harus tersedia sebelum pengajuan diproses.')
            if status_baru in ('negosiasi', 'disetujui', 'tayang'):
                inspeksi = self.inspeksi_c2c.order_by('-tanggal_waktu', '-pk').first()
                if not inspeksi or inspeksi.status != 'lulus':
                    raise ValueError('Inspeksi terakhir wajib berstatus lulus sebelum melanjutkan.')
            if status_baru == 'disetujui' and (
                self.status_persetujuan_harga != 'disetujui'
                or not self.harga_kesepakatan
                or self.harga_kesepakatan <= 0
            ):
                raise ValueError('Persetujuan dan harga final dari pemilik wajib tersedia.')
            if status_baru == 'tayang':
                if (
                    self.status_persetujuan_harga != 'disetujui'
                    or not self.harga_kesepakatan
                    or self.harga_kesepakatan <= 0
                ):
                    raise ValueError('Persetujuan pemilik atas harga final wajib tersedia sebelum ditayangkan.')
                if not self.serah_terima_masuk_lengkap():
                    raise ValueError('Serah-terima masuk yang lengkap wajib dicatat sebelum kendaraan ditayangkan.')
            if status_baru == 'terjual' and not self.transaksi_penjualan.filter(status='selesai').exists():
                raise ValueError('Penjualan harus diverifikasi dan diselesaikan melalui catatan transaksi C2C.')
            if (
                status_baru == 'dikembalikan'
                and self.serah_terima.filter(jenis='masuk').exists()
                and not self.serah_terima.filter(
                    jenis='keluar',
                    tujuan_keluar='pemilik',
                    nama_penyerah__gt='',
                    nama_penerima__gt='',
                    lokasi__gt='',
                    odometer_saat_ini__isnull=False,
                    jumlah_kunci__isnull=False,
                    kelengkapan__gt='',
                    foto_kondisi__gt='',
                    dicatat_oleh__isnull=False,
                ).exists()
            ):
                raise ValueError('Serah-terima keluar kepada pemilik wajib dicatat sebelum unit dikembalikan.')
            status_lama = self.status
            now = timezone.now()
            self.status = status_baru
            if status_baru == 'disetujui' and not self.tanggal_disetujui:
                self.tanggal_disetujui = now
            elif status_baru == 'terjual' and not self.tanggal_terjual:
                self.tanggal_terjual = now
            self.save(update_fields=['status', 'tanggal_disetujui', 'tanggal_terjual', 'updated_at'])
            RiwayatStatusTitipJual.objects.create(
                titip_jual=self,
                status_dari=status_lama,
                status_ke=status_baru,
                diubah_oleh=oleh_user,
                catatan=catatan,
            )
        return self


# ── 3a. Foto Galeri Kendaraan TitipJual ───────────────────────────

class FotoTitipJual(models.Model):
    """Foto-foto tambahan kendaraan yang dititipkan (reuse pola FotoMobil)."""
    JENIS_FOTO = [
        ('depan', 'Tampak Depan'),
        ('belakang', 'Tampak Belakang'),
        ('samping_kiri', 'Samping Kiri'),
        ('samping_kanan', 'Samping Kanan'),
        ('ruang_mesin', 'Ruang Mesin'),
        ('tambahan', 'Foto Tambahan'),
    ]

    titip_jual  = models.ForeignKey(
        TitipJual, on_delete=models.CASCADE, related_name='galeri_foto',
        verbose_name="Pengajuan Titip-Jual"
    )
    gambar      = models.ImageField(
        upload_to=foto_titipjual_path, verbose_name="Foto"
    )
    jenis_foto  = models.CharField(
        max_length=20, choices=JENIS_FOTO, default='tambahan',
        verbose_name="Jenis Foto"
    )
    keterangan  = models.CharField(
        max_length=100, blank=True,
        verbose_name="Keterangan",
        help_text="Misal: Eksterior Depan, Interior, Mesin, Odometer"
    )
    urutan      = models.PositiveSmallIntegerField(default=0, verbose_name="Urutan")
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Foto Kendaraan Titip-Jual"
        verbose_name_plural = "Foto Kendaraan Titip-Jual"
        ordering            = ['urutan', 'id']

    def __str__(self):
        return f"Foto {self.keterangan or self.id} — {self.titip_jual}"


# ── 3b. Dokumen Privat TitipJual ──────────────────────────────────

class DokumenTitipJual(models.Model):
    """
    Dokumen kendaraan yang bersifat privat (STNK, BPKB, KTP pemilik, dsb.).
    Path disimpan dengan UUID sehingga URL tidak mudah ditebak.
    Akses file hanya lewat view yang sudah terautentikasi dan ber-otorisasi.
    """
    JENIS_DOKUMEN = [
        ('stnk',       'STNK (Surat Tanda Nomor Kendaraan)'),
        ('bpkb',       'BPKB (Buku Pemilik Kendaraan Bermotor)'),
        ('ktp_pemilik','KTP Pemilik'),
        ('faktur',     'Faktur Pembelian'),
        ('hasil_inspeksi', 'Laporan Hasil Inspeksi MBG'),
        ('perjanjian', 'Perjanjian Titip-Jual'),
        ('surat_kuasa','Surat Kuasa'),
        ('lainnya',    'Dokumen Lainnya'),
    ]

    titip_jual  = models.ForeignKey(
        TitipJual, on_delete=models.CASCADE, related_name='dokumen',
        verbose_name="Pengajuan Titip-Jual"
    )
    jenis       = models.CharField(
        max_length=20, choices=JENIS_DOKUMEN, default='lainnya',
        verbose_name="Jenis Dokumen"
    )
    nama_file   = models.CharField(
        max_length=200, verbose_name="Referensi File Acak",
        help_text="Nama referensi acak; bukan nama asli dari unggahan pengguna"
    )
    file        = models.FileField(
        upload_to=dokumen_titipjual_path,
        storage=PrivateTitipJualStorage(),
        verbose_name="File Dokumen",
        help_text="PDF, JPG, PNG — maks 10 MB"
    )
    keterangan  = models.CharField(max_length=200, blank=True, verbose_name="Keterangan")
    diunggah_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='dokumen_titipjual_diunggah',
        verbose_name="Diunggah Oleh"
    )
    sudah_diverifikasi = models.BooleanField(default=False, verbose_name="Sudah Diverifikasi")
    catatan_verifikasi = models.TextField(blank=True, verbose_name="Catatan Verifikasi")
    diverifikasi_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='dokumen_titipjual_diverifikasi',
        verbose_name="Diverifikasi Oleh"
    )
    tanggal_verifikasi = models.DateTimeField(
        null=True, blank=True, verbose_name="Tanggal Verifikasi"
    )
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Dokumen Titip-Jual"
        verbose_name_plural = "Dokumen Titip-Jual"
        ordering            = ['jenis', 'id']

    def __str__(self):
        return f"{self.get_jenis_display()} — {self.titip_jual}"


class PermintaanTitipJual(models.Model):
    JENIS_CHOICES = [
        ('ubah_harga', 'Perubahan Harga'),
        ('pembatalan', 'Pembatalan Pengajuan'),
    ]
    STATUS_CHOICES = [
        ('menunggu', 'Menunggu Tindakan Admin'),
        ('disetujui', 'Disetujui'),
        ('ditolak', 'Ditolak'),
    ]

    titip_jual = models.ForeignKey(
        TitipJual, on_delete=models.CASCADE, related_name='permintaan_pemilik',
        verbose_name="Pengajuan Titip-Jual"
    )
    jenis = models.CharField(max_length=20, choices=JENIS_CHOICES, verbose_name="Jenis Permintaan")
    harga_diminta = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True,
        verbose_name="Harga yang Diminta (Rp)"
    )
    catatan = models.TextField(blank=True, verbose_name="Catatan Pemilik")
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default='menunggu',
        verbose_name="Status Permintaan"
    )
    catatan_admin = models.TextField(blank=True, verbose_name="Tanggapan Admin")
    dibuat_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='permintaan_titipjual',
        verbose_name="Diajukan Oleh"
    )
    diproses_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='permintaan_titipjual_diproses',
        verbose_name="Diproses Oleh"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Permintaan Pemilik Titip-Jual"
        verbose_name_plural = "Permintaan Pemilik Titip-Jual"
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_jenis_display()} — {self.titip_jual}"


# ── 3c. Riwayat Perubahan Status ──────────────────────────────────

class RiwayatStatusTitipJual(models.Model):
    """
    Log audit setiap perubahan status pengajuan TitipJual.
    Dibuat otomatis melalui TitipJual.lakukan_transisi() atau secara manual
    oleh admin dari panel.
    """
    titip_jual   = models.ForeignKey(
        TitipJual, on_delete=models.CASCADE, related_name='riwayat_status',
        verbose_name="Pengajuan"
    )
    status_dari  = models.CharField(
        max_length=20, choices=TitipJual.STATUS_PENGAJUAN,
        verbose_name="Status Sebelumnya"
    )
    status_ke    = models.CharField(
        max_length=20, choices=TitipJual.STATUS_PENGAJUAN,
        verbose_name="Status Baru"
    )
    diubah_oleh  = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='riwayat_titipjual',
        verbose_name="Diubah Oleh"
    )
    catatan      = models.TextField(blank=True, verbose_name="Catatan Perubahan")
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Riwayat Status Titip-Jual"
        verbose_name_plural = "Riwayat Status Titip-Jual"
        ordering            = ['-created_at']

    def __str__(self):
        return (
            f"#{self.titip_jual_id}: {self.status_dari} → {self.status_ke} "
            f"({self.created_at.strftime('%d/%m/%Y %H:%M') if self.created_at else '—'})"
        )


# ── 3d. Serah Terima Kendaraan di Showroom ────────────────────────

class SerahTerimaUnit(models.Model):
    """
    Pencatatan serah terima fisik kendaraan antara pemilik dan MBG.
    Dua jenis: masuk (pemilik serahkan ke MBG) dan keluar (dikembalikan/terjual).
    """
    JENIS_SERAH_TERIMA = [
        ('masuk',  'Penerimaan Unit dari Pemilik (Masuk Showroom)'),
        ('keluar', 'Pengembalian / Penyerahan ke Pembeli (Keluar Showroom)'),
    ]

    TUJUAN_KELUAR_CHOICES = [
        ('pemilik', 'Dikembalikan kepada Pemilik'),
        ('pembeli', 'Diserahkan kepada Pembeli'),
    ]

    DOKUMEN_CHOICES = [
        ('stnk', 'STNK'),
        ('bpkb', 'BPKB'),
        ('faktur', 'Faktur'),
        ('buku_servis', 'Buku servis'),
        ('kunci_cadangan', 'Kunci cadangan'),
        ('lainnya', 'Dokumen/barang lainnya'),
        ('tidak_ada', 'Tidak ada kelengkapan yang diterima'),
    ]

    KONDISI_CHOICES = [
        ('baik',   'Baik'),
        ('sedang', 'Sedang — ada catatan minor'),
        ('perlu_perbaikan', 'Perlu Perbaikan'),
    ]

    titip_jual      = models.ForeignKey(
        TitipJual, on_delete=models.CASCADE, related_name='serah_terima',
        verbose_name="Pengajuan Titip-Jual"
    )
    jenis           = models.CharField(
        max_length=10, choices=JENIS_SERAH_TERIMA, default='masuk',
        verbose_name="Jenis Serah Terima"
    )
    tujuan_keluar   = models.CharField(
        max_length=10, choices=TUJUAN_KELUAR_CHOICES, blank=True, default='',
        verbose_name="Tujuan Unit Keluar"
    )
    tanggal_waktu   = models.DateTimeField(verbose_name="Tanggal & Waktu Serah Terima")
    nama_penyerah   = models.CharField(max_length=100, verbose_name="Nama Pihak Penyerah")
    nama_penerima   = models.CharField(max_length=100, verbose_name="Nama Pihak Penerima")
    lokasi          = models.CharField(
        max_length=200, default='Showroom MBG — Jl. A.P. Pettarani No. 88, Makassar',
        verbose_name="Lokasi"
    )
    kondisi_unit    = models.CharField(
        max_length=20, choices=KONDISI_CHOICES, default='baik',
        verbose_name="Kondisi Unit Saat Serah Terima"
    )
    odometer_saat_ini = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(0)],
        verbose_name="Odometer Saat Serah Terima (KM)"
    )
    jumlah_kunci    = models.PositiveSmallIntegerField(
        null=True, blank=True, verbose_name="Jumlah Kunci"
    )
    catatan_kondisi = models.TextField(blank=True, verbose_name="Catatan Kondisi")
    kelengkapan     = models.TextField(
        blank=True, verbose_name="Kelengkapan",
        help_text="Daftar kelengkapan yang diserahkan: kunci, STNK, karpet, dll."
    )
    kelengkapan_dokumen = models.JSONField(
        default=list, blank=True, verbose_name="Checklist Dokumen/Kelengkapan"
    )
    foto_kondisi = models.ImageField(
        upload_to=serah_terima_titipjual_path,
        storage=PrivateTitipJualStorage(),
        blank=True,
        verbose_name="Foto Kondisi Serah Terima",
    )
    transaksi_penjualan = models.OneToOneField(
        'TransaksiTitipJual', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='serah_terima_keluar',
        verbose_name="Transaksi Penjualan C2C",
    )
    dicatat_oleh    = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='serah_terima_dicatat',
        verbose_name="Dicatat Oleh (Staf MBG)"
    )
    # Tanda tangan digital — simpan hash/nama file
    ttd_penyerah    = models.CharField(
        max_length=200, blank=True,
        verbose_name="Tanda Tangan Penyerah (referensi)"
    )
    ttd_penerima    = models.CharField(
        max_length=200, blank=True,
        verbose_name="Tanda Tangan Penerima (referensi)"
    )
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Serah Terima Unit"
        verbose_name_plural = "Serah Terima Unit"
        ordering            = ['-tanggal_waktu']

    def __str__(self):
        return (
            f"{self.get_jenis_display()} — "
            f"{self.titip_jual.nama_kendaraan} "
            f"({self.tanggal_waktu.strftime('%d/%m/%Y') if self.tanggal_waktu else '—'})"
        )

    @property
    def lengkap(self):
        return bool(
            self.nama_penyerah.strip()
            and self.nama_penerima.strip()
            and self.lokasi.strip()
            and self.odometer_saat_ini is not None
            and self.jumlah_kunci is not None
            and self.kelengkapan
            and self.kelengkapan_dokumen
            and self.foto_kondisi
            and self.dicatat_oleh_id
        )


class ChecklistInspeksiC2C(models.Model):
    kode = models.SlugField(max_length=60, unique=True, verbose_name="Kode")
    kategori = models.CharField(max_length=100, verbose_name="Kategori")
    nama = models.CharField(max_length=160, verbose_name="Poin Pemeriksaan")
    wajib = models.BooleanField(default=True, verbose_name="Wajib Lulus")
    aktif = models.BooleanField(default=True, verbose_name="Aktif")
    urutan = models.PositiveSmallIntegerField(default=0, verbose_name="Urutan")

    class Meta:
        ordering = ['urutan', 'pk']
        verbose_name = "Poin Checklist Inspeksi C2C"
        verbose_name_plural = "Checklist Inspeksi C2C"

    def __str__(self):
        return f'{self.kategori}: {self.nama}'


class InspeksiC2C(models.Model):
    STATUS_CHOICES = [
        ('lulus', 'Lulus'),
        ('gagal', 'Tidak Lulus'),
    ]

    titip_jual = models.ForeignKey(
        TitipJual, on_delete=models.CASCADE, related_name='inspeksi_c2c',
        verbose_name="Pengajuan Titip-Jual",
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, verbose_name="Hasil")
    pemeriksa = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name='inspeksi_titipjual', verbose_name="Pemeriksa",
    )
    tanggal_waktu = models.DateTimeField(default=timezone.now, verbose_name="Tanggal Pemeriksaan")
    catatan = models.TextField(blank=True, verbose_name="Catatan Pemeriksaan")
    harga_rekomendasi = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True,
        verbose_name="Rekomendasi Harga (Rp)",
    )

    class Meta:
        ordering = ['-tanggal_waktu', '-pk']
        verbose_name = "Hasil Inspeksi C2C"
        verbose_name_plural = "Hasil Inspeksi C2C"

    def __str__(self):
        return f'Inspeksi {self.titip_jual} — {self.get_status_display()}'


class HasilChecklistInspeksiC2C(models.Model):
    HASIL_CHOICES = [
        ('lulus', 'Lulus'),
        ('gagal', 'Tidak Lulus'),
        ('tidak_berlaku', 'Tidak Berlaku'),
    ]

    inspeksi = models.ForeignKey(
        InspeksiC2C, on_delete=models.CASCADE, related_name='hasil_checklist',
        verbose_name="Inspeksi",
    )
    poin = models.ForeignKey(
        ChecklistInspeksiC2C, on_delete=models.PROTECT,
        related_name='hasil_inspeksi', verbose_name="Poin Pemeriksaan",
    )
    hasil = models.CharField(max_length=15, choices=HASIL_CHOICES, verbose_name="Hasil")
    catatan = models.CharField(max_length=500, blank=True, verbose_name="Catatan")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['inspeksi', 'poin'], name='uniq_c2c_inspection_item'),
        ]
        verbose_name = "Hasil Poin Inspeksi C2C"
        verbose_name_plural = "Hasil Poin Inspeksi C2C"

    def __str__(self):
        return f'{self.poin}: {self.get_hasil_display()}'


class TransaksiTitipJual(models.Model):
    SUMBER_CHOICES = [
        ('pemilik', 'Pembeli ditemukan Pemilik'),
        ('mbg', 'Pembeli ditemukan MBG'),
    ]
    STATUS_CHOICES = [
        ('menunggu_verifikasi', 'Menunggu Verifikasi MBG'),
        ('terverifikasi', 'Terverifikasi — Menunggu Penyelesaian'),
        ('ditolak', 'Laporan Ditolak'),
        ('selesai', 'Selesai'),
    ]
    TRANSISI_DIIZINKAN = {
        'menunggu_verifikasi': ['terverifikasi', 'ditolak'],
        'terverifikasi': ['selesai'],
        'ditolak': [],
        'selesai': [],
    }

    titip_jual = models.ForeignKey(
        TitipJual, on_delete=models.PROTECT, related_name='transaksi_penjualan',
        verbose_name="Pengajuan Titip-Jual",
    )
    sumber = models.CharField(max_length=10, choices=SUMBER_CHOICES, verbose_name="Sumber Pembeli")
    nama_pembeli = models.CharField(max_length=120, verbose_name="Nama Pembeli")
    kontak_pembeli = models.CharField(max_length=120, verbose_name="Kontak Pembeli")
    harga_final = models.DecimalField(max_digits=12, decimal_places=0, verbose_name="Harga Transaksi (Rp)")
    tanggal_kesepakatan = models.DateField(verbose_name="Tanggal Kesepakatan")
    catatan_pelapor = models.TextField(blank=True, verbose_name="Catatan Transaksi")
    status = models.CharField(
        max_length=24, choices=STATUS_CHOICES, default='menunggu_verifikasi',
        db_index=True, verbose_name="Status Transaksi",
    )
    dilaporkan_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, related_name='transaksi_titipjual_dilaporkan',
        verbose_name="Dilaporkan Oleh",
    )
    diverifikasi_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='transaksi_titipjual_diverifikasi',
        verbose_name="Diverifikasi Oleh",
    )
    tanggal_verifikasi = models.DateTimeField(null=True, blank=True, verbose_name="Tanggal Verifikasi")
    catatan_verifikasi = models.TextField(blank=True, verbose_name="Catatan Verifikasi")
    komisi_final = models.DecimalField(
        max_digits=12, decimal_places=0, null=True, blank=True,
        verbose_name="Komisi Sesuai Perjanjian (Rp)",
    )
    komisi_diperiksa_berdasarkan = models.TextField(
        blank=True, verbose_name="Dasar Pemeriksaan Komisi",
        help_text="Catat rujukan klausul perjanjian yang telah diperiksa.",
    )
    komisi_diperiksa_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='komisi_titipjual_diperiksa',
        verbose_name="Komisi Diperiksa Oleh",
    )
    tanggal_komisi_diperiksa = models.DateTimeField(
        null=True, blank=True, verbose_name="Tanggal Pemeriksaan Komisi",
    )
    catatan_administrasi = models.TextField(blank=True, verbose_name="Catatan Penyelesaian Administrasi")
    administrasi_diselesaikan_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='administrasi_titipjual_diselesaikan',
        verbose_name="Administrasi Diselesaikan Oleh",
    )
    tanggal_administrasi_selesai = models.DateTimeField(
        null=True, blank=True, verbose_name="Tanggal Administrasi Selesai",
    )
    diselesaikan_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='transaksi_titipjual_diselesaikan',
        verbose_name="Diselesaikan Oleh",
    )
    tanggal_selesai = models.DateTimeField(null=True, blank=True, verbose_name="Tanggal Selesai")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['titip_jual', 'status'])]
        verbose_name = "Transaksi Penjualan Titip-Jual"
        verbose_name_plural = "Transaksi Penjualan Titip-Jual"

    def __str__(self):
        return f'{self.titip_jual.nama_kendaraan} — {self.get_sumber_display()}'

    def lakukan_transisi(self, status_baru, oleh_user=None, catatan=''):
        with transaction.atomic():
            TitipJual.objects.select_for_update().get(pk=self.titip_jual_id)
            TransaksiTitipJual.objects.select_for_update().get(pk=self.pk)
            self.refresh_from_db()
            if status_baru not in self.TRANSISI_DIIZINKAN.get(self.status, []):
                raise ValueError('Transisi status transaksi C2C tidak diizinkan.')
            if status_baru in ('terverifikasi', 'ditolak', 'selesai') and not (
                oleh_user and (oleh_user.is_staff or oleh_user.is_superuser)
            ):
                raise ValueError('Transisi transaksi hanya dapat dilakukan oleh staf MBG.')
            pengajuan = TitipJual.objects.get(pk=self.titip_jual_id)
            if status_baru == 'terverifikasi':
                if pengajuan.status != 'tayang':
                    raise ValueError('Pengajuan tidak lagi berada pada tahap penjualan.')
                if (
                    pengajuan.harga_jual_minimum
                    and self.harga_final < pengajuan.harga_jual_minimum
                ):
                    raise ValueError('Harga transaksi di bawah batas minimum yang disetujui pemilik.')
            if status_baru == 'ditolak' and not catatan.strip():
                raise ValueError('Alasan penolakan laporan wajib dicatat.')
            if status_baru == 'selesai':
                serah_terima = getattr(self, 'serah_terima_keluar', None)
                if pengajuan.status != 'tayang':
                    raise ValueError('Pengajuan tidak berada pada tahap penjualan.')
                if not pengajuan.dokumen.filter(
                    jenis='perjanjian', sudah_diverifikasi=True
                ).exists():
                    raise ValueError('Perjanjian titip-jual yang terverifikasi wajib tersedia.')
                if (
                    not serah_terima
                    or serah_terima.jenis != 'keluar'
                    or serah_terima.tujuan_keluar != 'pembeli'
                    or not serah_terima.lengkap
                ):
                    raise ValueError('Serah-terima lengkap kepada pembeli wajib tercatat.')
                if (
                    self.komisi_final is None
                    or not self.komisi_diperiksa_berdasarkan.strip()
                    or not self.komisi_diperiksa_oleh_id
                    or not self.tanggal_komisi_diperiksa
                    or not self.administrasi_diselesaikan_oleh_id
                    or not self.tanggal_administrasi_selesai
                    or not self.catatan_administrasi.strip()
                ):
                    raise ValueError('Pemeriksaan komisi dan administrasi wajib diselesaikan.')
            status_lama = self.status
            self.status = status_baru
            if status_baru == 'terverifikasi':
                self.diverifikasi_oleh = oleh_user
                self.tanggal_verifikasi = timezone.now()
                self.catatan_verifikasi = catatan
            elif status_baru == 'selesai':
                self.diselesaikan_oleh = oleh_user
                self.tanggal_selesai = timezone.now()
            self.save()
            RiwayatTransaksiTitipJual.objects.create(
                transaksi=self,
                status_dari=status_lama,
                status_ke=status_baru,
                diubah_oleh=oleh_user,
                catatan=catatan,
            )
        return self


class RiwayatTransaksiTitipJual(models.Model):
    transaksi = models.ForeignKey(
        TransaksiTitipJual, on_delete=models.CASCADE, related_name='riwayat',
        verbose_name="Transaksi C2C",
    )
    status_dari = models.CharField(max_length=24, blank=True, verbose_name="Status Sebelumnya")
    status_ke = models.CharField(
        max_length=24, choices=TransaksiTitipJual.STATUS_CHOICES, verbose_name="Status Baru",
    )
    diubah_oleh = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='riwayat_transaksi_titipjual',
        verbose_name="Diubah Oleh",
    )
    catatan = models.TextField(blank=True, verbose_name="Catatan")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']
        verbose_name = "Riwayat Transaksi Titip-Jual"
        verbose_name_plural = "Riwayat Transaksi Titip-Jual"

# 4. MODEL BOOKING CEK UNIT (ALUR O2O)

class BookingCekUnit(models.Model):
    OPSI_DELIVERY = [
        ('showroom', 'Ambil / Cek di Showroom MBG Makassar (Gratis)'),
        ('towing', 'Diantar via Truk Towing ke Rumah (Makassar)'),
    ]

    STATUS_BOOKING = [
        ('menunggu_dp', 'Menunggu DP / Booking Fee'),
        ('terkonfirmasi', 'Jadwal Terkonfirmasi'),
        ('selesai', 'Selesai / Pelunasan'),
        ('batal', 'Dibatalkan'),
    ]

    produk = models.ForeignKey(Produk, on_delete=models.CASCADE)
    nama_pemesan = models.CharField(max_length=100)
    no_whatsapp = models.CharField(max_length=20)
    email = models.EmailField()
    
    tanggal_cek_unit = models.DateField()
    jam_cek_unit = models.TimeField()
    opsi_delivery = models.CharField(max_length=10, choices=OPSI_DELIVERY, default='showroom')
    alamat_pengantaran = models.TextField(blank=True, null=True, help_text="Wajib diisi jika memilih Towing")
    
    status = models.CharField(max_length=20, choices=STATUS_BOOKING, default='menunggu_dp')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Booking Cek Unit (O2O)"

    def __str__(self):
        return f"Booking #{self.id} - {self.produk.nama} oleh {self.nama_pemesan}"



# 5. MODEL TRANSAKSI & PEMBAYARAN (LK 4)

class Transaksi(models.Model):
    METODE_PEMBAYARAN = [
        ('transfer', 'Transfer Bank Manual'),
        ('va', 'Virtual Account'),
        ('qris', 'QRIS (Booking Fee)'),
        ('showroom', 'Bayar Tunai di Showroom'),
    ]

    STATUS_PEMBAYARAN = [
        ('pending', 'Menunggu Pembayaran'),
        ('berhasil', 'Pembayaran Berhasil'),
        ('gagal', 'Gagal / Expired'),
    ]

    no_order = models.CharField(max_length=50, unique=True, help_text="Contoh: TRX-MBG-20261021-001")
    booking = models.OneToOneField(BookingCekUnit, on_delete=models.CASCADE)
    nominal_dp = models.DecimalField(max_digits=12, decimal_places=0, default=2500000, help_text="Booking fee / Tanda jadi")
    metode_pembayaran = models.CharField(max_length=20, choices=METODE_PEMBAYARAN, default='qris')
    status_pembayaran = models.CharField(max_length=20, choices=STATUS_PEMBAYARAN, default='pending')
    midtrans_order_id = models.CharField(max_length=100, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Transaksi & Pembayaran"

    def __str__(self):
        return f"{self.no_order} - {self.get_status_pembayaran_display()}"
        

# ═══════════════════════════════════════════════════════════════════
# 7. KATEGORI ARTIKEL
# ═══════════════════════════════════════════════════════════════════

class KategoriArtikel(models.Model):
    nama       = models.CharField(max_length=100, unique=True, verbose_name="Nama Kategori")
    slug       = models.SlugField(max_length=120, unique=True, blank=True)
    deskripsi  = models.TextField(blank=True, verbose_name="Deskripsi")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Kategori Artikel"
        verbose_name_plural = "Kategori Artikel"
        ordering            = ['nama']

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.nama)
            slug = base
            n = 1
            while KategoriArtikel.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.nama


# ═══════════════════════════════════════════════════════════════════
# 8. ARTIKEL & BERITA
# ═══════════════════════════════════════════════════════════════════

class Artikel(models.Model):
    STATUS_CHOICES = [
        ('draft',     'Draft'),
        ('published', 'Diterbitkan'),
    ]

    judul            = models.CharField(max_length=200, verbose_name="Judul")
    slug             = models.SlugField(max_length=220, unique=True, blank=True)
    ringkasan        = models.TextField(verbose_name="Ringkasan")
    isi              = models.TextField(verbose_name="Isi Artikel")
    gambar_utama     = models.ImageField(
                           upload_to='artikel/', blank=True, null=True,
                           verbose_name="Gambar Utama"
                       )
    kategori         = models.ForeignKey(
                           KategoriArtikel, on_delete=models.SET_NULL,
                           null=True, blank=True, related_name='artikel',
                           verbose_name="Kategori"
                       )
    penulis          = models.ForeignKey(
                           settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                           null=True, related_name='artikel_ditulis',
                           verbose_name="Penulis"
                       )
    status           = models.CharField(
                           max_length=12, choices=STATUS_CHOICES,
                           default='draft', verbose_name="Status"
                       )
    meta_title       = models.CharField(max_length=200, blank=True, verbose_name="Meta Title")
    meta_description = models.TextField(blank=True, verbose_name="Meta Description")
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Artikel & Berita"
        verbose_name_plural = "Artikel & Berita"
        ordering            = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.judul)
            slug = base
            n = 1
            while Artikel.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self):
        return self.judul


# ═══════════════════════════════════════════════════════════════════
# 9. HALAMAN WEBSITE (halaman informasi: Tentang Kami, S&K, Kebijakan Privasi, dll.)
# ═══════════════════════════════════════════════════════════════════

class HalamanWebsite(models.Model):
    STATUS_CHOICES = [
        ('draft',     'Draft'),
        ('published', 'Diterbitkan'),
    ]

    judul            = models.CharField(max_length=200, verbose_name="Judul Halaman")
    slug             = models.SlugField(max_length=220, unique=True, blank=True)
    ringkasan        = models.TextField(blank=True, verbose_name="Ringkasan")
    isi              = models.TextField(verbose_name="Isi Halaman")
    status           = models.CharField(
                           max_length=12, choices=STATUS_CHOICES,
                           default='draft', verbose_name="Status"
                       )
    tampil_di_footer = models.BooleanField(default=False, verbose_name="Tampilkan di footer")
    urutan           = models.PositiveSmallIntegerField(default=0, verbose_name="Urutan")
    meta_title       = models.CharField(max_length=200, blank=True, verbose_name="Meta Title")
    meta_description = models.TextField(blank=True, verbose_name="Meta Description")
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Halaman Website"
        verbose_name_plural = "Halaman Website"
        ordering            = ['urutan', 'judul']

    def save(self, *args, **kwargs):
        if not self.slug:
            # Judul yang isinya hanya simbol menghasilkan slug kosong → pakai cadangan.
            base = slugify(self.judul) or 'halaman'
            slug = base
            n = 1
            while HalamanWebsite.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('halaman_detail', args=[self.slug])

    def __str__(self):
        return self.judul


# ═══════════════════════════════════════════════════════════════════
# 10. HERO SLIDE / BANNER BERANDA
# ═══════════════════════════════════════════════════════════════════

class HeroSlide(models.Model):
    STATUS_CHOICES = [
        ('draft',     'Draft'),
        ('published', 'Tayang'),
    ]

    judul       = models.CharField(max_length=200, verbose_name="Judul")
    judul_sorot = models.CharField(
                      max_length=200, blank=True, verbose_name="Judul Sorotan",
                      help_text="Opsional. Baris kedua judul yang tampil dengan warna gradasi."
                  )
    deskripsi   = models.TextField(blank=True, verbose_name="Deskripsi")
    label_promo = models.CharField(
                      max_length=150, blank=True, verbose_name="Label Promo",
                      help_text="Opsional. Teks pita kecil di atas judul, mis. promo atau subsidi DP."
                  )
    gambar      = models.ImageField(
                      upload_to='hero/', blank=True, null=True,
                      verbose_name="Gambar Latar"
                  )
    teks_tombol = models.CharField(max_length=60, blank=True, verbose_name="Teks Tombol")
    link_tombol = models.CharField(
                      max_length=300, blank=True, verbose_name="Link Tombol",
                      validators=[validasi_link_tombol]
                  )
    status      = models.CharField(
                      max_length=12, choices=STATUS_CHOICES,
                      default='draft', verbose_name="Status"
                  )
    urutan      = models.PositiveSmallIntegerField(default=0, verbose_name="Urutan")
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Hero Slide"
        verbose_name_plural = "Hero Slide"
        ordering            = ['urutan', 'id']

    def __str__(self):
        return self.judul


# ═══════════════════════════════════════════════════════════════════
# 11. KERJA SAMA / MITRA
# ═══════════════════════════════════════════════════════════════════

class Mitra(models.Model):
    STATUS_CHOICES = [
        ('draft',     'Draft'),
        ('published', 'Tayang'),
    ]
    JENIS_CHOICES = [
        ('pembiayaan', 'Pembiayaan / Leasing'),
        ('bank',       'Bank'),
        ('asuransi',   'Asuransi'),
        ('inspeksi',   'Bengkel & Inspeksi'),
        ('lainnya',    'Lainnya'),
    ]

    nama       = models.CharField(max_length=150, verbose_name="Nama Mitra")
    jenis      = models.CharField(
                     max_length=12, choices=JENIS_CHOICES,
                     default='lainnya', verbose_name="Jenis Kerja Sama"
                 )
    keterangan = models.CharField(max_length=160, blank=True, verbose_name="Keterangan Singkat")
    logo       = models.ImageField(
                     upload_to='mitra/', blank=True, null=True, verbose_name="Logo"
                 )
    situs      = models.CharField(
                     max_length=300, blank=True, verbose_name="Situs Web",
                     validators=[validasi_situs_mitra]
                 )
    status     = models.CharField(
                     max_length=12, choices=STATUS_CHOICES,
                     default='draft', verbose_name="Status"
                 )
    urutan     = models.PositiveSmallIntegerField(default=0, verbose_name="Urutan")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Mitra"
        verbose_name_plural = "Mitra"
        ordering            = ['urutan', 'nama']

    def __str__(self):
        return self.nama


# ═══════════════════════════════════════════════════════════════════
# 12. PENGATURAN WEBSITE (informasi global, satu baris saja)
# ═══════════════════════════════════════════════════════════════════

class PengaturanWebsite(models.Model):
    """
    Informasi global situs (kontak, alamat, jam buka, media sosial). Hanya ada satu baris
    (pk=1). Nilai bawaan sama dengan teks yang dulu ditulis langsung di template, sehingga
    situs tampil seperti biasa sebelum admin pernah menyimpan pengaturan.
    """
    whatsapp         = models.CharField(
                           max_length=15, default='6281234567890',
                           validators=[validasi_whatsapp], verbose_name="Nomor WhatsApp"
                       )
    email            = models.EmailField(
                           blank=True, default='halo@mobilbekasgarasi.com', verbose_name="Email"
                       )
    alamat           = models.CharField(
                           max_length=250,
                           default='Jl. A.P. Pettarani No. 88, Bonto-Bontoa, Makassar, Sulawesi Selatan 90222',
                           verbose_name="Alamat Lengkap"
                       )
    alamat_singkat   = models.CharField(
                           max_length=120, default='Jl. A.P. Pettarani No. 88, Makassar',
                           verbose_name="Alamat Singkat"
                       )
    jam_buka         = models.CharField(
                           max_length=120, default='08.30 – 18.00 WITA Setiap Hari',
                           verbose_name="Jam Operasional"
                       )
    link_peta        = models.CharField(
                           max_length=500, blank=True, validators=[validasi_situs_mitra],
                           verbose_name="Link Google Maps"
                       )
    deskripsi_footer = models.TextField(
                           blank=True,
                           default='Platform e-commerce otomotif terpercaya di Makassar dan Sulawesi Selatan. Mengutamakan transparansi harga, garansi mesin, dan jaminan 100% lulus 150 titik inspeksi dengan model Online-to-Offline (O2O).',
                           verbose_name="Deskripsi Footer"
                       )
    instagram        = models.CharField(max_length=300, blank=True, validators=[validasi_situs_mitra], verbose_name="Instagram")
    facebook         = models.CharField(max_length=300, blank=True, validators=[validasi_situs_mitra], verbose_name="Facebook")
    tiktok           = models.CharField(max_length=300, blank=True, validators=[validasi_situs_mitra], verbose_name="TikTok")
    youtube          = models.CharField(max_length=300, blank=True, validators=[validasi_situs_mitra], verbose_name="YouTube")
    logo             = models.ImageField(
                           upload_to='situs/', blank=True, null=True, verbose_name="Logo Website"
                       )
    favicon          = models.ImageField(
                           upload_to='situs/', blank=True, null=True, verbose_name="Favicon"
                       )
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Pengaturan Website"
        verbose_name_plural = "Pengaturan Website"

    def save(self, *args, **kwargs):
        self.pk = 1   # singleton: selalu satu baris
        super().save(*args, **kwargs)

    @classmethod
    def ambil(cls):
        """Baris pengaturan (dibuat dengan nilai bawaan jika belum ada)."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    @property
    def whatsapp_tampil(self):
        """'6281234567890' → '+62 812-3456-7890' untuk ditampilkan ke pengunjung."""
        angka = self.whatsapp or ''
        if not angka.startswith('62') or len(angka) < 10:
            return angka
        sisa = angka[2:]
        return f"+62 {sisa[:3]}-{sisa[3:7]}-{sisa[7:]}" if len(sisa) > 7 else f"+62 {sisa}"

    def __str__(self):
        return "Pengaturan Website"
