import io
from importlib import import_module
from decimal import Decimal
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from .forms import HasilInspeksiC2CForm, validasi_dokumen_titip_jual
from .models import (
    ChecklistInspeksiC2C, DokumenTitipJual, FotoTitipJual, InquiryC2C,
    HasilChecklistInspeksiC2C, InspeksiC2C, PermintaanTitipJual,
    Produk, RiwayatTransaksiTitipJual, SerahTerimaUnit,
    TitipJual, TransaksiTitipJual, RINGKASAN_INSPEKSI_DEFAULT_LEGACY,
)
from .views import _periksa_izin_tindakan_c2c, produk_tayang


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


class TitipJualPenggunaTests(TransactionTestCase):
    def setUp(self):
        role_migration = import_module(
            'toko.migrations.0015_c2c_permissions_and_inspection_default'
        )
        role_migration.create_c2c_roles(
            apps,
            SimpleNamespace(connection=connection),
        )
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
        self.admin.groups.add(Group.objects.get(name='Admin C2C'))
        self.checklist = ChecklistInspeksiC2C.objects.create(
            kode='test-item', kategori='Pengujian', nama='Poin pemeriksaan uji',
            urutan=1,
        )
        self.client.force_login(self.pemilik)

    def tearDown(self):
        for model in (FotoTitipJual, DokumenTitipJual, SerahTerimaUnit):
            for item in model.objects.all():
                field = (
                    item.gambar if isinstance(item, FotoTitipJual)
                    else item.file if isinstance(item, DokumenTitipJual)
                    else item.foto_kondisi
                )
                if field:
                    field.storage.delete(field.name)
        super().tearDown()

    def _tambahkan_foto_wajib(self, pengajuan):
        foto_utama = None
        for jenis in ('depan', 'belakang', 'samping_kiri', 'samping_kanan', 'ruang_mesin'):
            foto = FotoTitipJual.objects.create(
                titip_jual=pengajuan,
                jenis_foto=jenis,
                gambar=gambar_unggahan(f'{jenis}.jpg'),
            )
            if foto_utama is None:
                foto_utama = foto
        return foto_utama

    def _tambahkan_serah_terima_masuk(self, pengajuan):
        return SerahTerimaUnit.objects.create(
            titip_jual=pengajuan,
            jenis='masuk',
            tanggal_waktu='2030-01-15T10:00:00Z',
            lokasi='Showroom uji',
            nama_penyerah='Pemilik Uji',
            nama_penerima='Petugas Uji',
            kondisi_unit='baik',
            odometer_saat_ini=42000,
            jumlah_kunci=2,
            kelengkapan='STNK, BPKB',
            kelengkapan_dokumen=['stnk', 'bpkb'],
            foto_kondisi=gambar_unggahan('kondisi-masuk.jpg'),
            dicatat_oleh=self.admin,
        )

    def _buat_pengajuan_tayang(self, pemilik=None):
        pengajuan = TitipJual.objects.create(
            pengguna=pemilik or self.pemilik,
            nama_pemilik='Pemilik MBG',
            no_whatsapp='081234567890',
            merek='Toyota',
            model='Avanza',
            tahun=2021,
            harga_harapan=Decimal('210000000'),
            harga_kesepakatan=Decimal('200000000'),
            harga_jual_minimum=Decimal('190000000'),
            status='tayang',
            status_persetujuan_harga='disetujui',
        )
        foto_utama = self._tambahkan_foto_wajib(pengajuan)
        inspeksi = InspeksiC2C.objects.create(
            titip_jual=pengajuan,
            status='lulus',
            pemeriksa=self.admin,
            harga_rekomendasi=Decimal('200000000'),
        )
        HasilChecklistInspeksiC2C.objects.create(
            inspeksi=inspeksi, poin=self.checklist, hasil='lulus',
        )
        self._tambahkan_serah_terima_masuk(pengajuan)
        produk = Produk.objects.create(
            sku=f'C2C-TEST-{pengajuan.pk}',
            nama='Toyota Avanza 2021',
            merek='Toyota',
            kategori='MPV',
            tahun=2021,
            jarak_tempuh=42000,
            warna='Putih',
            plat_nomor='DD 1234 XX',
            harga=Decimal('200000000'),
            tipe_penjual='C2C',
            status_stok='ready',
            lulus_inspeksi=True,
            gambar_utama=foto_utama.gambar.name,
            deskripsi_singkat='Unit uji.',
            deskripsi_detail='Unit uji untuk transaksi C2C.',
            titip_jual_sumber=pengajuan,
        )
        pengajuan.produk_terkait = produk
        pengajuan.save(update_fields=['produk_terkait'])
        return pengajuan

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
        self.assertNotIn('STNK', dokumen.file.name)
        self.assertNotIn('STNK.pdf', dokumen.nama_file)

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

    def test_inquiry_disimpan_sebelum_redirect_whatsapp(self):
        pengajuan = self._buat_pengajuan_tayang()
        response = self.client.post(
            reverse('kirim_inquiry_c2c', args=[pengajuan.produk_terkait_id]),
            {
                'nama_pembeli': '  Calon   Pembeli ',
                'whatsapp': '0812-3456-7890',
                'email': 'buyer@example.test',
                'pesan': 'Apakah kilometer kendaraan masih sama?',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn('https://wa.me/6281234567890?', response['Location'])
        inquiry = InquiryC2C.objects.get()
        self.assertEqual(inquiry.produk_id, pengajuan.produk_terkait_id)
        self.assertEqual(inquiry.titip_jual_id, pengajuan.pk)
        self.assertEqual(inquiry.pengguna_id, self.pemilik.pk)
        self.assertEqual(inquiry.nama_pembeli, 'Calon Pembeli')
        self.assertEqual(inquiry.whatsapp, '6281234567890')
        self.assertEqual(inquiry.status, 'baru')

    def test_inquiry_duplikat_24_jam_tidak_membuat_record_baru(self):
        pengajuan = self._buat_pengajuan_tayang()
        url = reverse('kirim_inquiry_c2c', args=[pengajuan.produk_terkait_id])
        data = {
            'nama_pembeli': 'Calon Pembeli',
            'whatsapp': '081234567890',
            'email': '',
            'pesan': 'Masih tersedia?',
        }

        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(InquiryC2C.objects.count(), 1)

    def test_listing_tidak_layak_tidak_menerima_inquiry(self):
        pengajuan = self._buat_pengajuan_tayang()
        produk = pengajuan.produk_terkait
        produk.status_stok = 'in_review'
        produk.save(update_fields=['status_stok'])

        response = self.client.post(
            reverse('kirim_inquiry_c2c', args=[produk.pk]),
            {
                'nama_pembeli': 'Calon Pembeli',
                'whatsapp': '081234567890',
                'email': '',
                'pesan': '',
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(InquiryC2C.objects.exists())

    def test_listing_dengan_transaksi_aktif_tidak_menerima_inquiry(self):
        pengajuan = self._buat_pengajuan_tayang()
        TransaksiTitipJual.objects.create(
            titip_jual=pengajuan,
            sumber='pemilik',
            nama_pembeli='Pembeli Lain',
            kontak_pembeli='628123456703',
            harga_final=Decimal('200000000'),
            tanggal_kesepakatan=timezone.localdate(),
            dilaporkan_oleh=self.pemilik,
            status='terverifikasi',
        )

        response = self.client.post(
            reverse('kirim_inquiry_c2c', args=[pengajuan.produk_terkait_id]),
            {
                'nama_pembeli': 'Calon Pembeli',
                'whatsapp': '081234567890',
                'email': '',
                'pesan': '',
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(InquiryC2C.objects.exists())

    def test_form_inquiry_menolak_nomor_whatsapp_tidak_valid(self):
        pengajuan = self._buat_pengajuan_tayang()
        response = self.client.post(
            reverse('kirim_inquiry_c2c', args=[pengajuan.produk_terkait_id]),
            {
                'nama_pembeli': 'Calon Pembeli',
                'whatsapp': 'nomor salah',
                'email': '',
                'pesan': '',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(InquiryC2C.objects.exists())
        self.assertIn('whatsapp', response.context['inquiry_form'].errors)

    def test_dashboard_pemilik_hanya_memuat_inquiry_listing_sendiri(self):
        milik_pemilik = self._buat_pengajuan_tayang()
        milik_pengguna_lain = self._buat_pengajuan_tayang(pemilik=self.pengguna_lain)
        inquiry_pemilik = InquiryC2C.objects.create(
            produk=milik_pemilik.produk_terkait,
            titip_jual=milik_pemilik,
            nama_pembeli='Buyer Satu',
            whatsapp='628123456700',
            catatan_admin='Catatan internal rahasia',
        )
        InquiryC2C.objects.create(
            produk=milik_pengguna_lain.produk_terkait,
            titip_jual=milik_pengguna_lain,
            nama_pembeli='Buyer Dua',
            whatsapp='628123456701',
        )

        response = self.client.get(reverse('dashboard_user'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context['user_inquiry_c2c'].values_list('pk', flat=True)),
            [inquiry_pemilik.pk],
        )
        self.assertNotContains(response, 'Catatan internal rahasia')

    def test_admin_c2c_dapat_mencari_dan_memperbarui_inquiry(self):
        pengajuan = self._buat_pengajuan_tayang()
        inquiry = InquiryC2C.objects.create(
            produk=pengajuan.produk_terkait,
            titip_jual=pengajuan,
            nama_pembeli='Calon Pembeli Admin',
            whatsapp='628123456702',
        )
        self.client.force_login(self.admin)

        response = self.client.get(
            reverse('admin_inquiry_c2c'),
            {'q': 'Calon Pembeli Admin', 'status': 'baru'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Calon Pembeli Admin')

        response = self.client.post(
            reverse('admin_inquiry_c2c_status', args=[inquiry.pk]),
            {'status': 'dihubungi', 'catatan_admin': 'Sudah diteruskan ke pemilik.'},
        )
        self.assertEqual(response.status_code, 302)
        inquiry.refresh_from_db()
        self.assertEqual(inquiry.status, 'dihubungi')
        self.assertEqual(inquiry.catatan_admin, 'Sudah diteruskan ke pemilik.')
        self.assertEqual(inquiry.ditangani_oleh_id, self.admin.pk)

    def test_nomor_ktp_terenkripsi_di_database_dan_membutuhkan_kunci(self):
        nomor_ktp = '1234567890123456'
        key = Fernet.generate_key().decode('ascii')
        with override_settings(TITIP_JUAL_KTP_ENCRYPTION_KEY=key):
            pengajuan = TitipJual.objects.create(
                pengguna=self.pemilik,
                nama_pemilik='Pemilik MBG',
                no_whatsapp='081234567890',
                no_ktp=nomor_ktp,
                merek='Toyota',
                model='Avanza',
                tahun=2021,
                harga_harapan=Decimal('200000000'),
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    'SELECT no_ktp FROM toko_titipjual WHERE id = %s',
                    [pengajuan.pk],
                )
                ciphertext = cursor.fetchone()[0]
            self.assertNotEqual(ciphertext, nomor_ktp)
            self.assertEqual(
                Fernet(key.encode('ascii')).decrypt(ciphertext.encode('ascii')).decode('utf-8'),
                nomor_ktp,
            )
            pengajuan.refresh_from_db()
            self.assertEqual(pengajuan.no_ktp, nomor_ktp)

        with override_settings(TITIP_JUAL_KTP_ENCRYPTION_KEY=''):
            with self.assertRaises(ImproperlyConfigured):
                TitipJual.objects.create(
                    pengguna=self.pemilik,
                    nama_pemilik='Pemilik MBG',
                    no_whatsapp='081234567890',
                    no_ktp='1234567890123456',
                    merek='Toyota',
                    model='Avanza',
                    tahun=2021,
                    harga_harapan=Decimal('200000000'),
                )

    def test_migrasi_ktp_dapat_mengenkripsi_dan_membalikkan_data(self):
        migration = import_module('toko.migrations.0014_encrypt_titipjual_ktp')
        table_name = 'c2c_ktp_migration_probe'
        quoted_table = connection.ops.quote_name(table_name)
        quoted_id = connection.ops.quote_name('id')
        quoted_ktp = connection.ops.quote_name('no_ktp')

        class FakeMeta:
            db_table = table_name
            pk = SimpleNamespace(column='id')

            @staticmethod
            def get_field(name):
                return SimpleNamespace(column=name)

        class FakeApps:
            @staticmethod
            def get_model(app_label, model_name):
                return SimpleNamespace(_meta=FakeMeta)

        schema_editor = SimpleNamespace(connection=connection)
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE {quoted_table} '
                f'({quoted_id} INTEGER PRIMARY KEY, {quoted_ktp} VARCHAR(255) NULL)'
            )
            cursor.execute(
                f'INSERT INTO {quoted_table} ({quoted_id}, {quoted_ktp}) VALUES (%s, %s)',
                [1, '1234567890123456'],
            )

        key = Fernet.generate_key().decode('ascii')
        try:
            with override_settings(TITIP_JUAL_KTP_ENCRYPTION_KEY=key):
                migration.encrypt_legacy_ktp(FakeApps, schema_editor)
                with connection.cursor() as cursor:
                    cursor.execute(f'SELECT {quoted_ktp} FROM {quoted_table} WHERE {quoted_id} = %s', [1])
                    ciphertext = cursor.fetchone()[0]
                self.assertEqual(
                    Fernet(key.encode('ascii')).decrypt(ciphertext.encode('ascii')).decode('utf-8'),
                    '1234567890123456',
                )

                with self.assertRaisesMessage(RuntimeError, 'would restore plaintext'):
                    migration.decrypt_ktp_for_rollback(FakeApps, schema_editor)
                with override_settings(C2C_KTP_ALLOW_PLAINTEXT_ROLLBACK=True):
                    migration.decrypt_ktp_for_rollback(FakeApps, schema_editor)
                with connection.cursor() as cursor:
                    cursor.execute(f'SELECT {quoted_ktp} FROM {quoted_table} WHERE {quoted_id} = %s', [1])
                    self.assertEqual(cursor.fetchone()[0], '1234567890123456')
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE {quoted_table}')

    def test_pemilik_menyetujui_harga_dan_admin_menerbitkan_ke_produk(self):
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG', no_whatsapp='081234567890',
            merek='Toyota', model='Avanza', tahun=2021, harga_harapan=200000000,
            harga_rekomendasi=Decimal('195000000'), status='negosiasi',
            status_persetujuan_harga='menunggu',
        )
        self._tambahkan_foto_wajib(pengajuan)
        InspeksiC2C.objects.create(
            titip_jual=pengajuan, status='lulus', pemeriksa=self.admin,
            harga_rekomendasi=Decimal('195000000'),
        )

        response = self.client.post(
            reverse('titip_jual_tindakan', args=[pengajuan.id]),
            {'aksi': 'setujui_harga'},
        )
        self.assertEqual(response.status_code, 302)
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'disetujui')
        self.assertEqual(pengajuan.harga_kesepakatan, Decimal('195000000'))
        self._tambahkan_serah_terima_masuk(pengajuan)

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
        self.assertTrue(pengajuan.produk_terkait.lulus_inspeksi)
        self.assertEqual(
            pengajuan.produk_terkait.ringkasan_inspeksi,
            'Checklist inspeksi lulus.',
        )

    def test_default_lulus_inspeksi_tidak_lulus_sebelum_diperiksa(self):
        self.assertFalse(Produk._meta.get_field('lulus_inspeksi').default)
        self.assertNotIn('150', Produk._meta.get_field('lulus_inspeksi').help_text)
        self.assertEqual(Produk._meta.get_field('ringkasan_inspeksi').default, '')
        self.assertTrue(Produk._meta.get_field('ringkasan_inspeksi').blank)
        self.assertFalse(Produk(
            lulus_inspeksi=True,
            ringkasan_inspeksi=RINGKASAN_INSPEKSI_DEFAULT_LEGACY,
        ).has_inspection_result)

    def test_ringkasan_inspeksi_baru_dan_lama_tidak_mengklaim_hasil(self):
        produk = Produk(
            sku='B2C-LEGACY-SUMMARY',
            nama='Kendaraan uji',
            merek='Toyota',
            kategori='MPV',
            tahun=2021,
            jarak_tempuh=42000,
            warna='Putih',
            plat_nomor='DD 4321 XX',
            harga=Decimal('200000000'),
            tipe_penjual='B2C',
            status_stok='ready',
            lulus_inspeksi=True,
            ringkasan_inspeksi=RINGKASAN_INSPEKSI_DEFAULT_LEGACY,
            gambar_utama='mobil/legacy-summary.jpg',
            deskripsi_singkat='Unit uji.',
            deskripsi_detail='Unit uji.',
        )
        produk.save()

        response = self.client.get(reverse('detail_produk', args=[produk.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, RINGKASAN_INSPEKSI_DEFAULT_LEGACY)
        self.assertNotContains(response, 'Bebas Terendam Banjir')
        self.assertNotContains(response, 'Lulus 150 Titik')

    def test_migrasi_0015_dependency_dan_urutan_tidak_mengubah_produk_lama(self):
        migration = import_module(
            'toko.migrations.0015_c2c_permissions_and_inspection_default'
        )
        self.assertIn(
            ('toko', '0014_encrypt_titipjual_ktp'),
            migration.Migration.dependencies,
        )
        self.assertIn(
            ('auth', '0012_alter_user_first_name_max_length'),
            migration.Migration.dependencies,
        )
        self.assertIn(
            ('contenttypes', '0002_remove_content_type_name'),
            migration.Migration.dependencies,
        )
        operations = migration.Migration.operations
        self.assertEqual(len(operations), 4)
        self.assertEqual(operations[0].model_name, 'produk')
        self.assertEqual(operations[0].name, 'lulus_inspeksi')
        self.assertFalse(operations[0].field.default)
        self.assertNotIn('150', operations[0].field.help_text)
        self.assertEqual(operations[1].model_name, 'produk')
        self.assertEqual(operations[1].name, 'ringkasan_inspeksi')
        self.assertEqual(operations[1].field.default, '')
        self.assertTrue(operations[1].field.blank)
        self.assertEqual(operations[2].name, 'titipjual')
        run_python_operations = [
            operation for operation in operations
            if operation.__class__.__name__ == 'RunPython'
        ]
        self.assertEqual(len(run_python_operations), 1)
        self.assertIs(run_python_operations[0].code, migration.create_c2c_roles)

    def test_dashboard_oculta_dados_c2c_de_staf_sem_permissao(self):
        TitipJual.objects.create(
            pengguna=self.pemilik,
            nama_pemilik='Pemilik Rahasia',
            no_whatsapp='081298765432',
            merek='Toyota',
            model='Avanza',
            tahun=2021,
            harga_harapan=Decimal('200000000'),
        )
        staf_tanpa_izin_c2c = get_user_model().objects.create_user(
            'staf-tanpa-izin-c2c', 'staf-lain@example.test', 'Password123!', is_staff=True,
        )
        self.client.force_login(staf_tanpa_izin_c2c)

        response = self.client.get(reverse('dashboard_admin'))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_view_c2c'])
        self.assertIsNone(response.context['total_titip_jual'])
        self.assertFalse(response.context['recent_titip_jual'])
        self.assertNotContains(response, 'Pemilik Rahasia')
        self.assertNotContains(response, '081298765432')
        self.assertNotContains(response, 'Titip-Jual C2C')

    def test_produk_c2c_tanpa_inspeksi_sah_tidak_ditayangkan_ke_publik(self):
        produk = Produk.objects.create(
            sku='C2C-TEST-TANPA-INSPEKSI-PUBLIK',
            nama='Kendaraan tanpa inspeksi',
            merek='Toyota',
            kategori='MPV',
            tahun=2021,
            jarak_tempuh=42000,
            warna='Putih',
            plat_nomor='DD 4321 XX',
            harga=Decimal('200000000'),
            tipe_penjual='C2C',
            status_stok='ready',
            lulus_inspeksi=True,
            gambar_utama='mobil/without-inspection-public.jpg',
            deskripsi_singkat='Unit uji.',
            deskripsi_detail='Unit tanpa inspeksi sah.',
        )
        self.assertNotIn(produk, produk_tayang())

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
        detail_response = self.client.get(reverse('admin_titip_jual_detail', args=[pengajuan.pk]))
        self.assertContains(detail_response, '<option value="perjanjian">', html=False)
        self.assertContains(detail_response, '<option value="hasil_inspeksi">', html=False)
        self.assertNotContains(detail_response, '<option value="stnk">', html=False)
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
        self._tambahkan_foto_wajib(pengajuan)
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

        hasil_inspeksi = {
            'aksi': 'hasil_inspeksi',
            'catatan': 'Kondisi mesin baik dan lulus pemeriksaan.',
            'harga_rekomendasi': '195000000',
        }
        for poin in ChecklistInspeksiC2C.objects.filter(aktif=True):
            hasil_inspeksi[f'poin_{poin.pk}'] = 'lulus'
            hasil_inspeksi[f'catatan_{poin.pk}'] = 'Diperiksa.'
        response = self.client.post(url, hasil_inspeksi)
        self.assertEqual(response.status_code, 302)
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'negosiasi')
        self.assertEqual(pengajuan.harga_rekomendasi, Decimal('195000000'))
        self.assertEqual(pengajuan.inspeksi_c2c.get().status, 'lulus')
        self.assertTrue(
            pengajuan.riwayat_status.filter(status_ke='negosiasi').exists()
        )

    def test_inspeksi_tidak_lulus_jika_semua_poin_ditandai_opsional(self):
        self.checklist.wajib = False
        self.checklist.save(update_fields=['wajib'])
        form = HasilInspeksiC2CForm(
            data={
                f'poin_{self.checklist.pk}': 'gagal',
                'harga_rekomendasi': '195000000',
            },
            poin_inspeksi=[self.checklist],
        )
        self.assertFalse(form.is_valid())
        self.assertIn('Minimal satu poin checklist', str(form.non_field_errors()))
        self.assertFalse(form.lulus)

    def test_produk_tayang_fallback_ke_b2c_jika_tabel_c2c_belum_ada(self):
        produk = Produk.objects.create(
            sku='B2C-FALLBACK-TEST',
            nama='Toyota Avanza Uji',
            merek='Toyota',
            kategori='MPV',
            tahun=2021,
            jarak_tempuh=42000,
            warna='Putih',
            plat_nomor='DD 1234 XX',
            harga=Decimal('200000000'),
            tipe_penjual='B2C',
            status_stok='ready',
            gambar_utama='mobil/fallback-test.jpg',
            deskripsi_singkat='Unit uji.',
            deskripsi_detail='Unit uji fallback katalog.',
        )
        with patch('toko.views.connection.introspection.table_names', return_value=[]):
            self.assertEqual(list(produk_tayang()), [produk])

    def test_publikasi_memerlukan_inspeksi_terakhir_lulus_dan_serah_terima(self):
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik,
            nama_pemilik='Pemilik MBG',
            no_whatsapp='081234567890',
            merek='Toyota',
            model='Avanza',
            tahun=2021,
            harga_harapan=Decimal('210000000'),
            harga_kesepakatan=Decimal('200000000'),
            status='disetujui',
            status_persetujuan_harga='disetujui',
        )
        self._tambahkan_foto_wajib(pengajuan)
        with self.assertRaisesMessage(ValueError, 'Inspeksi terakhir wajib'):
            pengajuan.lakukan_transisi('tayang', oleh_user=self.admin)

        inspeksi = InspeksiC2C.objects.create(
            titip_jual=pengajuan,
            status='lulus',
            pemeriksa=self.admin,
        )
        with self.assertRaisesMessage(ValueError, 'Serah-terima masuk'):
            pengajuan.lakukan_transisi('tayang', oleh_user=self.admin)

        self._tambahkan_serah_terima_masuk(pengajuan)
        self.client.force_login(self.admin)
        self.client.post(
            reverse('admin_titip_jual_tindakan', args=[pengajuan.pk]),
            {'aksi': 'status', 'status': 'tayang'},
        )
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.status, 'tayang')
        self.assertIsNotNone(pengajuan.produk_terkait_id)
        self.assertTrue(pengajuan.riwayat_status.filter(status_ke='tayang').exists())

        InspeksiC2C.objects.create(
            titip_jual=pengajuan,
            status='gagal',
            pemeriksa=self.admin,
            tanggal_waktu=inspeksi.tanggal_waktu + timedelta(seconds=1),
        )
        self.assertEqual(
            self.client.get(reverse('detail_produk', args=[pengajuan.produk_terkait_id])).status_code,
            404,
        )

    def test_laporan_pembeli_pemilik_menahan_stok_dan_mematuhi_harga_minimum(self):
        pengajuan = self._buat_pengajuan_tayang()
        url = reverse('titip_jual_tindakan', args=[pengajuan.pk])
        payload = {
            'aksi': 'lapor_penjualan_pemilik',
            'nama_pembeli': 'Pembeli Uji',
            'kontak_pembeli': '081234567891',
            'harga_final': '180000000',
            'tanggal_kesepakatan': '2030-01-15',
        }
        self.client.post(url, payload)
        self.assertFalse(TransaksiTitipJual.objects.filter(titip_jual=pengajuan).exists())
        pengajuan.produk_terkait.refresh_from_db()
        self.assertEqual(pengajuan.produk_terkait.status_stok, 'ready')

        payload['harga_final'] = '200000000'
        self.client.post(url, payload)
        transaksi = TransaksiTitipJual.objects.get(titip_jual=pengajuan)
        self.assertEqual(transaksi.status, 'menunggu_verifikasi')
        self.assertTrue(
            RiwayatTransaksiTitipJual.objects.filter(
                transaksi=transaksi, status_ke='menunggu_verifikasi',
            ).exists()
        )
        pengajuan.produk_terkait.refresh_from_db()
        self.assertEqual(pengajuan.produk_terkait.status_stok, 'in_review')

        self.client.force_login(self.pengguna_lain)
        self.assertEqual(
            self.client.get(reverse('titip_jual_detail', args=[pengajuan.pk])).status_code,
            404,
        )

    def test_penjualan_pemilik_harus_diverifikasi_serah_terima_dan_diselesaikan(self):
        pengajuan = self._buat_pengajuan_tayang()
        self.client.post(
            reverse('titip_jual_tindakan', args=[pengajuan.pk]),
            {
                'aksi': 'lapor_penjualan_pemilik',
                'nama_pembeli': 'Pembeli Uji',
                'kontak_pembeli': '081234567891',
                'harga_final': '200000000',
                'tanggal_kesepakatan': '2030-01-15',
            },
        )
        transaksi = TransaksiTitipJual.objects.get(titip_jual=pengajuan)
        self.client.force_login(self.admin)
        url = reverse('admin_titip_jual_tindakan', args=[pengajuan.pk])
        self.client.post(url, {
            'aksi': 'verifikasi_penjualan_pemilik',
            'transaksi_id': transaksi.pk,
            'keputusan': 'verifikasi',
            'catatan': 'Bukti transaksi telah diperiksa.',
        })
        transaksi.refresh_from_db()
        self.assertEqual(transaksi.status, 'terverifikasi')
        self.assertTrue(transaksi.riwayat.filter(status_ke='terverifikasi').exists())

        payload_selesai = {
            'aksi': 'selesaikan_penjualan',
            'transaksi_id': transaksi.pk,
            'komisi_final': '10000000',
            'komisi_diperiksa_berdasarkan': 'Pasal komisi pada perjanjian.',
            'catatan_administrasi': 'Administrasi diperiksa.',
            'konfirmasi_perjanjian_diperiksa': 'on',
            'konfirmasi_administrasi_selesai': 'on',
        }
        self.client.post(url, payload_selesai)
        transaksi.refresh_from_db()
        self.assertEqual(transaksi.status, 'terverifikasi')

        DokumenTitipJual.objects.create(
            titip_jual=pengajuan,
            jenis='perjanjian',
            nama_file='contract-random.pdf',
            file=SimpleUploadedFile(
                'contract.pdf', b'%PDF-1.4 private contract', content_type='application/pdf',
            ),
            sudah_diverifikasi=True,
            diverifikasi_oleh=self.admin,
            tanggal_verifikasi=timezone.now(),
        )
        self.client.post(url, payload_selesai)
        transaksi.refresh_from_db()
        self.assertEqual(transaksi.status, 'terverifikasi')

        self.client.post(url, {
            'aksi': 'serah_terima',
            'jenis': 'keluar',
            'tujuan_keluar': 'pembeli',
            'transaksi_id': str(transaksi.pk),
            'tanggal_waktu': '2030-01-16T10:00',
            'nama_penyerah': 'Petugas Uji',
            'nama_penerima': 'Pembeli Uji',
            'lokasi': 'Showroom uji',
            'odometer_saat_ini': '42100',
            'jumlah_kunci': '2',
            'kondisi_unit': 'baik',
            'kelengkapan_dokumen': ['stnk', 'bpkb'],
            'catatan_kondisi': 'Unit diterima dalam kondisi baik.',
            'foto_kondisi': gambar_unggahan('kondisi-keluar.jpg'),
        })
        serah_terima = SerahTerimaUnit.objects.get(transaksi_penjualan=transaksi)
        self.assertTrue(serah_terima.lengkap)

        self.client.post(url, payload_selesai)
        transaksi.refresh_from_db()
        pengajuan.refresh_from_db()
        pengajuan.produk_terkait.refresh_from_db()
        self.assertEqual(transaksi.status, 'selesai')
        self.assertEqual(pengajuan.status, 'terjual')
        self.assertEqual(pengajuan.produk_terkait.status_stok, 'sold')
        self.assertTrue(pengajuan.riwayat_status.filter(status_ke='terjual').exists())

    def test_dokumen_dan_foto_serah_terima_mewajibkan_izin_pemilik_atau_staf_c2c(self):
        pengajuan = self._buat_pengajuan_tayang()
        dokumen = DokumenTitipJual.objects.create(
            titip_jual=pengajuan,
            jenis='stnk',
            nama_file='private-test.pdf',
            file=SimpleUploadedFile(
                'STNK.pdf', b'%PDF-1.4 private test document',
                content_type='application/pdf',
            ),
        )
        serah = pengajuan.serah_terima.get(jenis='masuk')
        url = reverse('serah_terima_foto_download', args=[serah.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()

        self.client.force_login(self.pengguna_lain)
        self.assertEqual(self.client.get(url).status_code, 404)

        self.client.force_login(self.admin)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response.close()

        staf_tanpa_izin = get_user_model().objects.create_user(
            'staf-tanpa-izin', 'staf2@example.test', 'Password123!', is_staff=True,
        )
        self.client.force_login(staf_tanpa_izin)
        self.assertEqual(self.client.get(url).status_code, 404)

        petugas_dokumen = get_user_model().objects.create_user(
            'petugas-dokumen', 'dokumen@example.test', 'Password123!', is_staff=True,
        )
        petugas_dokumen.groups.add(Group.objects.get(name='Petugas Dokumen'))
        self.client.force_login(petugas_dokumen)
        url_dokumen = reverse('dokumen_titip_jual_download', args=[dokumen.pk])
        response = self.client.get(url_dokumen)
        self.assertEqual(response.status_code, 200)
        response.close()
        self.assertEqual(self.client.get(url).status_code, 404)

        petugas_inspeksi = get_user_model().objects.create_user(
            'petugas-inspeksi', 'inspeksi@example.test', 'Password123!', is_staff=True,
        )
        petugas_inspeksi.groups.add(Group.objects.get(name='Petugas Inspeksi'))
        self.client.force_login(petugas_inspeksi)
        self.assertEqual(self.client.get(url_dokumen).status_code, 404)

    def test_panel_admin_c2c_menolak_staf_tanpa_permission_model(self):
        staf = get_user_model().objects.create_user(
            'staf-tanpa-permission', 'staf3@example.test', 'Password123!', is_staff=True,
        )
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG',
            no_whatsapp='081234567890', merek='Toyota', model='Avanza',
            tahun=2021, harga_harapan=Decimal('200000000'),
        )
        self.client.force_login(staf)
        self.assertEqual(self.client.get(reverse('admin_titip_jual')).status_code, 403)
        self.assertEqual(
            self.client.get(reverse('admin_titip_jual_detail', args=[pengajuan.pk])).status_code,
            403,
        )

    def test_lima_role_mendapat_permission_c2c_terpisah(self):
        migration = import_module(
            'toko.migrations.0015_c2c_permissions_and_inspection_default'
        )
        expected = {
            'Staf C2C': {
                'view_titipjual', 'change_status_titipjual', 'manage_price_titipjual',
            },
            'Petugas Inspeksi': {'view_titipjual', 'inspect_titipjual'},
            'Petugas Dokumen': {'view_titipjual', 'manage_documents_titipjual'},
            'Admin C2C': {
                'view_titipjual', 'change_titipjual', 'change_status_titipjual',
                'inspect_titipjual', 'manage_documents_titipjual',
                'manage_handover_titipjual', 'manage_sales_titipjual',
                'manage_price_titipjual',
            },
        }
        for nama_grup, izin_diharapkan in expected.items():
            with self.subTest(role=nama_grup):
                grup = Group.objects.get(name=nama_grup)
                izin = set(
                    grup.permissions.filter(
                        content_type__app_label='toko',
                    ).values_list('codename', flat=True)
                )
                self.assertTrue(izin_diharapkan.issubset(izin))
                managed_izin = izin & migration.MANAGED_PERMISSION_CODES
                self.assertEqual(managed_izin, izin_diharapkan)

        izin_tambahan = Permission.objects.get(
            content_type__app_label='toko',
            codename='add_titipjual',
        )
        petugas_dokumen_group = Group.objects.get(name='Petugas Dokumen')
        petugas_dokumen_group.permissions.add(izin_tambahan)
        anggota_lama = get_user_model().objects.create_user(
            'anggota-role-lama', 'anggota-lama@example.test', 'Password123!',
        )
        anggota_lama.groups.add(petugas_dokumen_group)
        grup_lama = Group.objects.create(name='Tim Operasi Lama')
        grup_lama.permissions.add(izin_tambahan)
        anggota_lama.groups.add(grup_lama)

        migration.create_c2c_roles(apps, SimpleNamespace(connection=connection))
        migration.create_c2c_roles(apps, SimpleNamespace(connection=connection))

        petugas_dokumen_group.refresh_from_db()
        grup_lama.refresh_from_db()
        anggota_lama.refresh_from_db()
        self.assertTrue(petugas_dokumen_group.permissions.filter(pk=izin_tambahan.pk).exists())
        self.assertTrue(grup_lama.permissions.filter(pk=izin_tambahan.pk).exists())
        self.assertTrue(anggota_lama.groups.filter(pk=petugas_dokumen_group.pk).exists())
        self.assertTrue(anggota_lama.groups.filter(pk=grup_lama.pk).exists())

        superuser = get_user_model().objects.create_superuser(
            'super-c2c', 'super@example.test', 'Password123!',
        )
        self.assertTrue(superuser.has_perm('toko.manage_sales_titipjual'))

    def test_pembatasan_tindakan_c2c_untuk_setiap_role(self):
        roles = {
            'Staf C2C': {
                'status', 'price_request', 'cancellation_request',
            },
            'Petugas Inspeksi': {
                'jadwalkan_inspeksi', 'mulai_inspeksi', 'hasil_inspeksi',
            },
            'Petugas Dokumen': {'verifikasi_dokumen', 'unggah_dokumen'},
            'Admin C2C': {
                'status', 'jadwalkan_inspeksi', 'mulai_inspeksi',
                'hasil_inspeksi', 'verifikasi_dokumen', 'unggah_dokumen',
                'serah_terima', 'verifikasi_penjualan_pemilik',
                'catat_penjualan_mbg', 'selesaikan_penjualan',
                'price_request', 'cancellation_request',
            },
        }
        tindakan = (
            'status', 'jadwalkan_inspeksi', 'mulai_inspeksi', 'hasil_inspeksi',
            'verifikasi_dokumen', 'unggah_dokumen', 'serah_terima',
            'verifikasi_penjualan_pemilik', 'catat_penjualan_mbg',
            'selesaikan_penjualan',
        )
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG',
            no_whatsapp='081234567890', merek='Toyota', model='Avanza',
            tahun=2021, harga_harapan=Decimal('200000000'),
        )
        request_harga = PermintaanTitipJual.objects.create(
            titip_jual=pengajuan, jenis='ubah_harga', dibuat_oleh=self.pemilik,
        )
        request_batal = PermintaanTitipJual.objects.create(
            titip_jual=pengajuan, jenis='pembatalan', dibuat_oleh=self.pemilik,
        )
        for nama_role, izin_diharapkan in roles.items():
            user = self.admin if nama_role == 'Admin C2C' else get_user_model().objects.create_user(
                f'role-{nama_role.lower().replace(" ", "-")}',
                f'{nama_role.lower().replace(" ", "-")}@example.test',
                'Password123!',
                is_staff=True,
            )
            if nama_role != 'Admin C2C':
                user.groups.add(Group.objects.get(name=nama_role))
            self.assertTrue(user.has_perm('toko.view_titipjual'))
            for aksi in tindakan:
                request = SimpleNamespace(user=user, POST={})
                with self.subTest(role=nama_role, action=aksi):
                    if aksi in izin_diharapkan:
                        _periksa_izin_tindakan_c2c(request, aksi, pengajuan)
                    else:
                        with self.assertRaises(PermissionDenied):
                            _periksa_izin_tindakan_c2c(request, aksi, pengajuan)

            for jenis, permintaan in (
                ('price_request', request_harga),
                ('cancellation_request', request_batal),
            ):
                request = SimpleNamespace(
                    user=user,
                    POST={'permintaan_id': str(permintaan.pk)},
                )
                with self.subTest(role=nama_role, request=jenis):
                    if jenis in izin_diharapkan:
                        _periksa_izin_tindakan_c2c(request, 'proses_permintaan', pengajuan)
                    else:
                        with self.assertRaises(PermissionDenied):
                            _periksa_izin_tindakan_c2c(
                                request, 'proses_permintaan', pengajuan,
                            )

        superuser = get_user_model().objects.create_superuser(
            'role-superuser', 'role-superuser@example.test', 'Password123!',
        )
        for aksi in tindakan:
            _periksa_izin_tindakan_c2c(
                SimpleNamespace(user=superuser, POST={}),
                aksi,
                pengajuan,
            )
        for permintaan in (request_harga, request_batal):
            _periksa_izin_tindakan_c2c(
                SimpleNamespace(
                    user=superuser,
                    POST={'permintaan_id': str(permintaan.pk)},
                ),
                'proses_permintaan',
                pengajuan,
            )

    def test_inspektor_dapat_inspeksi_tetapi_tidak_mengubah_status(self):
        petugas = get_user_model().objects.create_user(
            'petugas-inspeksi-aksi', 'inspeksi-aksi@example.test',
            'Password123!', is_staff=True,
        )
        petugas.groups.add(Group.objects.get(name='Petugas Inspeksi'))
        pengajuan = TitipJual.objects.create(
            pengguna=self.pemilik, nama_pemilik='Pemilik MBG',
            no_whatsapp='081234567890', merek='Toyota', model='Avanza',
            tahun=2021, harga_harapan=Decimal('200000000'), status='pending',
        )
        self.client.force_login(petugas)
        url = reverse('admin_titip_jual_tindakan', args=[pengajuan.pk])
        self.assertEqual(self.client.post(url, {
            'aksi': 'status', 'status': 'ditolak', 'catatan': 'Tidak sesuai.',
        }).status_code, 403)
        self.assertEqual(self.client.post(url, {
            'aksi': 'jadwalkan_inspeksi',
            'jadwal_inspeksi': '2030-01-15T10:00',
        }).status_code, 302)

    def test_foto_tambahan_tidak_menggantikan_foto_wajib_dan_file_invalid_ditolak(self):
        post_data = {
            **data_pengajuan(),
            **foto_wajib(),
            'foto_tambahan': [gambar_unggahan('only-additional.jpg')],
        }
        post_data.pop('foto_samping_kanan')
        response = self.client.post(reverse('titip_jual_tambah'), post_data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TitipJual.objects.exists())

    def test_validasi_dokumen_mengganti_nama_dan_membatasi_ukuran(self):
        dokumen = SimpleUploadedFile(
            '../../nama-asli-ktp.PDF',
            b'%PDF-1.4 test document',
            content_type='application/pdf',
        )
        dokumen = validasi_dokumen_titip_jual(dokumen)
        self.assertRegex(dokumen.name, r'^[0-9a-f]{32}\.pdf$')

        terlalu_besar = SimpleUploadedFile(
            'besar.pdf',
            b'x' * (10 * 1024 * 1024 + 1),
            content_type='application/pdf',
        )
        with self.assertRaises(ValidationError):
            validasi_dokumen_titip_jual(terlalu_besar)

        post_data = {**data_pengajuan(), **foto_wajib()}
        post_data['foto_depan'] = SimpleUploadedFile(
            'fake.jpg', b'not actually an image', content_type='image/jpeg',
        )
        response = self.client.post(reverse('titip_jual_tambah'), post_data)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TitipJual.objects.exists())
