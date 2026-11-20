import itertools
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from io import StringIO
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, models, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse

from .models import (
    BookingCekUnit, PembayaranPesanan, PengaturanPembelian, PesananPembelian,
    Produk, RefundPesanan, RiwayatPesanan, TitipJual, Transaksi, TransaksiTitipJual,
    akhir_masa_booking,
)
from .views import produk_tayang

WITA = ZoneInfo('Asia/Makassar')
UTC = ZoneInfo('UTC')
_urutan = itertools.count(1)


def wita(tahun, bulan, hari, jam=0, menit=0, detik=0, mikro=0):
    return datetime(tahun, bulan, hari, jam, menit, detik, mikro, tzinfo=WITA)


def bukti_transfer_pdf():
    return SimpleUploadedFile('bukti.pdf', b'%PDF-1.4\nbukti transfer', content_type='application/pdf')


def buat_produk(**kw):
    """Pabrik data uji: unit B2C ready seharga Rp200.000.000 (gambar berupa path teks)."""
    data = dict(
        sku=f'PB-{next(_urutan):04d}', nama='Toyota Avanza 1.5 G AT 2021', merek='Toyota',
        kategori='MPV', tahun=2021, transmisi='AT', jarak_tempuh=30000, warna='Putih',
        plat_nomor='DD 1 XX', harga=Decimal('200000000'), tipe_penjual='B2C',
        status_stok='ready', gambar_utama='mobil/uji.jpg',
        deskripsi_singkat='Ringkas', deskripsi_detail='Detail',
    )
    data.update(kw)
    return Produk.objects.create(**data)


class PembelianTestCase(TestCase):
    """Dasar: persentase Booking Fee 2% sehingga Rp200 juta → Booking Fee Rp4 juta."""

    def setUp(self):
        pengguna = get_user_model().objects
        self.staf = pengguna.create_user(
            'staf-pembelian', 'staf-pembelian@example.test', 'Password123!', is_staff=True,
        )
        self.pelanggan = pengguna.create_user(
            'pelanggan-pembelian', 'pelanggan-pembelian@example.test', 'Password123!',
        )
        self.pelanggan_lain = pengguna.create_user(
            'pelanggan-lain', 'pelanggan-lain@example.test', 'Password123!',
        )
        pengaturan = PengaturanPembelian.ambil()
        pengaturan.persentase_booking_fee = Decimal('2.00')
        pengaturan.save()
        self.produk = buat_produk()

    def buat_pesanan(self, produk=None, pelanggan=None, **kw):
        data = dict(
            produk=produk or self.produk, pelanggan=pelanggan or self.pelanggan,
            nama_pelanggan='Budi Santoso', no_whatsapp='0812-3456-7890',
            metode_pembayaran='cash',
        )
        data.update(kw)
        return PesananPembelian.buat_dari_produk(**data)

    def bersihkan_bukti_transfer(self, pembayaran):
        self.addCleanup(
            pembayaran.bukti_transfer.storage.delete,
            pembayaran.bukti_transfer.name,
        )

    def buat_pesanan_data_lama(self, produk=None, pelanggan=None):
        """
        Pesanan 'menunggu Booking Fee' yang melewati buat_dari_produk(), menyimulasikan data
        lama dari sebelum aturan "satu pesanan aktif per unit" (Sprint 2.1) diberlakukan.
        """
        produk = produk or self.produk
        pengaturan = PengaturanPembelian.ambil()
        return PesananPembelian.objects.create(
            pelanggan=pelanggan or self.pelanggan_lain, produk=produk,
            nama_pelanggan='Data Lama', no_whatsapp='6281298765432',
            nama_kendaraan=produk.nama, sku_kendaraan=produk.sku, plat_nomor=produk.plat_nomor,
            harga_kendaraan=produk.harga,
            persentase_booking_fee=pengaturan.persentase_booking_fee,
            nominal_booking_fee=pengaturan.hitung_booking_fee(produk.harga),
            metode_pembayaran='cash',
        )

    def buat_pembayaran(self, pesanan, jenis='booking_fee', nominal=None, **kw):
        if nominal is None:
            nominal = pesanan.nominal_booking_fee
        return PembayaranPesanan.objects.create(
            pesanan=pesanan, jenis=jenis, nominal=nominal, **kw,
        )

    def aktifkan_booking(self, pesanan=None, sekarang=None):
        """Buat (atau pakai) pesanan lalu verifikasi Booking Fee-nya."""
        pesanan = pesanan or self.buat_pesanan()
        self.buat_pembayaran(pesanan).verifikasi(
            self.staf, sekarang=sekarang or wita(2026, 11, 15, 10, 0),
        )
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        return pesanan

    def bayar(self, pesanan, jenis, nominal, sekarang):
        pembayaran = self.buat_pembayaran(pesanan, jenis, Decimal(nominal))
        pembayaran.verifikasi(self.staf, sekarang=sekarang)
        return pembayaran


# ═══════════════════════════════════════════════════════════════════
# PengaturanPembelian
# ═══════════════════════════════════════════════════════════════════

class PengaturanPembelianTests(TestCase):
    def test_ambil_membuat_satu_baris_singleton(self):
        PengaturanPembelian.objects.all().delete()
        pertama = PengaturanPembelian.ambil()
        kedua = PengaturanPembelian.ambil()
        self.assertEqual((pertama.pk, kedua.pk), (1, 1))
        self.assertEqual(PengaturanPembelian.objects.count(), 1)
        self.assertGreater(pertama.persentase_booking_fee, 0)

    def test_save_selalu_memakai_satu_baris(self):
        PengaturanPembelian(persentase_booking_fee=Decimal('3.00')).save()
        PengaturanPembelian(persentase_booking_fee=Decimal('4.00')).save()
        self.assertEqual(PengaturanPembelian.objects.count(), 1)
        self.assertEqual(PengaturanPembelian.ambil().persentase_booking_fee, Decimal('4.00'))

    def test_booking_fee_dihitung_dari_persentase(self):
        pengaturan = PengaturanPembelian(persentase_booking_fee=Decimal('2.00'))
        self.assertEqual(pengaturan.hitung_booking_fee(Decimal('200000000')), Decimal('4000000'))

    def test_booking_fee_mengikuti_persentase_yang_diubah_admin(self):
        pengaturan = PengaturanPembelian.ambil()
        pengaturan.persentase_booking_fee = Decimal('2.50')
        pengaturan.save()
        self.assertEqual(
            PengaturanPembelian.ambil().hitung_booking_fee(Decimal('200000000')),
            Decimal('5000000'),
        )

    def test_booking_fee_dibulatkan_ke_rupiah_penuh(self):
        pengaturan = PengaturanPembelian(persentase_booking_fee=Decimal('1.25'))
        # 1,25% × 333.333.333 = 4.166.666,6625 → 4.166.667
        self.assertEqual(pengaturan.hitung_booking_fee(Decimal('333333333')), Decimal('4166667'))

    def test_validasi_rentang_persentase(self):
        for nilai in ('0', '-1', '100.01'):
            with self.subTest(nilai=nilai):
                with self.assertRaises(ValidationError) as ctx:
                    PengaturanPembelian(persentase_booking_fee=Decimal(nilai)).full_clean()
                self.assertIn('persentase_booking_fee', ctx.exception.message_dict)
        for nilai in ('0.01', '100'):
            with self.subTest(nilai=nilai):
                PengaturanPembelian(persentase_booking_fee=Decimal(nilai)).full_clean()

    def test_constraint_database_menolak_persentase_tidak_valid(self):
        PengaturanPembelian.ambil()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PengaturanPembelian.objects.filter(pk=1).update(persentase_booking_fee=Decimal('0'))


