from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.urls import reverse
from django.utils.html import format_html
from .models import (
    CustomUser, Mitra, PengaturanWebsite, Produk, FotoMobil,
    TitipJual, FotoTitipJual, DokumenTitipJual, RiwayatStatusTitipJual, SerahTerimaUnit,
    BookingCekUnit, Transaksi,
    KategoriArtikel, Artikel, HalamanWebsite, HeroSlide,
)


# ── Custom User ───────────────────────────────────────────────────

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ('username', 'email', 'first_name', 'last_name', 'no_whatsapp', 'is_staff', 'is_active')
    list_filter  = ('is_staff', 'is_superuser', 'is_active', 'groups')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'no_whatsapp')
    fieldsets = UserAdmin.fieldsets + (
        ('Informasi Tambahan MBG', {'fields': ('no_whatsapp', 'alamat')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Informasi Tambahan MBG', {'fields': ('no_whatsapp', 'alamat')}),
    )


admin.site.register(CustomUser, CustomUserAdmin)


# ── Produk & Galeri ───────────────────────────────────────────────

class FotoMobilInline(admin.TabularInline):
    model  = FotoMobil
    extra  = 1
    fields = ('gambar', 'keterangan')


@admin.register(Produk)
class ProdukAdmin(admin.ModelAdmin):
    list_display   = ('sku', 'nama', 'merek', 'tahun', 'harga', 'tipe_penjual', 'status_stok')
    list_filter    = ('tipe_penjual', 'status_stok', 'merek')
    search_fields  = ('sku', 'nama', 'merek', 'plat_nomor')
    inlines        = [FotoMobilInline]
    readonly_fields = ('created_at', 'updated_at')


admin.site.register(FotoMobil)


# ── TitipJual & Model Pendukung C2C ───────────────────────────────

class FotoTitipJualInline(admin.TabularInline):
    model   = FotoTitipJual
    extra   = 1
    fields  = ('gambar', 'keterangan', 'urutan')

class DokumenTitipJualInline(admin.TabularInline):
    model   = DokumenTitipJual
    extra   = 0
    fields  = ('jenis', 'nama_file', 'file_link', 'keterangan', 'sudah_diverifikasi')
    readonly_fields = ('file_link',)
    can_delete = False

    @admin.display(description='File privat')
    def file_link(self, obj):
        if not obj or not obj.pk:
            return '—'
        return format_html(
            '<a href="{}">Unduh dokumen privat</a>',
            reverse('dokumen_titip_jual_download', args=[obj.pk]),
        )

class RiwayatStatusInline(admin.TabularInline):
    model   = RiwayatStatusTitipJual
    extra   = 0
    fields  = ('status_dari', 'status_ke', 'diubah_oleh', 'catatan', 'created_at')
    readonly_fields = ('status_dari', 'status_ke', 'diubah_oleh', 'catatan', 'created_at')
    can_delete = False

class SerahTerimaInline(admin.TabularInline):
    model   = SerahTerimaUnit
    extra   = 0
    fields  = ('jenis', 'tanggal_waktu', 'nama_penyerah', 'nama_penerima', 'kondisi_unit', 'odometer_saat_ini')
    readonly_fields = ('created_at',)


@admin.register(TitipJual)
class TitipJualAdmin(admin.ModelAdmin):
    list_display   = ('id', 'nama_kendaraan', 'tahun', 'nama_pemilik', 'no_whatsapp', 'status', 'created_at')
    list_filter    = ('status', 'transmisi', 'bahan_bakar')
    search_fields  = ('merek', 'model', 'nama_pemilik', 'no_whatsapp', 'plat_nomor')
    readonly_fields = ('created_at', 'updated_at', 'nama_kendaraan')
    inlines        = [FotoTitipJualInline, DokumenTitipJualInline, RiwayatStatusInline, SerahTerimaInline]
    fieldsets = (
        ('Identitas Pemilik', {
            'fields': ('pengguna', 'nama_pemilik', 'no_whatsapp', 'email', 'alamat_pemilik', 'no_ktp')
        }),
        ('Detail Kendaraan', {
            'fields': (
                'merek', 'model', 'varian', 'tahun', 'transmisi', 'bahan_bakar',
                'warna', 'plat_nomor', 'jarak_tempuh', 'no_rangka', 'no_mesin',
            )
        }),
        ('Kondisi Kendaraan', {
            'fields': ('kondisi_eksterior', 'kondisi_interior', 'kondisi_mesin', 'riwayat_servis', 'catatan_tambahan'),
            'classes': ('collapse',)
        }),
        ('Harga & Komisi', {
            'fields': ('harga_harapan', 'harga_kesepakatan', 'persen_komisi', 'harga_jual_minimum')
        }),
        ('Status & Administrasi', {
            'fields': (
                'status', 'catatan_admin', 'alasan_penolakan',
                'tanggal_inspeksi_dijadwalkan', 'tanggal_disetujui',
                'tanggal_terjual', 'tanggal_kadaluarsa',
                'produk_terkait',
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(FotoTitipJual)
class FotoTitipJualAdmin(admin.ModelAdmin):
    list_display = ('titip_jual', 'keterangan', 'urutan', 'created_at')
    list_filter  = ('created_at',)


@admin.register(DokumenTitipJual)
class DokumenTitipJualAdmin(admin.ModelAdmin):
    list_display = ('titip_jual', 'jenis', 'nama_file', 'diunggah_oleh', 'created_at')
    list_filter  = ('jenis',)
    search_fields = ('nama_file', 'keterangan')
    fields = ('titip_jual', 'jenis', 'nama_file', 'file_link', 'keterangan', 'diunggah_oleh', 'created_at')
    readonly_fields = ('file_link', 'diunggah_oleh', 'created_at')

    @admin.display(description='File privat')
    def file_link(self, obj):
        if not obj or not obj.pk:
            return '—'
        return format_html(
            '<a href="{}">Unduh dokumen privat</a>',
            reverse('dokumen_titip_jual_download', args=[obj.pk]),
        )

    def has_add_permission(self, request):
        return False


@admin.register(RiwayatStatusTitipJual)
class RiwayatStatusTitipJualAdmin(admin.ModelAdmin):
    list_display   = ('titip_jual', 'status_dari', 'status_ke', 'diubah_oleh', 'created_at')
    list_filter    = ('status_ke',)
    readonly_fields = ('created_at',)
    can_delete     = False


@admin.register(SerahTerimaUnit)
class SerahTerimaUnitAdmin(admin.ModelAdmin):
    list_display   = ('titip_jual', 'jenis', 'tanggal_waktu', 'nama_penyerah', 'nama_penerima', 'kondisi_unit')
    list_filter    = ('jenis', 'kondisi_unit')
    search_fields  = ('nama_penyerah', 'nama_penerima')
    readonly_fields = ('created_at',)


# ── Booking & Transaksi ───────────────────────────────────────────

admin.site.register(BookingCekUnit)
admin.site.register(Transaksi)


# ── CMS & Konten ──────────────────────────────────────────────────

admin.site.register(KategoriArtikel)
admin.site.register(Artikel)
admin.site.register(HalamanWebsite)
admin.site.register(HeroSlide)
admin.site.register(Mitra)


# ── Pengaturan Website (Singleton) ────────────────────────────────

@admin.register(PengaturanWebsite)
class PengaturanWebsiteAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not PengaturanWebsite.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
