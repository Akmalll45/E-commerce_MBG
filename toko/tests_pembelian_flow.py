import itertools
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse

from toko.models import (
    Produk, PengaturanPembelian, PesananPembelian, RiwayatPesanan,
)

_urutan = itertools.count(100)


def buat_produk(**kw):
    data = dict(
        sku=f'PB-FLOW-{next(_urutan):04d}',
        nama='Honda CR-V 1.5 Turbo Prestige 2022',
        merek='Honda',
        kategori='SUV',
        tahun=2022,
        transmisi='AT',
        jarak_tempuh=25000,
        warna='Hitam Metalik',
        plat_nomor='DD 888 XX',
        harga=Decimal('400000000'),
        tipe_penjual='B2C',
        status_stok='ready',
        gambar_utama='mobil/crv.jpg',
        deskripsi_singkat='SUV mewah siap pakai',
        deskripsi_detail='Kondisi sangat istimewa, servis berkala lengkap',
    )
    data.update(kw)
    return Produk.objects.create(**data)


class CustomerPurchaseFlowTests(TestCase):
    def setUp(self):
        pengguna = get_user_model().objects
        self.pelanggan = pengguna.create_user(
            username='budi_pembeli',
            email='budi@example.test',
            password='Password123!',
            first_name='Budi',
            last_name='Santoso',
            no_whatsapp='081234567890',
            alamat='Jl. Boulevard Panakkukang No. 10, Makassar',
        )
        self.pelanggan_lain = pengguna.create_user(
            username='ani_pembeli',
            email='ani@example.test',
            password='Password123!',
            first_name='Ani',
            last_name='Wijaya',
            no_whatsapp='081298765432',
        )
        self.staf = pengguna.create_user(
            username='staf_mbg',
            email='staf@mbg.test',
            password='Password123!',
            is_staff=True,
        )

        pengaturan = PengaturanPembelian.ambil()
        pengaturan.persentase_booking_fee = Decimal('2.00')
        pengaturan.save()

        self.produk_ready = buat_produk()
        self.client_auth = Client()
        self.client_auth.login(username='budi_pembeli', password='Password123!')

        self.client_anon = Client()

    # ── 1. Tombol Beli Mobil pada Detail Kendaraan ─────────────────

    def test_tombol_beli_mobil_tampil_pada_unit_b2c_ready(self):
        resp = self.client_auth.get(reverse('detail_produk', args=[self.produk_ready.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Beli Mobil Sekarang')
        self.assertContains(resp, reverse('checkout_pembelian', args=[self.produk_ready.id]))

    def test_tombol_beli_menunjukkan_status_saat_unit_in_review(self):
        self.produk_ready.status_stok = 'in_review'
        self.produk_ready.save()
        resp = self.client_auth.get(reverse('detail_produk', args=[self.produk_ready.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Unit Sedang Di-booking')
        self.assertNotContains(resp, 'Beli Mobil Sekarang')

    def test_unit_c2c_tidak_menampilkan_tombol_beli_langsung(self):
        produk_c2c = buat_produk(tipe_penjual='C2C')
        resp = self.client_auth.get(reverse('detail_produk', args=[produk_c2c.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, 'Beli Mobil Sekarang')
        self.assertContains(resp, 'Booking Cek Unit (O2O)')

    # ── 2. Akses Checkout & Otentikasi ─────────────────────────────

    def test_checkout_wajib_login(self):
        url = reverse('checkout_pembelian', args=[self.produk_ready.id])
        resp = self.client_anon.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_checkout_menolak_unit_non_b2c(self):
        c2c = buat_produk(tipe_penjual='C2C')
        url = reverse('checkout_pembelian', args=[c2c.id])
        resp = self.client_auth.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('detail_produk', args=[c2c.id]))

    def test_checkout_menolak_unit_tidak_ready(self):
        self.produk_ready.status_stok = 'sold'
        self.produk_ready.save()
        url = reverse('checkout_pembelian', args=[self.produk_ready.id])
        resp = self.client_auth.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertRedirects(resp, reverse('detail_produk', args=[self.produk_ready.id]))

    # ── 3. Formulir & Perhitungan Booking Fee ─────────────────────

    def test_checkout_prefill_identitas_pelanggan_dan_hitung_booking_fee(self):
        url = reverse('checkout_pembelian', args=[self.produk_ready.id])
        resp = self.client_auth.get(url)
        self.assertEqual(resp.status_code, 200)
        # Prefill nama dan whatsapp
        self.assertContains(resp, 'Budi Santoso')
        self.assertContains(resp, '081234567890')
        # Booking fee 2% dari 400 jt = 8.000.000
        self.assertEqual(resp.context['nominal_booking_fee'], Decimal('8000000'))
        self.assertEqual(resp.context['persentase_booking_fee'], Decimal('2.00'))
        self.assertEqual(resp.context['sisa_estimasi'], Decimal('392000000'))
        self.assertContains(resp, '8,000,000')

    def test_validasi_checkout_towing_wajib_alamat(self):
        url = reverse('checkout_pembelian', args=[self.produk_ready.id])
        resp = self.client_auth.post(url, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '081234567890',
            'email': 'budi@example.test',
            'metode_pembayaran': 'cash',
            'opsi_penyerahan': 'towing',
            'alamat_pengantaran': '',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFormError(resp, 'form', 'alamat_pengantaran', 'Alamat pengantaran wajib diisi jika memilih pengantaran towing.')

    # ── 4. Alur Lengkap: Checkout -> Konfirmasi -> Buat Pesanan ───

    def test_alur_pembelian_berhasil(self):
        # Step 1: POST data checkout yang valid
        url_checkout = reverse('checkout_pembelian', args=[self.produk_ready.id])
        resp_checkout = self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '0812-3456-7890',
            'email': 'budi@example.test',
            'metode_pembayaran': 'cash',
            'opsi_penyerahan': 'showroom',
            'alamat_pengantaran': '',
        })
        self.assertRedirects(resp_checkout, reverse('konfirmasi_pembelian', args=[self.produk_ready.id]))

        # Step 2: Halaman Konfirmasi (GET)
        url_konfirmasi = reverse('konfirmasi_pembelian', args=[self.produk_ready.id])
        resp_konfirmasi = self.client_auth.get(url_konfirmasi)
        self.assertEqual(resp_konfirmasi.status_code, 200)
        self.assertContains(resp_konfirmasi, 'Budi Santoso')
        self.assertContains(resp_konfirmasi, 'Honda CR-V 1.5 Turbo Prestige 2022')
        self.assertContains(resp_konfirmasi, '8,000,000')
        self.assertContains(resp_konfirmasi, 'Ambil di Showroom MBG Makassar')

        # Step 3: POST Konfirmasi untuk membuat pesanan
        resp_submit = self.client_auth.post(url_konfirmasi)
        self.assertEqual(resp_submit.status_code, 302)

        # Verifikasi PesananPembelian tersimpan di database
        pesanan = PesananPembelian.objects.get(produk=self.produk_ready)
        self.assertEqual(pesanan.pelanggan, self.pelanggan)
        self.assertEqual(pesanan.nama_pelanggan, 'Budi Santoso')
        self.assertEqual(pesanan.no_whatsapp, '6281234567890')
        self.assertEqual(pesanan.harga_kendaraan, Decimal('400000000'))
        self.assertEqual(pesanan.persentase_booking_fee, Decimal('2.00'))
        self.assertEqual(pesanan.nominal_booking_fee, Decimal('8000000'))
        self.assertEqual(pesanan.metode_pembayaran, 'cash')
        self.assertEqual(pesanan.opsi_penyerahan, 'showroom')
        self.assertEqual(pesanan.status, 'menunggu_booking_fee')

        # Verifikasi RiwayatPesanan dibuat otomatis
        self.assertTrue(pesanan.riwayat.filter(status_ke='menunggu_booking_fee').exists())

        # Aturan wajib: Unit BELUM dikunci sebelum Booking Fee diverifikasi admin
        self.produk_ready.refresh_from_db()
        self.assertEqual(self.produk_ready.status_stok, 'ready')

        # Redirect ke halaman sukses
        self.assertRedirects(resp_submit, reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan]))

        # Step 4: Halaman Pesanan Berhasil
        resp_sukses = self.client_auth.get(reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan]))
        self.assertEqual(resp_sukses.status_code, 200)
        self.assertContains(resp_sukses, pesanan.nomor_pesanan)
        self.assertContains(resp_sukses, 'Pesanan Pembelian Berhasil Dibuat')
        self.assertContains(resp_sukses, '8,000,000')

    # ── 5. Hak Akses Pesanan ──────────────────────────────────────

    def test_pengguna_lain_tidak_bisa_mengakses_pesanan_orang_lain(self):
        # Buat pesanan milik budi
        url_checkout = reverse('checkout_pembelian', args=[self.produk_ready.id])
        self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '0812-3456-7890',
            'metode_pembayaran': 'cash',
            'opsi_penyerahan': 'showroom',
        })
        self.client_auth.post(reverse('konfirmasi_pembelian', args=[self.produk_ready.id]))
        pesanan = PesananPembelian.objects.get(produk=self.produk_ready)

        # Login sebagai pengguna lain (ani)
        client_ani = Client()
        client_ani.login(username='ani_pembeli', password='Password123!')

        resp_sukses = client_ani.get(reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan]))
        self.assertEqual(resp_sukses.status_code, 403)

        resp_detail = client_ani.get(reverse('pesanan_detail', args=[pesanan.nomor_pesanan]))
        self.assertEqual(resp_detail.status_code, 403)

        # Staf dapat mengakses
        client_staf = Client()
        client_staf.login(username='staf_mbg', password='Password123!')
        self.assertEqual(client_staf.get(reverse('pesanan_detail', args=[pesanan.nomor_pesanan])).status_code, 200)

    # ── 6. Pencegahan Double Booking & Validasi Ketersediaan Unit ─

    def test_pencegahan_jika_unit_tiba_tiba_tidak_ready_saat_konfirmasi(self):
        url_checkout = reverse('checkout_pembelian', args=[self.produk_ready.id])
        self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '0812-3456-7890',
            'metode_pembayaran': 'cash',
            'opsi_penyerahan': 'showroom',
        })

        # Simulasi: unit di-booking/terjual sebelum user menekan konfirmasi
        self.produk_ready.status_stok = 'in_review'
        self.produk_ready.save()

        # Submit konfirmasi harus ditolak
        resp_submit = self.client_auth.post(reverse('konfirmasi_pembelian', args=[self.produk_ready.id]))
        self.assertEqual(resp_submit.status_code, 302)
        self.assertRedirects(resp_submit, reverse('detail_produk', args=[self.produk_ready.id]))
        self.assertEqual(PesananPembelian.objects.count(), 0)

    # ── 7. Integrasi Dashboard Pelanggan ───────────────────────────

    def test_dashboard_pelanggan_menampilkan_pesanan_pembelian(self):
        url_checkout = reverse('checkout_pembelian', args=[self.produk_ready.id])
        self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '0812-3456-7890',
            'metode_pembayaran': 'kredit',
            'opsi_penyerahan': 'showroom',
        })
        self.client_auth.post(reverse('konfirmasi_pembelian', args=[self.produk_ready.id]))
        pesanan = PesananPembelian.objects.get(produk=self.produk_ready)

        resp_dash = self.client_auth.get(reverse('dashboard_user'))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, pesanan.nomor_pesanan)
        self.assertContains(resp_dash, 'Pesanan Pembelian Mobil')
        self.assertContains(resp_dash, reverse('pesanan_detail', args=[pesanan.nomor_pesanan]))
