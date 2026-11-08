from django.db import DatabaseError

from .models import HalamanWebsite, PengaturanWebsite

JUMLAH_MAKS_TAUTAN_FOOTER = 6


def halaman_footer(request):
    """
    Menyediakan `halaman_footer` ke semua template: daftar halaman informasi
    (Syarat & Ketentuan, Kebijakan Privasi, dll.) yang diterbitkan dan ditandai
    "Tampilkan di footer", terurut menurut kolom Urutan.

    Sengaja tahan galat: jika tabel belum dimigrasi (misalnya di database cloud),
    footer jatuh ke tautan bawaan dan situs publik tidak ikut error 500.
    """
    try:
        halaman = list(
            HalamanWebsite.objects
            .filter(status='published', tampil_di_footer=True)
            .order_by('urutan', 'judul')
            .only('judul', 'slug')[:JUMLAH_MAKS_TAUTAN_FOOTER]
        )
    except DatabaseError:
        halaman = []
    return {'halaman_footer': halaman}


def pengaturan_situs(request):
    """
    Menyediakan `pengaturan` (informasi global situs) ke semua template.

    Tahan galat seperti `halaman_footer`: jika tabel belum dimigrasi, dipakai nilai bawaan
    (sama dengan teks lama di template) sehingga situs publik tidak ikut error 500.
    """
    try:
        obj = PengaturanWebsite.objects.filter(pk=1).first()
    except DatabaseError:
        obj = None
    return {'pengaturan': obj or PengaturanWebsite()}