# ═══════════════════════════════════════════════════════════════════
# PesananPembelian: pembuatan, snapshot, validasi
# ═══════════════════════════════════════════════════════════════════

class PesananPembelianTests(PembelianTestCase):
    def test_beranda_menyembunyikan_unit_dengan_pesanan_aktif(self):
        self.buat_pesanan()
        for nomor in range(3):
            buat_produk(nama=f'Unit Tersedia {nomor}')

        response = self.client.get(reverse('beranda'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['katalog_mobil']), 3)
        self.assertEqual(response.context['total_unit_tayang'], 3)
        self.assertNotContains(response, self.produk.nama)

    def test_pesanan_menyimpan_snapshot_harga_dan_booking_fee(self):
        self.produk.harga_promo = Decimal('5000000')
        self.produk.save()
        pesanan = self.buat_pesanan(metode_pembayaran='kredit')
        self.assertEqual(pesanan.harga_kendaraan, Decimal('200000000'))
        self.assertEqual(pesanan.harga_promo, Decimal('5000000'))
        self.assertEqual(pesanan.persentase_booking_fee, Decimal('2.00'))
        self.assertEqual(pesanan.nominal_booking_fee, Decimal('4000000'))
        self.assertEqual(pesanan.nama_kendaraan, self.produk.nama)
        self.assertEqual(pesanan.sku_kendaraan, self.produk.sku)
        self.assertEqual(pesanan.metode_pembayaran, 'kredit')
        self.assertEqual(pesanan.opsi_penyerahan, 'showroom')
        self.assertEqual(pesanan.status, 'menunggu_booking_fee')
        self.assertIsNone(pesanan.booking_berakhir_pada)

    def test_snapshot_tidak_berubah_saat_pengaturan_atau_harga_berubah(self):
        lama = self.buat_pesanan()
        lama.batalkan_tanpa_pembayaran(self.pelanggan, 'Batal sebelum membayar')  # unit lepas
        pengaturan = PengaturanPembelian.ambil()
        pengaturan.persentase_booking_fee = Decimal('5.00')
        pengaturan.save()
        self.produk.harga = Decimal('300000000')
        self.produk.save()
        lama.refresh_from_db()
        self.assertEqual(lama.harga_kendaraan, Decimal('200000000'))
        self.assertEqual(lama.nominal_booking_fee, Decimal('4000000'))
        baru = self.buat_pesanan(pelanggan=self.pelanggan_lain)
        self.assertEqual(baru.harga_kendaraan, Decimal('300000000'))
        self.assertEqual(baru.persentase_booking_fee, Decimal('5.00'))
        self.assertEqual(baru.nominal_booking_fee, Decimal('15000000'))

    def test_snapshot_dan_booking_fee_tidak_dapat_diubah_setelah_dibuat(self):
        pesanan = self.buat_pesanan()
        pesanan.harga_kendaraan = Decimal('1000')
        with self.assertRaisesMessage(ValueError, 'tidak dapat diubah'):
            pesanan.save()
        pesanan.refresh_from_db()
        pesanan.nominal_booking_fee = Decimal('1000')
        with self.assertRaisesMessage(ValueError, 'tidak dapat diubah'):
            pesanan.save(update_fields=['nominal_booking_fee'])
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.harga_kendaraan, Decimal('200000000'))
        self.assertEqual(pesanan.nominal_booking_fee, Decimal('4000000'))

    def test_pesanan_baru_belum_mengunci_unit(self):
        self.buat_pesanan()
        self.produk.refresh_from_db()
        self.assertEqual(self.produk.status_stok, 'ready')
        self.assertIn(self.produk, produk_tayang())

    def test_riwayat_awal_tercatat(self):
        pesanan = self.buat_pesanan()
        riwayat = pesanan.riwayat.get()
        self.assertEqual((riwayat.status_dari, riwayat.status_ke), ('', 'menunggu_booking_fee'))
        self.assertEqual(riwayat.diubah_oleh, self.pelanggan)

    def test_menolak_unit_yang_tidak_ready(self):
        for status_stok in ('in_review', 'sold', 'returned'):
            with self.subTest(status_stok=status_stok):
                produk = buat_produk(status_stok=status_stok)
                with self.assertRaisesMessage(ValueError, 'tidak tersedia'):
                    self.buat_pesanan(produk=produk)

    def test_menolak_unit_c2c_agar_alur_titip_jual_tidak_terganggu(self):
        produk = buat_produk(tipe_penjual='C2C')
        with self.assertRaisesMessage(ValueError, 'B2C'):
            self.buat_pesanan(produk=produk)

    def test_menolak_pesanan_tanpa_pelanggan(self):
        with self.assertRaisesMessage(ValueError, 'Pelanggan wajib'):
            PesananPembelian.buat_dari_produk(
                produk=self.produk, pelanggan=None, nama_pelanggan='Budi',
                no_whatsapp='081234567890', metode_pembayaran='cash',
            )

    def test_pengantaran_towing_wajib_alamat(self):
        with self.assertRaises(ValidationError) as ctx:
            self.buat_pesanan(opsi_penyerahan='towing')
        self.assertIn('alamat_pengantaran', ctx.exception.message_dict)
        pesanan = self.buat_pesanan(opsi_penyerahan='towing', alamat_pengantaran='Jl. Perintis No. 1')
        self.assertEqual(pesanan.opsi_penyerahan, 'towing')

    def test_pilihan_metode_dan_penyerahan_dibatasi(self):
        with self.assertRaises(ValidationError) as ctx:
            self.buat_pesanan(metode_pembayaran='cicilan')
        self.assertIn('metode_pembayaran', ctx.exception.message_dict)
        with self.assertRaises(ValidationError) as ctx:
            self.buat_pesanan(opsi_penyerahan='kurir')
        self.assertIn('opsi_penyerahan', ctx.exception.message_dict)

    def test_beli_langsung_kredit_menjadi_pengajuan_dan_belum_mengunci_unit(self):
        pesanan = self.buat_pesanan(
            jenis_pembelian='beli_langsung',
            metode_pembayaran='kredit',
            saluran_pembayaran='transfer',
        )
        self.assertEqual(pesanan.status, 'menunggu_kredit')
        self.assertEqual(pesanan.nominal_dp_kredit, Decimal('40000000'))
        self.assertEqual(pesanan.nominal_pembayaran_midtrans, Decimal('40000000'))
        self.produk.refresh_from_db()
        self.assertEqual(self.produk.status_stok, 'ready')

    def test_pengajuan_kredit_beli_langsung_wajib_menggunakan_transfer_manual(self):
        with self.assertRaises(ValidationError) as ctx:
            self.buat_pesanan(
                jenis_pembelian='beli_langsung',
                metode_pembayaran='kredit',
                saluran_pembayaran='midtrans',
            )
        self.assertIn('saluran_pembayaran', ctx.exception.message_dict)

    def test_whatsapp_dinormalisasi_dan_divalidasi(self):
        self.assertEqual(self.buat_pesanan().no_whatsapp, '6281234567890')
        with self.assertRaises(ValidationError) as ctx:
            self.buat_pesanan(produk=buat_produk(), no_whatsapp='12345')
        self.assertIn('no_whatsapp', ctx.exception.message_dict)

    def test_nomor_pesanan_dibuat_otomatis_dan_unik(self):
        pertama = self.buat_pesanan()
        kedua = self.buat_pesanan(produk=buat_produk(), pelanggan=self.pelanggan_lain)
        self.assertRegex(pertama.nomor_pesanan, r'^PB-MBG-\d{8}-[0-9A-F]{6}$')
        self.assertNotEqual(pertama.nomor_pesanan, kedua.nomor_pesanan)

    def test_semua_nominal_memakai_decimalfield(self):
        daftar = [
            (PengaturanPembelian, 'persentase_booking_fee'),
            (PesananPembelian, 'harga_kendaraan'),
            (PesananPembelian, 'harga_promo'),
            (PesananPembelian, 'persentase_booking_fee'),
            (PesananPembelian, 'nominal_booking_fee'),
            (PembayaranPesanan, 'nominal'),
            (RefundPesanan, 'nominal_diajukan'),
            (RefundPesanan, 'nominal_disetujui'),
        ]
        for model, nama in daftar:
            with self.subTest(model=model.__name__, field=nama):
                self.assertIsInstance(model._meta.get_field(nama), models.DecimalField)

    def test_constraint_database_menjaga_harga_dan_booking_fee(self):
        pesanan = self.buat_pesanan()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PesananPembelian.objects.filter(pk=pesanan.pk).update(
                nominal_booking_fee=Decimal('300000000'),
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            PesananPembelian.objects.filter(pk=pesanan.pk).update(harga_kendaraan=Decimal('0'))

    def test_produk_dengan_pesanan_tidak_dapat_dihapus(self):
        self.buat_pesanan()
        with self.assertRaises(ProtectedError):
            self.produk.delete()

    def test_str_model_memuat_nomor_dan_status(self):
        pesanan = self.buat_pesanan()
        self.assertIn(pesanan.nomor_pesanan, str(pesanan))
        self.assertIn('Menunggu Booking Fee', str(pesanan))


# ═══════════════════════════════════════════════════════════════════
# Booking Fee: verifikasi, kunci unit, masa booking 23.59 WITA
# ═══════════════════════════════════════════════════════════════════

class BookingFeeTests(PembelianTestCase):
    def test_booking_fee_menjadi_bagian_dari_pembayaran_kendaraan(self):
        pesanan = self.aktifkan_booking()
        self.assertEqual(pesanan.nominal_booking_fee, Decimal('4000000'))
        self.assertEqual(pesanan.total_terverifikasi(), Decimal('4000000'))
        self.assertEqual(pesanan.sisa_pembayaran(), Decimal('196000000'))

    def test_pembayaran_belum_diverifikasi_tidak_mengunci_unit(self):
        pesanan = self.buat_pesanan()
        self.buat_pembayaran(pesanan)
        self.produk.refresh_from_db()
        pesanan.refresh_from_db()
        self.assertEqual(self.produk.status_stok, 'ready')
        self.assertEqual(pesanan.status, 'menunggu_booking_fee')
        self.assertEqual(pesanan.total_terverifikasi(), Decimal('0'))
        self.assertIn(self.produk, produk_tayang())

    def test_verifikasi_mengunci_unit_dan_mengeluarkannya_dari_produk_tayang(self):
        pesanan = self.aktifkan_booking()
        self.assertEqual(pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')
        self.assertNotIn(self.produk, produk_tayang())

    def test_status_pembayaran_dan_pesanan_terpisah(self):
        pesanan = self.buat_pesanan()
        pembayaran = self.buat_pembayaran(pesanan)
        pembayaran.verifikasi(self.staf, catatan='Sesuai mutasi', sekarang=wita(2026, 11, 15, 10))
        pembayaran.refresh_from_db()
        pesanan.refresh_from_db()
        self.assertEqual(pembayaran.status, 'terverifikasi')
        self.assertEqual(pembayaran.diverifikasi_oleh, self.staf)
        self.assertEqual(pembayaran.tanggal_verifikasi, wita(2026, 11, 15, 10))
        self.assertEqual(pesanan.status, 'booking_aktif')

    def test_masa_booking_berakhir_2359_wita_pada_tanggal_verifikasi(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10, 0))
        self.assertEqual(pesanan.booking_berakhir_pada, wita(2026, 11, 15, 23, 59, 59, 999999))
        lokal = pesanan.booking_berakhir_pada.astimezone(WITA)
        self.assertEqual((lokal.date(), lokal.time().hour, lokal.time().minute), (date(2026, 11, 15), 23, 59))

    def test_tanggal_verifikasi_dihitung_dalam_wita_bukan_utc(self):
        # 17.30 UTC tanggal 15 = 01.30 WITA tanggal 16 → berakhir 23.59 WITA tanggal 16.
        pesanan = self.aktifkan_booking(sekarang=datetime(2026, 11, 15, 17, 30, tzinfo=UTC))
        self.assertEqual(pesanan.booking_berakhir_pada, wita(2026, 11, 16, 23, 59, 59, 999999))

    def test_verifikasi_tepat_pukul_2359_wita_berakhir_di_hari_yang_sama(self):
        # 15.59 UTC = 23.59 WITA tanggal yang sama.
        pesanan = self.aktifkan_booking(sekarang=datetime(2026, 11, 15, 15, 59, tzinfo=UTC))
        self.assertEqual(pesanan.booking_berakhir_pada, wita(2026, 11, 15, 23, 59, 59, 999999))

    def test_akhir_masa_booking_menerima_waktu_dari_zona_mana_pun(self):
        hasil = akhir_masa_booking(datetime(2026, 11, 15, 0, 5, tzinfo=UTC))   # 08.05 WITA
        self.assertEqual(hasil, wita(2026, 11, 15, 23, 59, 59, 999999))

    def test_booking_berlaku_sampai_2359_dan_berakhir_setelahnya(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        self.assertTrue(pesanan.booking_masih_berlaku(wita(2026, 11, 15, 23, 59, 59)))
        self.assertFalse(pesanan.booking_masih_berlaku(wita(2026, 11, 16, 0, 0, 0)))

    def test_tidak_ada_perpanjangan_masa_booking(self):
        pesanan = self.aktifkan_booking()
        asli = pesanan.booking_berakhir_pada
        pesanan.booking_berakhir_pada = asli + timedelta(days=1)
        with self.assertRaisesMessage(ValueError, 'diperpanjang'):
            pesanan.save()
        with self.assertRaisesMessage(ValueError, 'diperpanjang'):
            pesanan.save(update_fields=['booking_berakhir_pada'])
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.booking_berakhir_pada, asli)

    def test_pesanan_kedaluwarsa_tidak_dapat_diaktifkan_kembali(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        self.assertTrue(pesanan.kedaluwarsakan(sekarang=wita(2026, 11, 16, 0, 1)))
        with self.assertRaisesMessage(ValueError, 'tidak diizinkan'):
            pesanan._pindah_status('booking_aktif', self.staf)
        # Booking Fee kedua pun tidak dapat memulai booking baru di pesanan yang sama.
        kedua = self.buat_pembayaran(pesanan)
        with self.assertRaisesMessage(ValueError, 'menunggu Booking Fee'):
            kedua.verifikasi(self.staf, sekarang=wita(2026, 11, 16, 9))

    def test_verifikasi_hanya_oleh_staf(self):
        pembayaran = self.buat_pembayaran(self.buat_pesanan())
        for pelaku in (self.pelanggan, None):
            with self.subTest(pelaku=pelaku):
                with self.assertRaisesMessage(ValueError, 'staf MBG'):
                    pembayaran.verifikasi(pelaku)
        pembayaran.refresh_from_db()
        self.assertEqual(pembayaran.status, 'menunggu_verifikasi')

    def test_verifikasi_ganda_ditolak(self):
        pesanan = self.buat_pesanan()
        pembayaran = self.buat_pembayaran(pesanan)
        pembayaran.verifikasi(self.staf, sekarang=wita(2026, 11, 15, 10))
        with self.assertRaisesMessage(ValueError, 'sudah diproses'):
            pembayaran.verifikasi(self.staf, sekarang=wita(2026, 11, 15, 11))

    def test_nominal_booking_fee_harus_sesuai_snapshot_pesanan(self):
        pesanan = self.buat_pesanan()
        pembayaran = self.buat_pembayaran(pesanan, nominal=Decimal('1000000'))
        with self.assertRaisesMessage(ValueError, 'tidak sesuai'):
            pembayaran.verifikasi(self.staf, sekarang=wita(2026, 11, 15, 10))
        self.produk.refresh_from_db()
        self.assertEqual(self.produk.status_stok, 'ready')

    def test_unit_yang_dikunci_pesanan_lain_tidak_dapat_diverifikasi(self):
        a = self.buat_pesanan()
        # Dua pesanan menunggu untuk satu unit tidak lagi bisa dibuat lewat buat_dari_produk();
        # tetap diuji sebagai pertahanan berlapis terhadap data lama.
        b = self.buat_pesanan_data_lama()
        pembayaran_a = self.buat_pembayaran(a)
        pembayaran_b = self.buat_pembayaran(b)
        pembayaran_a.verifikasi(self.staf, sekarang=wita(2026, 11, 15, 10))
        with self.assertRaisesMessage(ValueError, 'tidak tersedia'):
            pembayaran_b.verifikasi(self.staf, sekarang=wita(2026, 11, 15, 10, 5))
        pembayaran_b.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(pembayaran_b.status, 'menunggu_verifikasi')
        self.assertEqual(b.status, 'menunggu_booking_fee')
        self.assertIsNone(b.booking_berakhir_pada)

    def test_unit_yang_sedang_dikunci_tidak_dapat_dipesan_baru(self):
        self.aktifkan_booking()
        with self.assertRaisesMessage(ValueError, 'tidak tersedia'):
            self.buat_pesanan(pelanggan=self.pelanggan_lain)

    def test_kedaluwarsa_melepas_kunci_dan_unit_tayang_kembali(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        self.assertTrue(pesanan.kedaluwarsakan(sekarang=wita(2026, 11, 16, 0, 0, 1)))
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'kedaluwarsa')
        self.assertEqual(self.produk.status_stok, 'ready')
        self.assertIn(self.produk, produk_tayang())
        riwayat = pesanan.riwayat.order_by('-pk').first()
        self.assertEqual((riwayat.status_dari, riwayat.status_ke), ('booking_aktif', 'kedaluwarsa'))

    def test_kedaluwarsa_tidak_terjadi_sebelum_batas_waktu(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        self.assertFalse(pesanan.kedaluwarsakan(sekarang=wita(2026, 11, 15, 23, 59, 59)))
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')

    def test_kedaluwarsakan_hanya_berlaku_untuk_booking_aktif(self):
        pesanan = self.buat_pesanan()
        self.assertFalse(pesanan.kedaluwarsakan(sekarang=wita(2026, 12, 1)))
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_booking_fee')

    def test_pesanan_yang_sudah_lunas_tidak_dikedaluwarsakan(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        self.bayar(pesanan, 'pelunasan', '196000000', wita(2026, 11, 15, 12))
        self.assertFalse(pesanan.kedaluwarsakan(sekarang=wita(2026, 11, 16, 1)))
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'booking_aktif')


# ═══════════════════════════════════════════════════════════════════
# Pembayaran: DP, pelunasan, penolakan, penyelesaian
# ═══════════════════════════════════════════════════════════════════

class PembayaranPesananTests(PembelianTestCase):
    def test_dp_dan_pelunasan_mengurangi_sisa_hingga_selesai(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        self.bayar(pesanan, 'dp', '50000000', wita(2026, 11, 15, 11))
        self.assertEqual(pesanan.sisa_pembayaran(), Decimal('146000000'))
        self.bayar(pesanan, 'pelunasan', '146000000', wita(2026, 11, 15, 12))
        self.assertEqual(pesanan.total_terverifikasi(), Decimal('200000000'))
        self.assertEqual(pesanan.sisa_pembayaran(), Decimal('0'))
        pesanan.selesaikan(self.staf, catatan='Serah terima di showroom')
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'selesai')
        self.assertEqual(self.produk.status_stok, 'sold')
        self.assertNotIn(self.produk, produk_tayang())

    def test_riwayat_status_tercatat_berurutan(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        self.bayar(pesanan, 'pelunasan', '196000000', wita(2026, 11, 15, 12))
        pesanan.selesaikan(self.staf)
        urutan = list(pesanan.riwayat.order_by('pk').values_list('status_dari', 'status_ke'))
        self.assertEqual(urutan, [
            ('', 'menunggu_booking_fee'),
            ('menunggu_booking_fee', 'booking_aktif'),
            ('booking_aktif', 'selesai'),
        ])
        self.assertEqual(RiwayatPesanan.objects.filter(pesanan=pesanan).count(), 3)

    def test_dp_hanya_setelah_booking_fee_diverifikasi(self):
        pesanan = self.buat_pesanan()
        dp = self.buat_pembayaran(pesanan, 'dp', Decimal('50000000'))
        with self.assertRaisesMessage(ValueError, 'Masa booking'):
            dp.verifikasi(self.staf, sekarang=wita(2026, 11, 15, 10))

    def test_dp_ditolak_setelah_masa_booking_berakhir(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        dp = self.buat_pembayaran(pesanan, 'dp', Decimal('50000000'))
        with self.assertRaisesMessage(ValueError, 'Masa booking'):
            dp.verifikasi(self.staf, sekarang=wita(2026, 11, 16, 0, 0, 0))
        dp.refresh_from_db()
        self.assertEqual(dp.status, 'menunggu_verifikasi')

    def test_dp_tidak_boleh_melebihi_sisa_pembayaran(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        dp = self.buat_pembayaran(pesanan, 'dp', Decimal('196000001'))
        with self.assertRaisesMessage(ValueError, 'melebihi sisa'):
            dp.verifikasi(self.staf, sekarang=wita(2026, 11, 15, 11))

    def test_pelunasan_harus_sama_dengan_sisa(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        pelunasan = self.buat_pembayaran(pesanan, 'pelunasan', Decimal('100000000'))
        with self.assertRaisesMessage(ValueError, 'sama dengan sisa'):
            pelunasan.verifikasi(self.staf, sekarang=wita(2026, 11, 15, 11))

    def test_pesanan_belum_lunas_tidak_dapat_diselesaikan(self):
        pesanan = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        with self.assertRaisesMessage(ValueError, 'belum lunas'):
            pesanan.selesaikan(self.staf)
        with self.assertRaisesMessage(ValueError, 'staf MBG'):
            pesanan.selesaikan(self.pelanggan)

    def test_penolakan_wajib_beralasan_dan_tidak_dihitung_sebagai_pembayaran(self):
        pesanan = self.buat_pesanan()
        pembayaran = self.buat_pembayaran(pesanan)
        with self.assertRaisesMessage(ValueError, 'Alasan penolakan'):
            pembayaran.tolak(self.staf, '  ')
        with self.assertRaisesMessage(ValueError, 'staf MBG'):
            pembayaran.tolak(self.pelanggan, 'Tidak sesuai')
        pembayaran.tolak(self.staf, 'Bukti transfer tidak sesuai')
        pembayaran.refresh_from_db()
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pembayaran.status, 'ditolak')
        self.assertEqual(pembayaran.catatan_verifikasi, 'Bukti transfer tidak sesuai')
        self.assertEqual(pesanan.total_terverifikasi(), Decimal('0'))
        self.assertEqual(pesanan.status, 'menunggu_booking_fee')
        self.assertEqual(self.produk.status_stok, 'ready')
        with self.assertRaisesMessage(ValueError, 'sudah diproses'):
            pembayaran.verifikasi(self.staf)

    def test_validasi_pembayaran_pada_pesanan(self):
        pesanan = self.buat_pesanan()
        with self.assertRaises(ValidationError) as ctx:
            PembayaranPesanan(pesanan=pesanan, jenis='booking_fee', nominal=Decimal('1')).full_clean()
        self.assertIn('nominal', ctx.exception.message_dict)
        with self.assertRaises(ValidationError) as ctx:
            PembayaranPesanan(pesanan=pesanan, jenis='dp', nominal=Decimal('1000')).full_clean()
        self.assertIn('jenis', ctx.exception.message_dict)
        with self.assertRaises(ValidationError) as ctx:
            PembayaranPesanan(pesanan=pesanan, jenis='booking_fee', nominal=Decimal('-5')).full_clean()
        self.assertIn('nominal', ctx.exception.message_dict)
        PembayaranPesanan(
            pesanan=pesanan, jenis='booking_fee', nominal=pesanan.nominal_booking_fee,
        ).full_clean()

    def test_constraint_database_menolak_nominal_nol(self):
        pesanan = self.buat_pesanan()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PembayaranPesanan.objects.create(pesanan=pesanan, jenis='dp', nominal=Decimal('0'))

    def test_pesanan_dengan_pembayaran_tidak_dapat_dihapus(self):
        pesanan = self.buat_pesanan()
        self.buat_pembayaran(pesanan)
        with self.assertRaises(ProtectedError):
            pesanan.delete()


# ═══════════════════════════════════════════════════════════════════
# Pembatalan & refund
# ═══════════════════════════════════════════════════════════════════

class RefundPesananTests(PembelianTestCase):
    def pesanan_aktif(self, sekarang=None):
        return self.aktifkan_booking(sekarang=sekarang or wita(2026, 11, 15, 10))

    def test_pengajuan_mencatat_alasan_pengaju_dan_nominal(self):
        pesanan = self.pesanan_aktif()
        refund = pesanan.ajukan_pembatalan(self.pelanggan, '  Berubah pikiran  ')
        self.assertEqual(refund.status, 'diajukan')
        self.assertEqual(refund.alasan, 'Berubah pikiran')
        self.assertEqual(refund.nominal_diajukan, Decimal('4000000'))
        self.assertEqual(refund.diajukan_oleh, self.pelanggan)
        self.assertIsNone(refund.nominal_disetujui)
        # Belum disetujui admin: pesanan tetap berjalan dan unit tetap terkunci.
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')

    def test_pengajuan_mencakup_seluruh_pembayaran_terverifikasi(self):
        pesanan = self.pesanan_aktif()
        self.bayar(pesanan, 'dp', '50000000', wita(2026, 11, 15, 11))
        refund = pesanan.ajukan_pembatalan(self.pelanggan, 'Gagal pembiayaan')
        self.assertEqual(refund.nominal_diajukan, Decimal('54000000'))

    def test_alasan_wajib_dicatat(self):
        pesanan = self.pesanan_aktif()
        for alasan in ('', '   ', None):
            with self.subTest(alasan=alasan):
                with self.assertRaisesMessage(ValueError, 'Alasan pembatalan wajib'):
                    pesanan.ajukan_pembatalan(self.pelanggan, alasan)
        self.assertEqual(RefundPesanan.objects.count(), 0)
        with self.assertRaises(ValidationError) as ctx:
            RefundPesanan(pesanan=pesanan, alasan=' ', nominal_diajukan=Decimal('1000')).full_clean()
        self.assertIn('alasan', ctx.exception.message_dict)

    def test_hanya_pelanggan_pemilik_atau_staf_yang_dapat_mengajukan(self):
        pesanan = self.pesanan_aktif()
        with self.assertRaisesMessage(ValueError, 'pelanggan pemilik'):
            pesanan.ajukan_pembatalan(self.pelanggan_lain, 'Bukan pemilik')
        refund = pesanan.ajukan_pembatalan(self.staf, 'Atas permintaan pelanggan via WhatsApp')
        self.assertEqual(refund.diajukan_oleh, self.staf)

    def test_tidak_dapat_diajukan_setelah_transaksi_selesai(self):
        pesanan = self.pesanan_aktif()
        self.bayar(pesanan, 'pelunasan', '196000000', wita(2026, 11, 15, 12))
        pesanan.selesaikan(self.staf)
        with self.assertRaisesMessage(ValueError, 'selesai atau dibatalkan'):
            pesanan.ajukan_pembatalan(self.pelanggan, 'Menyesal')

    def test_tidak_dapat_diajukan_dua_kali_selagi_diproses(self):
        pesanan = self.pesanan_aktif()
        pesanan.ajukan_pembatalan(self.pelanggan, 'Alasan pertama')
        with self.assertRaisesMessage(ValueError, 'sedang diproses'):
            pesanan.ajukan_pembatalan(self.pelanggan, 'Alasan kedua')

    def test_tanpa_pembayaran_terverifikasi_tidak_ada_yang_di_refund(self):
        pesanan = self.buat_pesanan()
        with self.assertRaisesMessage(ValueError, 'Belum ada pembayaran terverifikasi'):
            pesanan.ajukan_pembatalan(self.pelanggan, 'Batal')

    def test_refund_wajib_diverifikasi_staf(self):
        refund = self.pesanan_aktif().ajukan_pembatalan(self.pelanggan, 'Batal')
        for pelaku in (self.pelanggan, None):
            with self.subTest(pelaku=pelaku):
                with self.assertRaisesMessage(ValueError, 'staf MBG'):
                    refund.setujui(pelaku)
                with self.assertRaisesMessage(ValueError, 'staf MBG'):
                    refund.tolak(pelaku, 'Ditolak')
        refund.refresh_from_db()
        self.assertEqual(refund.status, 'diajukan')

    def test_persetujuan_membatalkan_pesanan_dan_melepas_unit(self):
        pesanan = self.pesanan_aktif()
        refund = pesanan.ajukan_pembatalan(self.pelanggan, 'Berubah pikiran')
        refund.setujui(self.staf, catatan='Sesuai kebijakan', sekarang=wita(2026, 11, 15, 14))
        refund.refresh_from_db()
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(refund.status, 'disetujui')
        self.assertEqual(refund.nominal_disetujui, Decimal('4000000'))
        self.assertEqual(refund.diverifikasi_oleh, self.staf)
        self.assertEqual(refund.tanggal_verifikasi, wita(2026, 11, 15, 14))
        self.assertEqual(pesanan.status, 'dibatalkan')
        self.assertEqual(self.produk.status_stok, 'ready')
        self.assertIn(self.produk, produk_tayang())
        riwayat = pesanan.riwayat.order_by('-pk').first()
        self.assertEqual((riwayat.status_dari, riwayat.status_ke), ('booking_aktif', 'dibatalkan'))
        self.assertIn('Berubah pikiran', riwayat.catatan)

    def test_nominal_disetujui_dibatasi(self):
        pesanan = self.pesanan_aktif()
        self.bayar(pesanan, 'dp', '50000000', wita(2026, 11, 15, 11))
        refund = pesanan.ajukan_pembatalan(self.pelanggan, 'Batal')
        for nominal in ('0', '-1', '54000001'):
            with self.subTest(nominal=nominal):
                with self.assertRaisesMessage(ValueError, 'Nominal disetujui'):
                    refund.setujui(self.staf, nominal_disetujui=Decimal(nominal))
        refund.setujui(self.staf, nominal_disetujui=Decimal('50000000'))
        self.assertEqual(refund.nominal_disetujui, Decimal('50000000'))

    def test_penolakan_wajib_beralasan_dan_pesanan_tetap_berjalan(self):
        pesanan = self.pesanan_aktif()
        refund = pesanan.ajukan_pembatalan(self.pelanggan, 'Batal')
        with self.assertRaisesMessage(ValueError, 'Alasan penolakan refund'):
            refund.tolak(self.staf, '')
        refund.tolak(self.staf, 'Di luar ketentuan pembatalan')
        refund.refresh_from_db()
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(refund.status, 'ditolak')
        self.assertEqual(refund.catatan_verifikasi, 'Di luar ketentuan pembatalan')
        self.assertEqual(pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')
        # Setelah ditolak, pelanggan dapat mengajukan lagi.
        self.assertEqual(pesanan.ajukan_pembatalan(self.pelanggan, 'Ajukan ulang').status, 'diajukan')

    def test_refund_yang_sudah_diverifikasi_tidak_dapat_diproses_ulang(self):
        refund = self.pesanan_aktif().ajukan_pembatalan(self.pelanggan, 'Batal')
        refund.setujui(self.staf)
        with self.assertRaisesMessage(ValueError, 'sudah diverifikasi'):
            refund.setujui(self.staf)
        with self.assertRaisesMessage(ValueError, 'sudah diverifikasi'):
            refund.tolak(self.staf, 'Terlambat')

    def test_pencatatan_pengembalian_dana(self):
        refund = self.pesanan_aktif().ajukan_pembatalan(self.pelanggan, 'Batal')
        with self.assertRaisesMessage(ValueError, 'sudah disetujui'):
            refund.catat_pengembalian(self.staf, 'TRX-001')
        refund.setujui(self.staf)
        with self.assertRaisesMessage(ValueError, 'Nomor referensi'):
            refund.catat_pengembalian(self.staf, ' ')
        with self.assertRaisesMessage(ValueError, 'staf MBG'):
            refund.catat_pengembalian(self.pelanggan, 'TRX-001')
        refund.catat_pengembalian(
            self.staf, 'TRX-001', catatan='Transfer BCA', sekarang=wita(2026, 11, 16, 9),
        )
        refund.refresh_from_db()
        self.assertEqual(refund.status, 'dikembalikan')
        self.assertEqual(refund.dicatat_oleh, self.staf)
        self.assertEqual(refund.nomor_referensi_pengembalian, 'TRX-001')
        self.assertEqual(refund.tanggal_pengembalian, wita(2026, 11, 16, 9))
        with self.assertRaisesMessage(ValueError, 'sudah disetujui'):
            refund.catat_pengembalian(self.staf, 'TRX-002')

    def test_refund_pesanan_kedaluwarsa_tidak_melepas_kunci_pesanan_lain(self):
        a = self.pesanan_aktif(wita(2026, 11, 15, 10))
        self.assertTrue(a.kedaluwarsakan(sekarang=wita(2026, 11, 16, 0, 1)))
        # Pesanan B memegang kunci unit yang sama pada hari berikutnya.
        b = self.buat_pesanan(pelanggan=self.pelanggan_lain)
        self.buat_pembayaran(b).verifikasi(self.staf, sekarang=wita(2026, 11, 16, 9))
        refund = a.ajukan_pembatalan(self.pelanggan, 'Booking Fee saya hangus')
        refund.setujui(self.staf)
        a.refresh_from_db()
        b.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(a.status, 'dibatalkan')
        self.assertEqual(b.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')

    def test_pengajuan_dan_persetujuan_ditolak_untuk_pesanan_yang_sudah_selesai(self):
        pesanan = self.pesanan_aktif()
        refund = pesanan.ajukan_pembatalan(self.pelanggan, 'Batal')
        self.bayar(pesanan, 'pelunasan', '196000000', wita(2026, 11, 15, 12))
        pesanan.selesaikan(self.staf)
        with self.assertRaisesMessage(ValueError, 'selesai atau dibatalkan'):
            refund.setujui(self.staf)
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'selesai')

    def test_constraint_database_menjaga_nominal_refund(self):
        refund = self.pesanan_aktif().ajukan_pembatalan(self.pelanggan, 'Batal')
        with self.assertRaises(IntegrityError), transaction.atomic():
            RefundPesanan.objects.filter(pk=refund.pk).update(nominal_disetujui=Decimal('99000000'))
        with self.assertRaises(IntegrityError), transaction.atomic():
            RefundPesanan.objects.filter(pk=refund.pk).update(nominal_diajukan=Decimal('0'))

    # ── Pembatalan sebelum ada pembayaran terverifikasi ───────────

    def test_batalkan_tanpa_pembayaran_mencatat_alasan(self):
        pesanan = self.buat_pesanan()
        pesanan.batalkan_tanpa_pembayaran(self.pelanggan, 'Salah memilih unit')
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'dibatalkan')
        self.assertEqual(self.produk.status_stok, 'ready')
        riwayat = pesanan.riwayat.order_by('-pk').first()
        self.assertEqual(riwayat.catatan, 'Salah memilih unit')
        self.assertEqual(riwayat.diubah_oleh, self.pelanggan)

    def test_batalkan_tanpa_pembayaran_validasi(self):
        pesanan = self.buat_pesanan()
        with self.assertRaisesMessage(ValueError, 'Alasan pembatalan wajib'):
            pesanan.batalkan_tanpa_pembayaran(self.pelanggan, '')
        with self.assertRaisesMessage(ValueError, 'pelanggan pemilik'):
            pesanan.batalkan_tanpa_pembayaran(self.pelanggan_lain, 'Bukan pemilik')
        self.buat_pembayaran(pesanan)
        with self.assertRaisesMessage(ValueError, 'menunggu verifikasi'):
            pesanan.batalkan_tanpa_pembayaran(self.pelanggan, 'Batal')
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_booking_fee')

    def test_pesanan_dengan_booking_aktif_wajib_melalui_refund(self):
        pesanan = self.pesanan_aktif()
        with self.assertRaisesMessage(ValueError, 'refund'):
            pesanan.batalkan_tanpa_pembayaran(self.pelanggan, 'Batal')


# ═══════════════════════════════════════════════════════════════════
# Kompatibilitas dengan fitur yang sudah berjalan
# ═══════════════════════════════════════════════════════════════════

class KompatibilitasPembelianTests(PembelianTestCase):
    def test_zona_waktu_proyek_asia_makassar(self):
        self.assertEqual(settings.TIME_ZONE, 'Asia/Makassar')
        self.assertTrue(settings.USE_TZ)

    def test_model_baru_tidak_berelasi_ke_model_booking_cek_unit_dan_c2c(self):
        terlarang = {BookingCekUnit, Transaksi, TitipJual, TransaksiTitipJual}
        for model in (
            PengaturanPembelian, PesananPembelian, PembayaranPesanan, RefundPesanan, RiwayatPesanan,
        ):
            for field in model._meta.get_fields():
                with self.subTest(model=model.__name__, field=field.name):
                    self.assertNotIn(field.related_model, terlarang)

    def test_model_lama_tidak_berelasi_ke_model_baru(self):
        baru = {PengaturanPembelian, PesananPembelian, PembayaranPesanan, RefundPesanan, RiwayatPesanan}
        for model in (BookingCekUnit, Transaksi, TitipJual, TransaksiTitipJual):
            for field in model._meta.get_fields():
                if field.related_model in baru:
                    # Relasi balik dari Produk/Pengguna saja yang diperbolehkan; model lama tidak.
                    self.fail(f'{model.__name__}.{field.name} berelasi ke model pembelian')

    def test_booking_cek_unit_dan_transaksi_lama_tetap_dapat_dipakai(self):
        booking = BookingCekUnit.objects.create(
            produk=self.produk, nama_pemesan='Budi', no_whatsapp='081234567890',
            email='budi@example.test', tanggal_cek_unit=date(2026, 11, 20),
            jam_cek_unit=time(10, 0),
        )
        transaksi = Transaksi.objects.create(no_order='TRX-MBG-20261115-001', booking=booking)
        self.assertEqual(transaksi.nominal_dp, Decimal('2500000'))
        self.assertEqual(PesananPembelian.objects.count(), 0)
        self.produk.refresh_from_db()
        self.assertEqual(self.produk.status_stok, 'ready')

    def test_produk_tayang_hanya_memuat_unit_ready(self):
        lain = buat_produk()
        self.aktifkan_booking()
        tayang = list(produk_tayang())
        self.assertNotIn(self.produk, tayang)
        self.assertIn(lain, tayang)

    def test_migration_sinkron_dengan_model(self):
        try:
            call_command(
                'makemigrations', '--check', '--dry-run', verbosity=0,
                stdout=StringIO(), stderr=StringIO(),
            )
        except SystemExit:
            self.fail('Perubahan model belum dibuatkan migration.')


class KonfirmasiPembayaranFlowTests(PembelianTestCase):
    def mulai_checkout(
        self,
        jenis_pembelian='booking',
        saluran_pembayaran='transfer',
        metode_pembayaran='cash',
    ):
        self.client.force_login(self.pelanggan)
        checkout_url = (
            f"{reverse('checkout_pembelian', args=[self.produk.pk])}"
            f"?jenis_pembelian={jenis_pembelian}"
        )
        response = self.client.post(checkout_url, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '081234567890',
            'email': 'budi@example.test',
            'metode_pembayaran': metode_pembayaran,
            'saluran_pembayaran': saluran_pembayaran,
            'opsi_penyerahan': 'showroom',
            'alamat_pengantaran': '',
        })
        self.assertRedirects(
            response,
            reverse('konfirmasi_pembelian', args=[self.produk.pk]),
        )
        return response

    def konfirmasi_pesanan(self):
        response = self.client.get(reverse('konfirmasi_pembelian', args=[self.produk.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PesananPembelian.objects.count(), 0)
        response = self.client.post(reverse('konfirmasi_pembelian', args=[self.produk.pk]))
        self.assertEqual(response.status_code, 302)
        return PesananPembelian.objects.get()

    def test_pilihan_transfer_dibuat_setelah_konfirmasi_dan_nominal_diambil_dari_server(self):
        self.mulai_checkout()
        pesanan = self.konfirmasi_pesanan()
        self.assertEqual(pesanan.saluran_pembayaran, 'transfer')
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=pesanan).exists())
        self.assertEqual(pesanan.produk.status_stok, 'ready')

        response = self.client.get(reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan]))
        self.assertContains(response, 'rekening tujuan resmi')
        response = self.client.post(
            reverse('konfirmasi_transfer_pesanan', args=[pesanan.nomor_pesanan]),
            {
                'nomor_referensi': 'TRF-12345',
                'nominal': '1',
                'bukti_transfer': bukti_transfer_pdf(),
            },
        )
        self.assertRedirects(response, reverse('pesanan_detail', args=[pesanan.nomor_pesanan]))

        pembayaran = PembayaranPesanan.objects.get(pesanan=pesanan)
        self.assertEqual(pembayaran.nominal, pesanan.nominal_booking_fee)
        self.assertEqual(pembayaran.status, 'menunggu_verifikasi')
        self.assertEqual(pembayaran.saluran, 'transfer')
        self.assertEqual(pembayaran.nomor_referensi, 'TRF-12345')
        self.assertTrue(pembayaran.bukti_transfer)
        self.assertTrue(pembayaran.bukti_transfer.storage.exists(pembayaran.bukti_transfer.name))
        self.bersihkan_bukti_transfer(pembayaran)

        self.client.force_login(self.staf)
        self.assertContains(
            self.client.get(reverse('admin_pesanan_pembelian')),
            'Lihat bukti transfer',
        )

        pembayaran.verifikasi(self.staf)
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')

    def test_simulasi_kredit_menggunakan_dp_20_persen_dan_angsuran_tahunan_5_persen(self):
        self.client.force_login(self.pelanggan)
        checkout_url = (
            f"{reverse('checkout_pembelian', args=[self.produk.pk])}"
            '?jenis_pembelian=booking'
        )
        checkout = self.client.get(checkout_url)
        rincian = checkout.context['rincian_kredit']
        self.assertEqual(rincian['nominal_dp'], Decimal('40000000'))
        self.assertEqual(rincian['nominal_dp_tambahan'], Decimal('36000000'))
        self.assertEqual(rincian['nominal_angsuran_tahunan'], Decimal('10000000'))
        self.assertEqual(rincian['tenor_tahun'], 16)
        self.assertEqual(rincian['nominal_angsuran_terakhir'], Decimal('10000000'))

        self.mulai_checkout(metode_pembayaran='kredit')
        konfirmasi = self.client.get(
            reverse('konfirmasi_pembelian', args=[self.produk.pk])
        )
        self.assertContains(konfirmasi, 'Simulasi tanpa bunga tambahan')
        self.assertEqual(
            konfirmasi.context['rincian_kredit']['nominal_dp_tambahan'],
            Decimal('36000000'),
        )
        pesanan = self.konfirmasi_pesanan()
        self.assertEqual(pesanan.metode_pembayaran, 'kredit')
        sukses = self.client.get(
            reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan])
        )
        self.assertContains(sukses, 'Simulasi Kredit tanpa bunga tambahan')
        detail = self.client.get(
            reverse('pesanan_detail', args=[pesanan.nomor_pesanan])
        )
        self.assertContains(detail, 'Simulasi Kredit')

    def test_transfer_beli_langsung_menunggu_admin_dan_mengunci_unit_setelah_disahkan(self):
        self.mulai_checkout(jenis_pembelian='beli_langsung')
        pesanan = self.konfirmasi_pesanan()
        self.assertEqual(pesanan.jenis_pembelian, 'beli_langsung')

        response = self.client.post(
            reverse('konfirmasi_transfer_pesanan', args=[pesanan.nomor_pesanan]),
            {
                'nomor_referensi': 'TRF-DIRECT-123',
                'bukti_transfer': bukti_transfer_pdf(),
            },
        )
        self.assertRedirects(response, reverse('pesanan_detail', args=[pesanan.nomor_pesanan]))
        pembayaran = PembayaranPesanan.objects.get(pesanan=pesanan)
        self.assertEqual(pembayaran.jenis, 'pelunasan')
        self.assertEqual(pembayaran.nominal, pesanan.harga_kendaraan)
        self.bersihkan_bukti_transfer(pembayaran)

        pembayaran.verifikasi(self.staf)
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')
        self.assertEqual(pesanan.sisa_pembayaran(), Decimal('0'))

    def test_konfirmasi_transfer_mewajibkan_bukti_sebelum_mencatat_pembayaran(self):
        self.mulai_checkout()
        pesanan = self.konfirmasi_pesanan()

        response = self.client.post(
            reverse('konfirmasi_transfer_pesanan', args=[pesanan.nomor_pesanan]),
            {'nomor_referensi': 'TRF-TANPA-BUKTI'},
        )

        self.assertRedirects(response, reverse('pesanan_detail', args=[pesanan.nomor_pesanan]))
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=pesanan).exists())
        self.assertEqual(pesanan.produk.status_stok, 'ready')

    def test_pemilik_saja_dapat_mengirim_konfirmasi_transfer(self):
        self.mulai_checkout()
        pesanan = self.konfirmasi_pesanan()
        self.client.force_login(self.pelanggan_lain)
        response = self.client.post(
            reverse('konfirmasi_transfer_pesanan', args=[pesanan.nomor_pesanan]),
            {'nomor_referensi': 'TRF-UNAUTHORIZED'},
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=pesanan).exists())

    def test_admin_dapat_membatalkan_booking_tanpa_pembayaran_dan_melepas_unit(self):
        self.mulai_checkout()
        pesanan = self.konfirmasi_pesanan()
        self.client.force_login(self.staf)
        response = self.client.post(
            reverse('admin_batalkan_pesanan_pembelian', args=[pesanan.pk]),
            {'alasan': 'Pelanggan tidak melanjutkan transaksi showroom.'},
        )
        self.assertRedirects(response, reverse('admin_pesanan_pembelian'))
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'dibatalkan')
        self.assertEqual(self.produk.status_stok, 'ready')

    def test_admin_membatalkan_booking_terbayar_melalui_refund_dan_melepas_unit(self):
        self.mulai_checkout()
        pesanan = self.konfirmasi_pesanan()
        self.buat_pembayaran(pesanan).verifikasi(self.staf)
        self.client.force_login(self.staf)

        response = self.client.post(
            reverse('admin_batalkan_pesanan_pembelian', args=[pesanan.pk]),
            {'alasan': 'Transaksi showroom tidak dilanjutkan.'},
        )
        self.assertRedirects(response, reverse('admin_pesanan_pembelian'))
        refund = pesanan.refund.get()
        self.assertEqual(refund.status, 'diajukan')
        self.produk.refresh_from_db()
        self.assertEqual(self.produk.status_stok, 'in_review')

        response = self.client.post(
            reverse('admin_setujui_refund_pesanan', args=[refund.pk]),
            {'catatan': 'Pengembalian disetujui admin.'},
        )
        self.assertRedirects(response, reverse('admin_pesanan_pembelian'))
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'dibatalkan')
        self.assertEqual(self.produk.status_stok, 'ready')
