from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.html import format_html
from .models import (
    CustomUser, Mitra, PengaturanWebsite, Produk, FotoMobil,
    TitipJual, FotoTitipJual, DokumenTitipJual, RiwayatStatusTitipJual, SerahTerimaUnit,
    ChecklistInspeksiC2C, InspeksiC2C, HasilChecklistInspeksiC2C,
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

    def has_change_permission(self, request, obj=None):
        if obj and (obj.tipe_penjual == 'C2C' or obj.titip_jual_sumber_id):
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and (obj.tipe_penjual == 'C2C' or obj.titip_jual_sumber_id):
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if obj.tipe_penjual == 'C2C' or obj.titip_jual_sumber_id:
            raise PermissionDenied('Produk C2C hanya boleh dikelola melalui panel Titip-Jual.')
        super().save_model(request, obj, form, change)


admin.site.register(FotoMobil)


# ── TitipJual & Model Pendukung C2C ───────────────────────────────

class FotoTitipJualInline(admin.TabularInline):
    model   = FotoTitipJual
    extra   = 0
    fields  = ('gambar', 'keterangan', 'urutan')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

class DokumenTitipJualInline(admin.TabularInline):
    model   = DokumenTitipJual
    extra   = 0
    fields  = ('jenis', 'file_link', 'sudah_diverifikasi', 'catatan_verifikasi')
    readonly_fields = ('jenis', 'file_link', 'sudah_diverifikasi', 'catatan_verifikasi')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

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
    fields  = ('jenis', 'tanggal_waktu', 'nama_penyerah', 'nama_penerima', 'kondisi_unit', 'odometer_saat_ini', 'foto_kondisi_link')
    readonly_fields = (
        'jenis', 'tanggal_waktu', 'nama_penyerah', 'nama_penerima',
        'kondisi_unit', 'odometer_saat_ini', 'jumlah_kunci',
        'kelengkapan_dokumen', 'foto_kondisi_link', 'dicatat_oleh', 'created_at',
    )
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description='Foto kondisi privat')
    def foto_kondisi_link(self, obj):
        if not obj or not obj.pk or not obj.foto_kondisi:
            return '—'
        return format_html(
            '<a href="{}">Lihat foto privat</a>',
            reverse('serah_terima_foto_download', args=[obj.pk]),
        )


@admin.register(TitipJual)
class TitipJualAdmin(admin.ModelAdmin):
    list_display   = ('id', 'nama_kendaraan', 'tahun', 'nama_pemilik', 'no_whatsapp', 'status', 'created_at')
    list_filter    = ('status', 'transmisi', 'bahan_bakar')
    search_fields  = ('merek', 'model', 'nama_pemilik', 'no_whatsapp', 'plat_nomor')
    readonly_fields = tuple(field.name for field in TitipJual._meta.fields) + ('nama_kendaraan',)
    inlines        = [FotoTitipJualInline, DokumenTitipJualInline, RiwayatStatusInline, SerahTerimaInline]
    fieldsets = (
        ('Identitas Pemilik', {
            'fields': ('pengguna', 'nama_pemilik', 'no_whatsapp', 'email', 'alamat_pemilik')
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

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or request.user.has_perm('toko.view_titipjual')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FotoTitipJual)
class FotoTitipJualAdmin(admin.ModelAdmin):
    list_display = ('titip_jual', 'keterangan', 'urutan', 'created_at')
    list_filter  = ('created_at',)
    readonly_fields = ('titip_jual', 'gambar', 'jenis_foto', 'keterangan', 'urutan', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DokumenTitipJual)
class DokumenTitipJualAdmin(admin.ModelAdmin):
    list_display = ('titip_jual', 'jenis', 'diunggah_oleh', 'created_at')
    list_filter  = ('jenis',)
    search_fields = ('titip_jual__merek', 'titip_jual__model')
    fields = ('titip_jual', 'jenis', 'file_link', 'keterangan', 'diunggah_oleh', 'created_at')
    readonly_fields = fields

    @admin.display(description='File privat')
    def file_link(self, obj):
        if not obj or not obj.pk:
            return '—'
        return format_html(
            '<a href="{}">Unduh dokumen privat</a>',
            reverse('dokumen_titip_jual_download', args=[obj.pk]),
        )

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or request.user.has_perm('toko.view_titipjual')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RiwayatStatusTitipJual)
class RiwayatStatusTitipJualAdmin(admin.ModelAdmin):
    list_display   = ('titip_jual', 'status_dari', 'status_ke', 'diubah_oleh', 'created_at')
    list_filter    = ('status_ke',)
    readonly_fields = ('created_at',)
    can_delete     = False

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(SerahTerimaUnit)
class SerahTerimaUnitAdmin(admin.ModelAdmin):
    list_display   = ('titip_jual', 'jenis', 'tanggal_waktu', 'nama_penyerah', 'nama_penerima', 'kondisi_unit')
    list_filter    = ('jenis', 'kondisi_unit')
    search_fields  = ('nama_penyerah', 'nama_penerima')
    readonly_fields = [
        field.name for field in SerahTerimaUnit._meta.fields
        if field.name != 'foto_kondisi'
    ] + ['foto_kondisi_link']

    @admin.display(description='Foto kondisi privat')
    def foto_kondisi_link(self, obj):
        if not obj or not obj.pk or not obj.foto_kondisi:
            return '—'
        return format_html(
            '<a href="{}">Lihat foto privat</a>',
            reverse('serah_terima_foto_download', args=[obj.pk]),
        )

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or request.user.has_perm('toko.view_titipjual')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ChecklistInspeksiC2C)
class ChecklistInspeksiC2CAdmin(admin.ModelAdmin):
    list_display = ('urutan', 'kategori', 'nama', 'wajib', 'aktif')
    list_editable = ('wajib', 'aktif')
    list_filter = ('kategori', 'wajib', 'aktif')
    search_fields = ('kode', 'kategori', 'nama')
    ordering = ('urutan', 'pk')


@admin.register(InspeksiC2C)
class InspeksiC2CAdmin(admin.ModelAdmin):
    list_display = ('titip_jual', 'status', 'pemeriksa', 'tanggal_waktu')
    list_filter = ('status', 'tanggal_waktu')
    search_fields = ('titip_jual__merek', 'titip_jual__model')
    readonly_fields = [field.name for field in InspeksiC2C._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HasilChecklistInspeksiC2C)
class HasilChecklistInspeksiC2CAdmin(admin.ModelAdmin):
    list_display = ('inspeksi', 'poin', 'hasil')
    list_filter = ('hasil',)
    readonly_fields = [field.name for field in HasilChecklistInspeksiC2C._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
