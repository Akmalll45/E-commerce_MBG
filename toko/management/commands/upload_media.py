import os
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError

from toko.models import FotoMobil, Produk, TitipJual

EKSTENSI_GAMBAR = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


class Command(BaseCommand):
    help = (
        "Unggah semua foto di folder media/ ke Cloudinary dengan nama yang sama seperti "
        "di database, lalu tampilkan foto di data yang masih belum ada di Cloudinary. "
        "Butuh variabel lingkungan CLOUDINARY_URL."
    )

    def add_arguments(self, parser):
        parser.add_argument('--folder', help='Folder sumber foto (bawaan: MEDIA_ROOT, yaitu folder media/).')
        parser.add_argument('--simulasi', action='store_true', help='Hanya tampilkan yang akan diunggah.')

    def handle(self, *args, **opsi):
        if not os.environ.get('CLOUDINARY_URL'):
            raise CommandError(
                "Variabel CLOUDINARY_URL belum diisi. Contoh (PowerShell): "
                "$env:CLOUDINARY_URL=\"cloudinary://API_KEY:API_SECRET@CLOUD_NAME\""
            )
        # Impor di sini agar perintah lain tidak butuh paket cloudinary.
        from katalog_mbg.storage import unggah

        folder = Path(opsi['folder'] or settings.MEDIA_ROOT)
        if not folder.is_dir():
            raise CommandError(f"Folder tidak ditemukan: {folder}")

        berhasil, gagal, diunggah = 0, [], set()
        for jalur in sorted(folder.rglob('*')):
            if not jalur.is_file() or jalur.suffix.lower() not in EKSTENSI_GAMBAR:
                continue
            nama = jalur.relative_to(folder).as_posix()
            if opsi['simulasi']:
                self.stdout.write(f"  (simulasi) {nama}")
                continue
            try:
                unggah(nama, jalur.read_bytes())
            except Exception as e:  # satu foto gagal tidak menghentikan yang lain
                gagal.append(f"{nama}: {e}")
                continue
            diunggah.add(nama)
            berhasil += 1
            self.stdout.write(f"  + {nama}")

        if opsi['simulasi']:
            return
        self.stdout.write(self.style.SUCCESS(f"{berhasil} foto diunggah ke Cloudinary."))
        for baris in gagal:
            self.stdout.write(self.style.ERROR(f"  GAGAL {baris}"))
        self._periksa_foto_hilang(diunggah)

    def _periksa_foto_hilang(self, diunggah):
        """Cari data yang merujuk ke foto yang tidak ada di Cloudinary."""
        sumber = [
            (Produk, 'gambar_utama'),
            (FotoMobil, 'gambar'),
            (TitipJual, 'foto_kendaraan'),
            (get_user_model(), 'foto_profil'),
        ]
        hilang = []
        for model, kolom in sumber:
            for pk, nama in model.objects.exclude(**{kolom: ''}).values_list('pk', kolom):
                if nama and nama not in diunggah and not default_storage.exists(nama):
                    hilang.append(f"{model.__name__} #{pk} ({kolom}): {nama}")

        if not hilang:
            self.stdout.write(self.style.SUCCESS("Semua foto yang dipakai data sudah ada di Cloudinary."))
            return
        self.stdout.write(self.style.WARNING(
            f"{len(hilang)} foto dipakai data tetapi tidak ada di Cloudinary "
            "(unggah ulang lewat panel admin):"
        ))
        for baris in hilang:
            self.stdout.write(f"  ! {baris}")
