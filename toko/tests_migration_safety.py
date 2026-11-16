"""
Pengujian Keamanan Migrasi Database & Enkripsi KTP MBG.
Memverifikasi:
1. Pemindahan data aman dari catatan_kondisi ke catatan_tambahan (0011).
2. Menjaga 2 data lama (ID 1 & ID 2) tanpa kehilangan data.
3. Idempotensi pemindahan data (menjalankan ulang tidak menduplikasi/menimpa data).
4. Enkripsi KTP (0014) menggunakan TITIP_JUAL_KTP_ENCRYPTION_KEY secara konsisten.
5. Pencegahan enkripsi ganda (tidak mengenkripsi ulang ciphertext yang sudah valid).
6. Tidak ada nomor KTP plaintext dalam pesan log/error.
"""
from decimal import Decimal
from importlib import import_module
from types import SimpleNamespace
from unittest import mock

from cryptography.fernet import Fernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

import toko.fields
from toko.models import CustomUser, TitipJual

DUMMY_FERNET_KEY = Fernet.generate_key().decode('ascii')


class CatatanKondisiMigrationSafetyTests(TestCase):
    def setUp(self):
        self.migration = import_module('toko.migrations.0011_c2c_titipjual_sprint1')
        self.table_name = 'probe_titipjual_catatan'
        self.quoted_table = connection.ops.quote_name(self.table_name)
        self.quoted_id = connection.ops.quote_name('id')
        self.quoted_kondisi = connection.ops.quote_name('catatan_kondisi')
        self.quoted_tambahan = connection.ops.quote_name('catatan_tambahan')

        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE TABLE {self.quoted_table} ('
                f'{self.quoted_id} INTEGER PRIMARY KEY, '
                f'{self.quoted_kondisi} TEXT NULL, '
                f'{self.quoted_tambahan} TEXT NULL'
                f')'
            )
            # Masukkan dua data aktual yang ditemukan di TiDB Cloud
            cursor.execute(
                f'INSERT INTO {self.quoted_table} ({self.quoted_id}, {self.quoted_kondisi}, {self.quoted_tambahan}) '
                f'VALUES (%s, %s, %s)',
                [1, 'Pajak mati dan kualitas ban bagian luarnya sudah tipis', '']
            )
            cursor.execute(
                f'INSERT INTO {self.quoted_table} ({self.quoted_id}, {self.quoted_kondisi}, {self.quoted_tambahan}) '
                f'VALUES (%s, %s, %s)',
                [2, 'Data simulasi untuk pengujian, kondisi kendaraan dan dokumen belum diverifikasi.', '']
            )
            # Data ke-3: baris dengan catatan_tambahan yang sudah memiliki data lain
            cursor.execute(
                f'INSERT INTO {self.quoted_table} ({self.quoted_id}, {self.quoted_kondisi}, {self.quoted_tambahan}) '
                f'VALUES (%s, %s, %s)',
                [3, 'Catatan kondisi lama', 'Catatan tambahan yang sudah ada sebelumnya']
            )

        class FakeMeta:
            db_table = self.table_name
            pk = SimpleNamespace(column='id')

        class FakeApps:
            @staticmethod
            def get_model(app_label, model_name):
                return SimpleNamespace(_meta=FakeMeta)

        self.fake_apps = FakeApps
        self.schema_editor = SimpleNamespace(connection=connection)

    def tearDown(self):
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE {self.quoted_table}')
        super().tearDown()

    def test_salin_catatan_kondisi_ke_tambahan_berhasil_dan_idempoten(self):
        # Jalankan pemindahan data pertama kali
        self.migration.salin_catatan_kondisi_ke_tambahan(self.fake_apps, self.schema_editor)

        with connection.cursor() as cursor:
            cursor.execute(f'SELECT {self.quoted_id}, {self.quoted_tambahan} FROM {self.quoted_table} ORDER BY {self.quoted_id}')
            rows = dict(cursor.fetchall())

        # Verifikasi ID 1 & ID 2 persis sesuai data asli
        self.assertEqual(rows[1], 'Pajak mati dan kualitas ban bagian luarnya sudah tipis')
        self.assertEqual(rows[2], 'Data simulasi untuk pengujian, kondisi kendaraan dan dokumen belum diverifikasi.')
        # Verifikasi ID 3 tidak menimpa data yang sudah ada sebelumnya
        self.assertIn('Catatan tambahan yang sudah ada sebelumnya', rows[3])
        self.assertIn('Catatan kondisi lama', rows[3])

        # Uji IDEMPOTENSI: Jalankan ulang pemindahan data kedua kali
        self.migration.salin_catatan_kondisi_ke_tambahan(self.fake_apps, self.schema_editor)

        with connection.cursor() as cursor:
            cursor.execute(f'SELECT {self.quoted_id}, {self.quoted_tambahan} FROM {self.quoted_table} ORDER BY {self.quoted_id}')
            rows_kedua = dict(cursor.fetchall())

        # Data tetap sama, tidak digandakan / terduplikasi
        self.assertEqual(rows_kedua[1], rows[1])
        self.assertEqual(rows_kedua[2], rows[2])
        self.assertEqual(rows_kedua[3], rows[3])


