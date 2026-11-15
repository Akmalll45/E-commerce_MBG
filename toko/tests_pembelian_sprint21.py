"""
Sprint 2.1 — regresi untuk tiga temuan implementasi Sprint 2.

1. Double booking : satu kendaraan hanya boleh punya satu pesanan aktif.
2. Rekening BCA   : tidak ada nomor rekening/instruksi transfer manual di pesanan_berhasil.html.
3. WhatsApp       : `pengaturan.whatsapp` (PengaturanWebsite) tersedia di halaman pesanan dan
                    tidak tertimpa oleh `PengaturanPembelian` di halaman checkout/konfirmasi.
"""
import threading
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse

from . import views
from .models import PengaturanPembelian, PengaturanWebsite, PesananPembelian
from .tests_pembelian import PembelianTestCase, buat_produk, wita

NOMOR_ADMIN = '6285242000999'
DATA_PESANAN = dict(
    nama_pelanggan='Budi Santoso', no_whatsapp='0812-3456-7890', metode_pembayaran='cash',
)


# ═══════════════════════════════════════════════════════════════════
# 1. Double booking — aturan di model
# ═══════════════════════════════════════════════════════════════════

class DoubleBookingModelTests(PembelianTestCase):
    def test_pelanggan_lain_tidak_dapat_memesan_unit_yang_sudah_punya_pesanan_aktif(self):
        self.buat_pesanan()
        with self.assertRaisesMessage(ValueError, 'sudah memiliki pesanan aktif'):
            self.buat_pesanan(pelanggan=self.pelanggan_lain)

    def test_pelanggan_yang_sama_tidak_dapat_memesan_unit_yang_sama_dua_kali(self):
        self.buat_pesanan()
        with self.assertRaisesMessage(ValueError, 'sudah memiliki pesanan aktif'):
            self.buat_pesanan()

    def test_penolakan_tidak_meninggalkan_pesanan_atau_riwayat_baru(self):
        pertama = self.buat_pesanan()
        with self.assertRaises(ValueError):
            self.buat_pesanan(pelanggan=self.pelanggan_lain)
        self.assertEqual(PesananPembelian.objects.filter(produk=self.produk).count(), 1)
        self.assertEqual(pertama.riwayat.count(), 1)

    def test_unit_lain_tetap_dapat_dipesan(self):
        self.buat_pesanan()
        lain = self.buat_pesanan(produk=buat_produk(), pelanggan=self.pelanggan_lain)
        self.assertEqual(lain.status, 'menunggu_booking_fee')

    def test_booking_aktif_tetap_menolak_pesanan_baru(self):
        self.aktifkan_booking()
        with self.assertRaisesMessage(ValueError, 'tidak tersedia'):
            self.buat_pesanan(pelanggan=self.pelanggan_lain)

    def test_unit_dapat_dipesan_lagi_setelah_pesanan_dibatalkan(self):
        pertama = self.buat_pesanan()
        pertama.batalkan_tanpa_pembayaran(self.pelanggan, 'Berubah pikiran')
        kedua = self.buat_pesanan(pelanggan=self.pelanggan_lain)
        self.assertEqual(kedua.status, 'menunggu_booking_fee')

    def test_unit_dapat_dipesan_lagi_setelah_booking_kedaluwarsa(self):
        pertama = self.aktifkan_booking(sekarang=wita(2026, 11, 15, 10))
        self.assertTrue(pertama.kedaluwarsakan(sekarang=wita(2026, 11, 16, 0, 1)))
        kedua = self.buat_pesanan(pelanggan=self.pelanggan_lain)
        self.assertEqual(kedua.status, 'menunggu_booking_fee')

    def test_hanya_status_menunggu_dan_booking_aktif_dianggap_aktif(self):
        self.assertEqual(
            set(PesananPembelian.STATUS_AKTIF), {'menunggu_booking_fee', 'booking_aktif'},
        )
        pesanan = self.buat_pesanan()
        self.assertEqual(PesananPembelian.pesanan_aktif_unit(self.produk), pesanan)
        pesanan.batalkan_tanpa_pembayaran(self.pelanggan, 'Batal')
        self.assertIsNone(PesananPembelian.pesanan_aktif_unit(self.produk))

    def test_verifikasi_tetap_menolak_pesanan_kedua_dari_data_lama(self):
        """Pertahanan berlapis: duplikat warisan sebelum Sprint 2.1 tidak bisa mengunci unit dua kali."""
        a = self.buat_pesanan()
        b = self.buat_pesanan_data_lama()
        self.buat_pembayaran(a).verifikasi(self.staf, sekarang=wita(2026, 11, 15, 10))
        with self.assertRaisesMessage(ValueError, 'tidak tersedia'):
            self.buat_pembayaran(b).verifikasi(self.staf, sekarang=wita(2026, 11, 15, 10, 5))
        b.refresh_from_db()
        self.assertEqual(b.status, 'menunggu_booking_fee')


