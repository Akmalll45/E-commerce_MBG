"""
Penyimpanan foto di Cloudinary.

Dipakai sebagai storage bawaan hanya jika variabel lingkungan CLOUDINARY_URL ada
(lihat settings.py), misalnya di Vercel. Di laptop tanpa variabel itu, foto tetap
disimpan di folder media/ seperti sebelumnya.

Nama file yang tersimpan di database (mis. `mobil/3f2a...c9.jpg`) tidak berubah:
Cloudinary memakai nama itu tanpa ekstensi sebagai public_id, dan `foto.url`
menghasilkan alamat CDN Cloudinary. Karena itu model, form, dan template tidak
perlu diubah.
"""
import io
import logging
import os
import urllib.error
import urllib.request
import uuid

import cloudinary
import cloudinary.api
import cloudinary.exceptions
import cloudinary.uploader
import cloudinary.utils
from django.core.files.base import ContentFile
from django.core.files.storage import Storage

logger = logging.getLogger(__name__)

# Dijalankan Cloudinary saat unggah dan hasilnya yang disimpan: sisi terpanjang
# maksimal 1600 px dan kualitas otomatis, sehingga foto HP yang besar tidak boros kuota.
TRANSFORMASI_UNGGAH = [{'width': 1600, 'height': 1600, 'crop': 'limit', 'quality': 'auto'}]


def pisah_nama(name):
    """'mobil/abc.jpg' -> ('mobil/abc', 'jpg')."""
    public_id, ekstensi = os.path.splitext(str(name).replace('\\', '/'))
    return public_id, ekstensi.lstrip('.').lower()


def unggah(name, data):
    """Unggah (atau timpa) satu foto ke Cloudinary dengan nama yang sama seperti di database."""
    public_id, _ = pisah_nama(name)
    return cloudinary.uploader.upload(
        io.BytesIO(data),
        public_id=public_id,
        resource_type='image',
        overwrite=True,
        invalidate=True,
        unique_filename=False,
        use_filename=False,
        transformation=TRANSFORMASI_UNGGAH,
    )


class CloudinaryStorage(Storage):
    """Storage Django yang menyimpan ImageField di Cloudinary."""

    def get_available_name(self, name, max_length=None):
        # Setiap unggahan diberi nama acak (UUID), jadi tidak pernah bentrok dan
        # tidak perlu bertanya ke Cloudinary (hemat kuota API).
        name = str(name).replace('\\', '/')
        folder, nama_file = os.path.split(name)
        baru = uuid.uuid4().hex + os.path.splitext(nama_file)[1].lower()
        return f'{folder}/{baru}' if folder else baru

    def _save(self, name, content):
        name = str(name).replace('\\', '/')
        if hasattr(content, 'seek'):
            content.seek(0)
        data = content.read()
        if isinstance(data, str):
            data = data.encode('utf-8')
        unggah(name, data)
        return name

    def _open(self, name, mode='rb'):
        try:
            with urllib.request.urlopen(self.url(name), timeout=15) as respons:
                return ContentFile(respons.read(), name=name)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise FileNotFoundError(name) from e
            raise

    def exists(self, name):
        if not name:
            return False
        public_id, _ = pisah_nama(name)
        try:
            cloudinary.api.resource(public_id, resource_type='image')
            return True
        except cloudinary.exceptions.NotFound:
            return False

    def delete(self, name):
        if not name:
            return
        public_id, _ = pisah_nama(name)
        try:
            cloudinary.uploader.destroy(public_id, resource_type='image', invalidate=True)
        except Exception:
            # Gagal menghapus di Cloudinary tidak boleh membuat penghapusan data (mobil,
            # galeri) ikut error; file yatim di Cloudinary tidak berbahaya.
            logger.warning('Gagal menghapus %s dari Cloudinary', name, exc_info=True)

    def size(self, name):
        public_id, _ = pisah_nama(name)
        try:
            return cloudinary.api.resource(public_id, resource_type='image')['bytes']
        except cloudinary.exceptions.NotFound as e:
            raise FileNotFoundError(name) from e

    def url(self, name):
        public_id, ekstensi = pisah_nama(name)
        return cloudinary.utils.cloudinary_url(
            public_id, format=ekstensi or None, secure=True, quality='auto',
        )[0]