@override_settings(TITIP_JUAL_KTP_ENCRYPTION_KEY=DUMMY_FERNET_KEY)
class KTPEncryptionSecurityTests(TransactionTestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='user_ktp_test',
            password='Password123!',
            no_whatsapp='081234567890',
        )

    def test_enkripsi_ktp_menyimpan_ciphertext_dan_mendekripsi_saat_dibaca(self):
        nomor_ktp_asli = '7371123456780001'
        pengajuan = TitipJual.objects.create(
            pengguna=self.user,
            nama_pemilik='Pemilik KTP',
            no_whatsapp='081234567890',
            no_ktp=nomor_ktp_asli,
            merek='Toyota',
            model='Yaris',
            tahun=2020,
            harga_harapan=Decimal('180000000'),
        )

        with connection.cursor() as cursor:
            cursor.execute('SELECT no_ktp FROM toko_titipjual WHERE id = %s', [pengajuan.pk])
            ciphertext = cursor.fetchone()[0]

        # Di database mentah HARUS tersimpan sebagai ciphertext Fernet (dimulai dengan gAAAA)
        self.assertTrue(ciphertext.startswith('gAAAA'))
        self.assertNotEqual(ciphertext, nomor_ktp_asli)

        # Saat diakses melalui model Django, didekripsi kembali menjadi plaintext asli
        pengajuan.refresh_from_db()
        self.assertEqual(pengajuan.no_ktp, nomor_ktp_asli)

    def test_tidak_mengenkrpsi_ulang_ciphertext_yang_sudah_valid(self):
        """Idempotensi get_prep_value: ciphertext yang sudah valid tidak dienkripsi ganda."""
        field = toko.fields.EncryptedCharField(max_length=255, max_plaintext_length=20)
        fernet = Fernet(DUMMY_FERNET_KEY.encode('ascii'))

        # Buat ciphertext valid
        ciphertext = fernet.encrypt('7371000000000002'.encode('utf-8')).decode('ascii')

        # Jalankan get_prep_value pada ciphertext
        hasil = field.get_prep_value(ciphertext)

        # Hasil HARUS tetap ciphertext yang sama (tidak dienkripsi ulang)
        self.assertEqual(hasil, ciphertext)
        # Dekripsi harus menghasilkan nomor asli
        self.assertEqual(fernet.decrypt(hasil.encode('ascii')).decode('utf-8'), '7371000000000002')

    def test_validasi_panjang_hanya_pada_plaintext_bukan_ciphertext(self):
        """Memastikan ciphertext yang panjang (>20 karakter) tidak ditolak oleh max_plaintext_length."""
        field = toko.fields.EncryptedCharField(max_length=255, max_plaintext_length=20)
        fernet = Fernet(DUMMY_FERNET_KEY.encode('ascii'))
        ciphertext = fernet.encrypt('7371123456780001'.encode('utf-8')).decode('ascii')
        self.assertGreater(len(ciphertext), 20)

        # Tidak boleh raise ValidationError karena merupakan ciphertext valid
        prep = field.get_prep_value(ciphertext)
        self.assertEqual(prep, ciphertext)

        # Plaintext yang melebihi 20 karakter HARUS ditolak
        with self.assertRaises(ValidationError):
            field.get_prep_value('12345678901234567890123')  # 23 karakter

    def test_tidak_menampilkan_nomor_ktp_dalam_pesan_error(self):
        field = toko.fields.EncryptedCharField(max_length=255, max_plaintext_length=16)
        ktp_rahasia = '7371999988887777000'
        try:
            field.get_prep_value(ktp_rahasia)
        except ValidationError as exc:
            pesan_error = str(exc)
            # Pastikan nomor KTP rahasia TIDAK muncul dalam pesan error
            self.assertNotIn(ktp_rahasia, pesan_error)

    def test_pesan_error_migrasi_0014_menyebut_titip_jual_ktp_encryption_key(self):
        migration = import_module('toko.migrations.0014_encrypt_titipjual_ktp')
        table_name = 'probe_ktp_err_msg'
        quoted_table = connection.ops.quote_name(table_name)
        quoted_id = connection.ops.quote_name('id')
        quoted_ktp = connection.ops.quote_name('no_ktp')

        with connection.cursor() as cursor:
            cursor.execute(f'CREATE TABLE {quoted_table} ({quoted_id} INT PRIMARY KEY, {quoted_ktp} VARCHAR(255))')
            # Masukkan ciphertext dengan kunci asing/salah (awalan gAAAA tapi tidak bisa didekripsi)
            cursor.execute(f'INSERT INTO {quoted_table} VALUES (%s, %s)', [1, 'gAAAAABfakeCiphertextInvalidToken1234567890'])

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
        try:
            with self.assertRaises(RuntimeError) as ctx:
                migration.encrypt_legacy_ktp(FakeApps, schema_editor)
            pesan = str(ctx.exception)
            # Pastikan menyebut TITIP_JUAL_KTP_ENCRYPTION_KEY, bukan C2C_KTP_ENCRYPTION_KEY
            self.assertIn('TITIP_JUAL_KTP_ENCRYPTION_KEY', pesan)
            self.assertNotIn('C2C_KTP_ENCRYPTION_KEY', pesan)
        finally:
            with connection.cursor() as cursor:
                cursor.execute(f'DROP TABLE {quoted_table}')