# ═══════════════════════════════════════════════════════════════════
# 1b. Double booking — balapan sungguhan antar-koneksi database
# ═══════════════════════════════════════════════════════════════════

@skipUnlessDBFeature('has_select_for_update')
class DoubleBookingConcurrencyTests(TransactionTestCase):
    """
    Setiap thread memakai koneksi MySQL sendiri dan transaksi sendiri, jadi row-lock InnoDB
    benar-benar diuji (TestCase biasa membungkus semuanya dalam satu transaksi/koneksi).
    """
    JUMLAH_PEMESAN = 8
    PUTARAN = 3

    def setUp(self):
        pengguna = get_user_model().objects
        self.pemesan = [
            pengguna.create_user(f'pemesan{i}', f'pemesan{i}@example.test', 'Password123!')
            for i in range(self.JUMLAH_PEMESAN)
        ]
        PengaturanPembelian.ambil()   # dibuat di luar thread agar tidak ikut balapan

    def test_pemesan_serentak_untuk_satu_unit_hanya_satu_yang_berhasil(self):
        for putaran in range(self.PUTARAN):
            with self.subTest(putaran=putaran):
                produk = buat_produk()
                gerbang = threading.Barrier(self.JUMLAH_PEMESAN)
                hasil = []

                def pesan(pelanggan):
                    try:
                        gerbang.wait(timeout=15)
                        PesananPembelian.buat_dari_produk(
                            produk=produk, pelanggan=pelanggan, **DATA_PESANAN,
                        )
                        hasil.append('berhasil')
                    except ValueError:
                        hasil.append('ditolak')
                    except Exception as galat:   # mis. deadlock MySQL — harus terlihat di test
                        hasil.append(f'galat:{type(galat).__name__}:{galat}')
                    finally:
                        connection.close()

                thread = [threading.Thread(target=pesan, args=(p,)) for p in self.pemesan]
                for t in thread:
                    t.start()
                for t in thread:
                    t.join(timeout=60)

                self.assertEqual(hasil.count('berhasil'), 1, hasil)
                self.assertEqual(hasil.count('ditolak'), self.JUMLAH_PEMESAN - 1, hasil)
                self.assertEqual(
                    PesananPembelian.objects.filter(
                        produk=produk, status__in=PesananPembelian.STATUS_AKTIF,
                    ).count(),
                    1,
                )


# ═══════════════════════════════════════════════════════════════════
# 1c. Double booking — alur checkout & konfirmasi di view
# ═══════════════════════════════════════════════════════════════════

