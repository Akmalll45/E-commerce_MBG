from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator
from django.contrib.auth.models import AbstractUser
from django.urls import reverse
from django.utils.text import slugify
import os
import uuid

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

class TitipJual(models.Model):
    STATUS_PENGAJUAN = [
        ('pending', 'Menunggu Verifikasi'),
        ('inspeksi', 'Proses Cek/Inspeksi'),
        ('disetujui', 'Disetujui & Tayang'),
        ('ditolak', 'Ditolak'),
    ]

    nama_pemilik = models.CharField(max_length=100)
    no_whatsapp = models.CharField(max_length=20)
    email = models.EmailField(blank=True, null=True)
    
    merek_model = models.CharField(max_length=150, help_text="Contoh: Honda Jazz RS 2018")
    tahun = models.IntegerField()
    transmisi = models.CharField(max_length=20)
    harga_harapan = models.DecimalField(max_digits=12, decimal_places=0)
    foto_kendaraan = models.ImageField(upload_to='titip_jual/')
    catatan_kondisi = models.TextField(blank=True, null=True)
    
    status = models.CharField(max_length=20, choices=STATUS_PENGAJUAN, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Pengajuan Titip-Jual (C2C)"

    def __str__(self):
        return f"{self.merek_model} - {self.nama_pemilik} ({self.get_status_display()})"



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