@override_settings(TITIP_JUAL_KTP_ENCRYPTION_KEY=DUMMY_FERNET_KEY)
class EndToEndMigrationChainTests(TransactionTestCase):
    def test_eksekusi_berurutan_0010_ke_0017_pada_database(self):
        """
        Simulasi realistis kondisi TiDB Cloud saat ini:
        1. Database berada pada status migrasi 0010_pengaturanwebsite_logo_favicon.
        2. Terdapat 2 data aktual catatan_kondisi pada tabel toko_titipjual (ID 1 & ID 2).
        3. Migrasi 0011 sampai 0017 dijalankan secara berurutan.
        4. Verifikasi bahwa data ID 1 & ID 2 selamat dan berpindah ke catatan_tambahan,
           kolom catatan_kondisi berhasil dihapus, dan skema akhir 0017 terbentuk sempurna.
        """
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)

        # 1. Pastikan graph dimuat dan migrasikan mundur ke 0010
        executor.loader.build_graph()
        with connection.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) FROM toko_titipjual')
            self.assertEqual(cursor.fetchone()[0], 0)
        with override_settings(C2C_KTP_ALLOW_PLAINTEXT_ROLLBACK=True):
            executor.migrate([('toko', '0010_pengaturanwebsite_logo_favicon')])

        # 2. Sisipkan 2 data catatan_kondisi persis seperti data aktual di TiDB saat ini
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO toko_titipjual (id, nama_pemilik, no_whatsapp, email, merek_model, tahun, transmisi, harga_harapan, foto_kendaraan, status, created_at, catatan_kondisi) "
                "VALUES (1, 'Pemilik 1', '081234567891', 'p1@test.com', 'Avanza G', 2018, 'MT', 150000000, '', 'pending', '2026-01-01 00:00:00', %s)",
                ['Pajak mati dan kualitas ban bagian luarnya sudah tipis']
            )
            cursor.execute(
                "INSERT INTO toko_titipjual (id, nama_pemilik, no_whatsapp, email, merek_model, tahun, transmisi, harga_harapan, foto_kendaraan, status, created_at, catatan_kondisi) "
                "VALUES (2, 'Pemilik 2', '081234567892', 'p2@test.com', 'Brio E', 2019, 'AT', 120000000, '', 'pending', '2026-01-01 00:00:00', %s)",
                ['Data simulasi untuk pengujian, kondisi kendaraan dan dokumen belum diverifikasi.']
            )

        # 3. Jalankan migrasi 0011 sampai 0017 secara berurutan
        executor.loader.build_graph()
        executor.migrate([('toko', '0017_pesananpembelian_midtrans_order_id_and_more')])

        # 4. Verifikasi hasil akhir di database
        with connection.cursor() as cursor:
            columns = [c.name for c in connection.introspection.get_table_description(cursor, 'toko_titipjual')]
            self.assertIn('catatan_tambahan', columns)
            self.assertNotIn('catatan_kondisi', columns)

            cursor.execute("SELECT id, catatan_tambahan FROM toko_titipjual ORDER BY id")
            rows = dict(cursor.fetchall())

            self.assertEqual(rows[1], 'Pajak mati dan kualitas ban bagian luarnya sudah tipis')
            self.assertEqual(rows[2], 'Data simulasi untuk pengujian, kondisi kendaraan dan dokumen belum diverifikasi.')