class DoubleBookingViewTests(TestCase):
    def setUp(self):
        pengguna = get_user_model().objects
        self.budi = pengguna.create_user(
            'budi', 'budi@example.test', 'Password123!', first_name='Budi',
            no_whatsapp='081234567890',
        )
        self.ani = pengguna.create_user(
            'ani', 'ani@example.test', 'Password123!', first_name='Ani',
            no_whatsapp='081298765432',
        )
        pengaturan = PengaturanPembelian.ambil()
        pengaturan.persentase_booking_fee = Decimal('2.00')
        pengaturan.save()
        self.produk = buat_produk()
        self.client_budi = Client()
        self.client_budi.login(username='budi', password='Password123!')
        self.client_ani = Client()
        self.client_ani.login(username='ani', password='Password123!')
        self.url_checkout = reverse('checkout_pembelian', args=[self.produk.id])
        self.url_konfirmasi = reverse('konfirmasi_pembelian', args=[self.produk.id])

    def isi_checkout(self, klien):
        return klien.post(self.url_checkout, {
            'nama_pelanggan': 'Pemesan Uji', 'no_whatsapp': '081234567890',
            'email': 'uji@example.test', 'metode_pembayaran': 'cash',
            'opsi_penyerahan': 'showroom',
        })

    def pesan_lewat_alur(self, klien):
        self.isi_checkout(klien)
        return klien.post(self.url_konfirmasi)

    def pesan_penuh(self):
        return PesananPembelian.objects.filter(produk=self.produk)

    def test_alur_normal_tetap_membuat_satu_pesanan(self):
        resp = self.pesan_lewat_alur(self.client_budi)
        pesanan = self.pesan_penuh().get()
        self.assertRedirects(
            resp, reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan]),
        )
        self.assertEqual((pesanan.pelanggan, pesanan.status), (self.budi, 'menunggu_booking_fee'))

    def test_checkout_pelanggan_lain_diarahkan_ke_detail_unit(self):
        self.pesan_lewat_alur(self.client_budi)
        resp = self.client_ani.get(self.url_checkout)
        self.assertRedirects(
            resp, reverse('detail_produk', args=[self.produk.id]),
        )
        pesan = [str(m) for m in get_messages(resp.wsgi_request)]
        self.assertTrue(any('pelanggan lain' in m for m in pesan), pesan)
        self.assertEqual(self.pesan_penuh().count(), 1)

    def test_checkout_pemilik_pesanan_diarahkan_ke_pesanan_miliknya(self):
        self.pesan_lewat_alur(self.client_budi)
        pesanan = self.pesan_penuh().get()
        resp = self.client_budi.get(self.url_checkout)
        self.assertRedirects(
            resp, reverse('pesanan_detail', args=[pesanan.nomor_pesanan]),
        )

    def test_konfirmasi_yang_tertunda_ditolak_jika_unit_keburu_dipesan_orang_lain(self):
        # Ani sudah mengisi checkout, lalu Budi menyelesaikan pesanannya lebih dulu.
        self.isi_checkout(self.client_ani)
        self.pesan_lewat_alur(self.client_budi)
        resp = self.client_ani.post(self.url_konfirmasi)
        self.assertRedirects(
            resp, reverse('detail_produk', args=[self.produk.id]),
        )
        self.assertEqual(self.pesan_penuh().count(), 1)
        self.assertEqual(self.pesan_penuh().get().pelanggan, self.budi)

    def test_penjaga_model_bekerja_saat_pemeriksaan_awal_view_kalah_balapan(self):
        """
        Simulasikan jendela balapan: pemeriksaan awal di view lolos (belum ada pesanan), lalu
        pesanan lain di-commit sebelum transaksi pembuatan. Penjaga di model harus menolak.
        """
        self.isi_checkout(self.client_ani)
        asli = views._alihkan_jika_ada_pesanan_aktif
        panggilan = []

        def pemeriksaan_awal_lolos(request, produk):
            panggilan.append(1)
            if len(panggilan) == 1:
                # Pesanan Budi ter-commit tepat setelah pemeriksaan awal Ani.
                PesananPembelian.buat_dari_produk(
                    produk=produk, pelanggan=self.budi, **DATA_PESANAN,
                )
                return None
            return asli(request, produk)

        with mock.patch.object(views, '_alihkan_jika_ada_pesanan_aktif', pemeriksaan_awal_lolos):
            resp = self.client_ani.post(self.url_konfirmasi)

        self.assertRedirects(
            resp, reverse('detail_produk', args=[self.produk.id]),
        )
        self.assertEqual(self.pesan_penuh().count(), 1)
        self.assertEqual(self.pesan_penuh().get().pelanggan, self.budi)

    def test_unit_bebas_lagi_setelah_pesanan_dibatalkan(self):
        self.pesan_lewat_alur(self.client_budi)
        self.pesan_penuh().get().batalkan_tanpa_pembayaran(self.budi, 'Batal')
        resp = self.client_ani.get(self.url_checkout)
        self.assertEqual(resp.status_code, 200)


# ═══════════════════════════════════════════════════════════════════
# 2 & 3. Template pesanan: tanpa rekening hardcode; WhatsApp memakai pengaturan situs
# ═══════════════════════════════════════════════════════════════════

