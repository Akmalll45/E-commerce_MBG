import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from .models import DokumenTitipJual, FotoTitipJual, PermintaanTitipJual, Produk, TitipJual


def gambar_unggahan(nama='foto.jpg'):
    gambar = io.BytesIO()
    Image.new('RGB', (32, 24), color='blue').save(gambar, format='JPEG')
    return SimpleUploadedFile(nama, gambar.getvalue(), content_type='image/jpeg')


def data_pengajuan(**tambahan):
    data = {
        'nama_pemilik': 'Pemilik MBG',
        'no_whatsapp': '081234567890',
        'email': 'pemilik@example.test',
        'merek': 'Toyota',
        'model': 'Avanza G',
        'tahun': '2021',
        'transmisi': 'AT',
        'warna': 'Putih',
        'plat_nomor': 'DD 1234 XX',
        'jarak_tempuh': '42000',
        'harga_harapan': '210000000',
        'kondisi_eksterior': 'Kondisi baik, ada gores tipis di bumper.',
    }
    data.update(tambahan)
    return data


def foto_wajib():
    return {
        'foto_depan': gambar_unggahan('depan.jpg'),
        'foto_belakang': gambar_unggahan('belakang.jpg'),
        'foto_samping_kiri': gambar_unggahan('kiri.jpg'),
        'foto_samping_kanan': gambar_unggahan('kanan.jpg'),
        'foto_ruang_mesin': gambar_unggahan('mesin.jpg'),
    }


class TitipJualPenggunaTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.pemilik = user_model.objects.create_user(
            'pemilik', 'pemilik@example.test', 'Password123!',
            no_whatsapp='081234567890',
        )
        self.pengguna_lain = user_model.objects.create_user(
            'pengguna-lain', 'pemilik@example.test', 'Password123!',
            no_whatsapp='081234567890',
        )
        self.admin = user_model.objects.create_user(
            'staf-mbg', 'staf@example.test', 'Password123!', is_staff=True,
        )
        self.client.force_login(self.pemilik)

    def tearDown(self):
        for model in (FotoTitipJual, DokumenTitipJual):
            for item in model.objects.all():
                field = item.gambar if isinstance(item, FotoTitipJual) else item.file
                if field:
                    field.storage.delete(field.name)
        super().tearDown()

    def test_form_hanya_login_dan_mewajibkan_lima_foto(self):
        self.client.logout()
        url = reverse('titip_jual_tambah')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response['Location'])

        self.client.force_login(self.pemilik)
        response = self.client.post(url, data_pengajuan())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TitipJual.objects.exists())

        response = self.client.post(
            url, {**data_pengajuan(), **foto_wajib()},
        )
        self.assertEqual(response.status_code, 302)
        pengajuan = TitipJual.objects.get(pengguna=self.pemilik)
        self.assertEqual(pengajuan.status, 'pending')
        self.assertEqual(
            set(pengajuan.galeri_foto.values_list('jenis_foto', flat=True)),
            {'depan', 'belakang', 'samping_kiri', 'samping_kanan', 'ruang_mesin'},
        )
        self.assertTrue(pengajuan.riwayat_status.filter(status_ke='pending').exists())

    def test_foto_tambahan_dan_dokumen_privasi_akses_pengajuan_sendiri(self):
        post_data = {
            **data_pengajuan(),
            **foto_wajib(),
            'foto_tambahan': [gambar_unggahan('interior.jpg')],
            'dokumen_stnk': SimpleUploadedFile(
                'STNK.pdf', b'%PDF-1.4\nprivate test document', content_type='application/pdf',
            ),
        }
        self.client.post(reverse('titip_jual_tambah'), post_data)
        pengajuan = TitipJual.objects.get(pengguna=self.pemilik)
        self.assertEqual(pengajuan.galeri_foto.count(), 6)
        dokumen = pengajuan.dokumen.get(jenis='stnk')
        self.assertNotIn('media/', dokumen.file.name)

        url = reverse('dokumen_titip_jual_download', args=[dokumen.id])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.client.force_login(self.pengguna_lain)
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 302)

    def test_dashboard_hanya_memuat_pengajuan_dengan_foreign_key_pemilik(self):
        milik_pemilik = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG', no_whatsapp='081234567890',
            merek='Toyota', model='Avanza', tahun=2021, harga_harapan=200000000,
        )
        TitipJual.objects.create(
            pengguna=self.pengguna_lain, nama_pemilik='Pengguna Lain',
            no_whatsapp='081234567890', merek='Honda', model='Jazz', tahun=2020,
            harga_harapan=180000000,
        )

        response = self.client.get(reverse('dashboard_user'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['user_titip_jual'].values_list('id', flat=True)),
            [milik_pemilik.id],
        )
        self.assertEqual(
            self.client.get(reverse('titip_jual_detail', args=[milik_pemilik.id + 1])).status_code,
            404,
        )

    def test_pemilik_menyetujui_harga_dan_admin_menerbitkan_ke_produk(self):
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG', no_whatsapp='081234567890',
            merek='Toyota', model='Avanza', tahun=2021, harga_harapan=200000000,
            harga_rekomendasi=Decimal('195000000'), status='negosiasi',
            status_persetujuan_harga='menunggu',
        )
        for jenis in ('depan', 'belakang', 'samping_kiri', 'samping_kanan', 'ruang_mesin'):
            FotoTitipJual.objects.create(
                titip_jual=pengajuan, jenis_foto=jenis, gambar=gambar_unggahan(),
            )

        response = self.client.post(
            reverse('titip_jual_tindakan', args=[pengajuan.id]),
            {'aksi': 'setujui_harga'},
        )
        self.assertEqual(response.status_code, 302)
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'disetujui')
        self.assertEqual(pengajuan.harga_kesepakatan, Decimal('195000000'))

        self.client.force_login(self.admin)
        self.client.post(
            reverse('admin_titip_jual_tindakan', args=[pengajuan.id]),
            {'aksi': 'status', 'status': 'tayang'},
        )
        pengajuan.refresh_from_db()
        self.assertIsNotNone(pengajuan.produk_terkait_id)
        self.assertEqual(pengajuan.produk_terkait.tipe_penjual, 'C2C')
        self.assertEqual(
            Produk.objects.filter(titip_jual_sumber=pengajuan, status_stok='ready').count(),
            1,
        )

    def test_perubahan_harga_dan_pembatalan_diproses_admin(self):
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG', no_whatsapp='081234567890',
            merek='Toyota', model='Avanza', tahun=2021, harga_harapan=200000000,
            harga_rekomendasi=Decimal('195000000'), status='negosiasi',
            status_persetujuan_harga='menunggu',
        )
        self.client.post(
            reverse('titip_jual_tindakan', args=[pengajuan.id]),
            {'aksi': 'ubah_harga', 'harga_diminta': '205000000', 'catatan': 'Pertimbangan kondisi unit.'},
        )
        permintaan_harga = PermintaanTitipJual.objects.get(
            titip_jual=pengajuan, jenis='ubah_harga',
        )

        self.client.force_login(self.admin)
        self.client.post(
            reverse('admin_titip_jual_tindakan', args=[pengajuan.id]),
            {'aksi': 'proses_permintaan', 'permintaan_id': permintaan_harga.id, 'keputusan': 'setujui'},
        )
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.harga_rekomendasi, Decimal('205000000'))

        self.client.force_login(self.pemilik)
        self.client.post(
            reverse('titip_jual_tindakan', args=[pengajuan.id]),
            {'aksi': 'pembatalan', 'catatan': 'Berubah rencana.'},
        )
        permintaan_batal = PermintaanTitipJual.objects.get(
            titip_jual=pengajuan, jenis='pembatalan',
        )
        self.client.force_login(self.admin)
        self.client.post(
            reverse('admin_titip_jual_tindakan', args=[pengajuan.id]),
            {'aksi': 'proses_permintaan', 'permintaan_id': permintaan_batal.id, 'keputusan': 'setujui'},
        )
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'dikembalikan')
        self.assertTrue(
            pengajuan.riwayat_status.filter(status_ke='dikembalikan').exists()
        )

    def test_menu_admin_c2c_memiliki_enam_tahap_dan_terbatas_staf(self):
        self.client.force_login(self.admin)
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG', no_whatsapp='081234567890',
            merek='Toyota', model='Avanza', tahun=2021, harga_harapan=200000000,
        )
        for tahap in ('verifikasi', 'inspeksi', 'harga', 'showroom', 'penjualan'):
            response = self.client.get(
                reverse('admin_titip_jual_tahap', args=[tahap]),
            )
            self.assertEqual(response.status_code, 200)
        response = self.client.get(reverse('admin_titip_jual'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.client.get(reverse('admin_titip_jual_detail', args=[pengajuan.pk])).status_code,
            200,
        )
        for label in (
            'Semua Pengajuan', 'Verifikasi &amp; Revisi', 'Jadwal Inspeksi',
            'Persetujuan Harga', 'Kendaraan di Showroom', 'Penjualan &amp; Penyelesaian',
        ):
            self.assertContains(response, label)

        self.client.force_login(self.pemilik)
        self.assertEqual(self.client.get(reverse('admin_titip_jual')).status_code, 302)

    def test_jadwal_inspeksi_hasil_dan_transisi_status_dicatat(self):
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG', no_whatsapp='081234567890',
            merek='Toyota', model='Avanza', tahun=2021, harga_harapan=200000000,
            status='pending',
        )
        self.client.force_login(self.admin)
        url = reverse('admin_titip_jual_tindakan', args=[pengajuan.pk])
        self.client.post(url, {
            'aksi': 'jadwalkan_inspeksi',
            'jadwal_inspeksi': '2030-01-15T10:00',
        })
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'dijadwalkan')
        self.assertTrue(pengajuan.tanggal_inspeksi_dijadwalkan)

        self.client.post(url, {'aksi': 'mulai_inspeksi'})
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'inspeksi')

        self.client.post(url, {
            'aksi': 'hasil_inspeksi',
            'hasil_inspeksi': 'Kondisi mesin baik dan lulus pemeriksaan.',
            'harga_rekomendasi': '195000000',
        })
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'negosiasi')
        self.assertEqual(pengajuan.harga_rekomendasi, Decimal('195000000'))
        self.assertTrue(
            pengajuan.riwayat_status.filter(status_ke='negosiasi').exists()
        )