class TemplatePesananTests(TestCase):
    TEKS_REKENING_DILARANG = (
        '789-012-3456', 'Bank BCA', 'BCA', 'Transfer Manual', 'a.n. PT',
        'bukti transfer', 'bank-card', 'Rekening',
    )

    def setUp(self):
        PengaturanWebsite.objects.update_or_create(pk=1, defaults={'whatsapp': NOMOR_ADMIN})
        pengguna = get_user_model().objects
        self.budi = pengguna.create_user(
            'budi', 'budi@example.test', 'Password123!', first_name='Budi',
            no_whatsapp='081234567890',
        )
        self.produk = buat_produk()
        self.pesanan = PesananPembelian.buat_dari_produk(
            produk=self.produk, pelanggan=self.budi, **DATA_PESANAN,
        )
        self.klien = Client()
        self.klien.login(username='budi', password='Password123!')

    def buka(self, nama, *args):
        resp = self.klien.get(reverse(nama, args=args))
        self.assertEqual(resp.status_code, 200)
        return resp

    # ── 2. Rekening BCA & instruksi transfer manual ────────────────

    def test_pesanan_berhasil_tidak_memuat_rekening_atau_instruksi_transfer_manual(self):
        html = self.buka('pesanan_berhasil', self.pesanan.nomor_pesanan).content.decode()
        for teks in self.TEKS_REKENING_DILARANG:
            with self.subTest(teks=teks):
                self.assertNotIn(teks, html)

    def test_pesanan_berhasil_tetap_menampilkan_tagihan_dan_menyebut_midtrans(self):
        resp = self.buka('pesanan_berhasil', self.pesanan.nomor_pesanan)
        self.assertContains(resp, self.pesanan.nomor_pesanan)
        self.assertContains(resp, 'Tagihan Booking Fee')
        self.assertContains(resp, 'Midtrans')

    def test_pesan_whatsapp_tidak_lagi_meminta_lampiran_bukti_transfer(self):
        html = self.buka('pesanan_berhasil', self.pesanan.nomor_pesanan).content.decode()
        self.assertNotIn('lampirkan', html.lower())
        self.assertNotIn('Konfirmasi Pembayaran via WhatsApp', html)

    # ── 3. pengaturan.whatsapp tersedia di halaman pesanan ─────────

    def test_pesanan_berhasil_memakai_nomor_whatsapp_pengaturan_situs(self):
        resp = self.buka('pesanan_berhasil', self.pesanan.nomor_pesanan)
        self.assertIsInstance(resp.context['pengaturan'], PengaturanWebsite)
        self.assertEqual(resp.context['pengaturan'].whatsapp, NOMOR_ADMIN)
        self.assertContains(resp, f'https://wa.me/{NOMOR_ADMIN}?text=')
        self.assertContains(resp, self.pesanan.nomor_pesanan)

    def test_pesanan_detail_memakai_nomor_whatsapp_pengaturan_situs(self):
        resp = self.buka('pesanan_detail', self.pesanan.nomor_pesanan)
        self.assertIsInstance(resp.context['pengaturan'], PengaturanWebsite)
        self.assertEqual(resp.context['pengaturan'].whatsapp, NOMOR_ADMIN)
        self.assertContains(resp, f'https://wa.me/{NOMOR_ADMIN}?text=')

    def test_tautan_whatsapp_tidak_pernah_kosong_di_seluruh_alur_pembelian(self):
        """Akar masalah: view checkout/konfirmasi dulu menimpa `pengaturan` dengan PengaturanPembelian."""
        produk = buat_produk()
        url_checkout = reverse('checkout_pembelian', args=[produk.id])
        self.klien.post(url_checkout, {
            'nama_pelanggan': 'Budi', 'no_whatsapp': '081234567890', 'email': '',
            'metode_pembayaran': 'cash', 'opsi_penyerahan': 'showroom',
        })
        halaman = {
            'checkout': self.buka('checkout_pembelian', produk.id),
            'konfirmasi': self.buka('konfirmasi_pembelian', produk.id),
            'berhasil': self.buka('pesanan_berhasil', self.pesanan.nomor_pesanan),
            'detail': self.buka('pesanan_detail', self.pesanan.nomor_pesanan),
        }
        for nama, resp in halaman.items():
            with self.subTest(halaman=nama):
                html = resp.content.decode()
                self.assertIsInstance(resp.context['pengaturan'], PengaturanWebsite)
                self.assertNotIn('https://wa.me/"', html)
                self.assertNotIn('https://wa.me/?text', html)
                self.assertIn(f'https://wa.me/{NOMOR_ADMIN}', html)

    def test_checkout_dan_konfirmasi_menyediakan_pengaturan_pembelian_terpisah(self):
        produk = buat_produk()
        self.klien.post(reverse('checkout_pembelian', args=[produk.id]), {
            'nama_pelanggan': 'Budi', 'no_whatsapp': '081234567890', 'email': '',
            'metode_pembayaran': 'cash', 'opsi_penyerahan': 'showroom',
        })
        for nama in ('checkout_pembelian', 'konfirmasi_pembelian'):
            with self.subTest(view=nama):
                resp = self.buka(nama, produk.id)
                self.assertIsInstance(resp.context['pengaturan_pembelian'], PengaturanPembelian)
