import itertools
import re
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse

from toko.models import (
    PembayaranPesanan, Produk, PengaturanPembelian, PesananPembelian, RiwayatPesanan,
)

_urutan = itertools.count(100)


def bukti_transfer_pdf():
    return SimpleUploadedFile('bukti.pdf', b'%PDF-1.4\nbukti transfer', content_type='application/pdf')


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
    def bersihkan_bukti_transfer(self, pembayaran):
        self.addCleanup(
            pembayaran.bukti_transfer.storage.delete,
            pembayaran.bukti_transfer.name,
        )

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

    def buat_pesanan_unit(self, pelanggan):
        return PesananPembelian.buat_dari_produk(
            produk=self.produk_ready,
            pelanggan=pelanggan,
            nama_pelanggan=pelanggan.get_full_name() or pelanggan.username,
            no_whatsapp=pelanggan.no_whatsapp,
            metode_pembayaran='cash',
        )

    # ── 1. Tombol Beli Mobil pada Detail Kendaraan ─────────────────

    def test_tombol_beli_mobil_tampil_pada_unit_b2c_ready(self):
        resp = self.client_auth.get(reverse('detail_produk', args=[self.produk_ready.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Beli Mobil Sekarang')
        self.assertContains(resp, 'Booking Kendaraan (Booking Fee)')
        self.assertContains(resp, reverse('checkout_pembelian', args=[self.produk_ready.id]))
        self.assertContains(resp, 'name="jenis_pembelian" value="beli_langsung"')
        self.assertContains(resp, 'name="jenis_pembelian" value="booking"')

    def test_tombol_beli_menunjukkan_status_saat_unit_in_review(self):
        self.produk_ready.status_stok = 'in_review'
        self.produk_ready.save()
        resp = self.client_auth.get(reverse('detail_produk', args=[self.produk_ready.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Unit Sedang Di-booking Pelanggan Lain')
        html = resp.content.decode()
        self.assertRegex(html, r'<button[^>]+id="btnBookingUnit"[^>]+disabled')
        button = re.search(r'<button[^>]+id="btnBookingUnit"[^>]*>.*?</button>', html, re.DOTALL)
        self.assertIsNotNone(button)
        self.assertNotIn('Beli Mobil Sekarang', button.group(0))
        self.assertContains(resp, 'unitBookedModal')
        self.assertContains(resp, 'Kembali ke Katalog')
        self.assertContains(resp, f'href="{reverse("katalog")}"')
        html = resp.content.decode()
        self.assertLess(html.index('</main>'), html.index('id="unitBookedModal"'))

    def test_ketersediaan_real_time_menonaktifkan_booking_dan_menampilkan_modal(self):
        url_status = reverse('status_ketersediaan_unit', args=[self.produk_ready.id])

        before = self.client_anon.get(url_status)
        self.assertEqual(before.status_code, 200)
        self.assertTrue(before.json()['available'])
        initially_available_page = self.client_anon.get(
            reverse('detail_produk', args=[self.produk_ready.id])
        )
        self.assertContains(initially_available_page, 'unitBookedModal')

        order = self.buat_pesanan_unit(self.pelanggan_lain)
        response = self.client_anon.get(reverse('detail_produk', args=[self.produk_ready.id]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertRegex(html, r'<button[^>]+id="btnBookingUnit"[^>]+disabled')
        self.assertIn('unitBookedModal', html)
        self.assertIn('Unit Sedang Di-booking Pelanggan Lain', html)
        self.assertNotIn(order.nomor_pesanan, html)
        button = re.search(r'<button[^>]+id="btnBookingUnit"[^>]*>.*?</button>', html, re.DOTALL)
        self.assertIsNotNone(button)
        self.assertNotIn('Beli Mobil Sekarang', button.group(0))

        after = self.client_anon.get(url_status)
        self.assertEqual(after.status_code, 200)
        self.assertFalse(after.json()['available'])
        self.assertEqual(after.json()['state'], 'booked')
        self.assertNotIn(order.nomor_pesanan, after.content.decode())

        client_owner = Client()
        client_owner.login(username='ani_pembeli', password='Password123!')
        own_response = client_owner.get(url_status)
        self.assertTrue(own_response.json()['reserved_by_you'])
        self.assertEqual(
            own_response.json()['order_url'],
            reverse('pesanan_detail', args=[order.nomor_pesanan]),
        )

    def test_ketersediaan_kembali_setelah_pesanan_tanpa_pembayaran_dibatalkan(self):
        order = self.buat_pesanan_unit(self.pelanggan_lain)
        order.batalkan_tanpa_pembayaran(self.pelanggan_lain, 'Pelanggan membatalkan.')

        response = self.client_anon.get(
            reverse('status_ketersediaan_unit', args=[self.produk_ready.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['available'])

    def test_ketersediaan_kembali_setelah_booking_kedaluwarsa(self):
        order = self.buat_pesanan_unit(self.pelanggan_lain)
        pembayaran = PembayaranPesanan.objects.create(
            pesanan=order,
            jenis='booking_fee',
            nominal=order.nominal_booking_fee,
            saluran='qris',
        )
        pembayaran.verifikasi(oleh_user=self.staf)
        order.refresh_from_db()
        self.assertTrue(order.kedaluwarsakan(
            sekarang=order.booking_berakhir_pada + timedelta(seconds=1),
        ))

        response = self.client_anon.get(
            reverse('status_ketersediaan_unit', args=[self.produk_ready.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['available'])

    def test_ketersediaan_kembali_setelah_pembatalan_refund_disetujui(self):
        order = self.buat_pesanan_unit(self.pelanggan)
        pembayaran = PembayaranPesanan.objects.create(
            pesanan=order,
            jenis='booking_fee',
            nominal=order.nominal_booking_fee,
            saluran='qris',
        )
        pembayaran.verifikasi(oleh_user=self.staf)
        refund = order.ajukan_pembatalan(
            diajukan_oleh=self.pelanggan,
            alasan='Pembeli mengajukan pembatalan.',
        )
        refund.setujui(oleh_user=self.staf)

        response = self.client_anon.get(
            reverse('status_ketersediaan_unit', args=[self.produk_ready.id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['available'])

    def test_unit_c2c_yang_belum_tayang_tidak_dapat_diakses_publik(self):
        produk_c2c = buat_produk(tipe_penjual='C2C')
        resp = self.client_auth.get(reverse('detail_produk', args=[produk_c2c.id]))
        self.assertEqual(resp.status_code, 404)

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
        self.assertEqual(resp.url, reverse('detail_produk', args=[c2c.id]))

    def test_checkout_menolak_unit_tidak_ready(self):
        self.produk_ready.status_stok = 'sold'
        self.produk_ready.save()
        url = reverse('checkout_pembelian', args=[self.produk_ready.id])
        resp = self.client_auth.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('detail_produk', args=[self.produk_ready.id]))

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
        self.assertIn('8000000', re.sub(r'\D', '', resp.content.decode()))

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
        self.assertFormError(
            resp.context['form'],
            'alamat_pengantaran',
            'Alamat pengantaran wajib diisi jika memilih pengantaran towing.',
        )

    # ── 4. Alur Lengkap: Checkout -> Konfirmasi -> Buat Pesanan ───

    def test_alur_pembelian_berhasil(self):
        # Step 1: POST data checkout yang valid
        url_checkout = reverse('checkout_pembelian', args=[self.produk_ready.id])
        resp_checkout = self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '0812-3456-7890',
            'email': 'budi@example.test',
            'metode_pembayaran': 'cash',
            'saluran_pembayaran': 'transfer',
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
        self.assertIn('8000000', re.sub(r'\D', '', resp_konfirmasi.content.decode()))
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
        self.assertContains(resp_sukses, 'Menunggu Pembayaran')
        self.assertContains(resp_sukses, 'Pesanan Diajukan')
        self.assertNotContains(resp_sukses, 'Pesanan Pembelian Berhasil Dibuat')
        self.assertFalse(resp_sukses.context['tahap_pembayaran_selesai'])
        self.assertEqual(
            resp_sukses.context['pesanan'].nominal_pembayaran_midtrans,
            Decimal('8000000'),
        )

    def test_input_checkout_tampil_di_panel_admin_sesuai_snapshot_pesanan(self):
        url_checkout = (
            f"{reverse('checkout_pembelian', args=[self.produk_ready.id])}"
            "?jenis_pembelian=booking"
        )
        self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '081234567890',
            'email': 'email-pesanan@example.test',
            'metode_pembayaran': 'cash',
            'saluran_pembayaran': 'transfer',
            'opsi_penyerahan': 'towing',
            'alamat_pengantaran': 'Jl. Contoh No. 10, Makassar',
        })
        self.client_auth.post(reverse('konfirmasi_pembelian', args=[self.produk_ready.id]))
        pesanan = PesananPembelian.objects.get(produk=self.produk_ready)
        admin = Client()
        admin.login(username='staf_mbg', password='Password123!')

        panel = admin.get(reverse('admin_pesanan_pembelian'))
        booking_panel = admin.get(reverse('admin_booking'))
        filtered_bookings = admin.get(
            reverse('admin_pesanan_pembelian'),
            {'jenis': 'booking'},
        )

        self.assertContains(panel, 'email-pesanan@example.test')
        self.assertNotContains(panel, self.pelanggan.email)
        self.assertContains(panel, 'Diantar via Truk Towing ke Rumah')
        self.assertContains(panel, 'Jl. Contoh No. 10, Makassar')
        self.assertEqual(booking_panel.context['jumlah_booking_kendaraan'], 1)
        self.assertContains(booking_panel, reverse('admin_pesanan_pembelian') + '?jenis=booking')
        self.assertContains(filtered_bookings, 'email-pesanan@example.test')
        self.assertContains(filtered_bookings, pesanan.nomor_pesanan)
        self.assertEqual(pesanan.email, 'email-pesanan@example.test')
        self.assertEqual(pesanan.opsi_penyerahan, 'towing')
        self.assertEqual(pesanan.alamat_pengantaran, 'Jl. Contoh No. 10, Makassar')

    def test_checkout_beli_langsung_membuat_pesanan_pembayaran_penuh(self):
        url_checkout = (
            f"{reverse('checkout_pembelian', args=[self.produk_ready.id])}"
            "?jenis_pembelian=beli_langsung"
        )
        checkout = self.client_auth.get(url_checkout)
        self.assertContains(checkout, 'Beli Langsung')
        self.assertContains(checkout, '100%')
        self.assertRegex(
            checkout.content.decode(),
            r'id="kreditPlanInfo"[^>]*class="[^"]*\bd-none\b',
        )
        self.assertContains(checkout, 'directCashSummary')
        self.assertNotContains(checkout, 'Pilih Jenis Pembelian')

        response = self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '081234567890',
            'email': 'budi@example.test',
            'jenis_pembelian': 'booking',
            'metode_pembayaran': 'cash',
            'saluran_pembayaran': 'transfer',
            'opsi_penyerahan': 'showroom',
            'alamat_pengantaran': '',
        })
        self.assertRedirects(
            response,
            reverse('konfirmasi_pembelian', args=[self.produk_ready.id]),
        )
        confirm = self.client_auth.get(
            reverse('konfirmasi_pembelian', args=[self.produk_ready.id])
        )
        self.assertContains(confirm, 'Total pembayaran')
        self.assertEqual(
            confirm.context['nominal_pembelian_langsung'],
            Decimal('400000000'),
        )

        created = self.client_auth.post(
            reverse('konfirmasi_pembelian', args=[self.produk_ready.id])
        )
        pesanan = PesananPembelian.objects.get(produk=self.produk_ready)
        self.assertRedirects(
            created,
            reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan]),
        )
        self.assertEqual(pesanan.jenis_pembelian, 'beli_langsung')
        self.assertEqual(pesanan.metode_pembayaran, 'cash')
        self.assertEqual(pesanan.nominal_pembayaran_midtrans, Decimal('400000000'))
        self.assertEqual(pesanan.status, 'menunggu_booking_fee')
        status_page = self.client_auth.get(
            reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan])
        )
        self.assertContains(status_page, 'Menunggu Pembayaran')
        self.assertContains(status_page, 'Admin akan memeriksa nomor referensi')
        self.assertFalse(status_page.context['tahap_pembayaran_selesai'])
        self.produk_ready.refresh_from_db()
        self.assertEqual(self.produk_ready.status_stok, 'ready')

    def test_beli_langsung_kredit_menunggu_admin_lalu_dp_dan_angsuran_diverifikasi(self):
        url_checkout = (
            f"{reverse('checkout_pembelian', args=[self.produk_ready.id])}"
            "?jenis_pembelian=beli_langsung"
        )
        checkout = self.client_auth.get(url_checkout)
        self.assertEqual(checkout.status_code, 200)
        self.assertContains(checkout, 'name="metode_pembayaran"')
        self.assertContains(checkout, 'directCreditSummary')
        self.assertEqual(
            checkout.context['rincian_kredit']['nominal_dp'],
            Decimal('80000000'),
        )

        self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '081234567890',
            'email': 'budi@example.test',
            'metode_pembayaran': 'kredit',
            'saluran_pembayaran': 'midtrans',
            'opsi_penyerahan': 'showroom',
        })
        confirmation = self.client_auth.get(
            reverse('konfirmasi_pembelian', args=[self.produk_ready.id])
        )
        self.assertContains(confirmation, 'Tidak ada pembayaran sekarang')
        self.assertContains(confirmation, 'DP setelah disetujui admin (20%)')
        self.assertNotContains(confirmation, 'Total pembayaran (100% OTR)')

        created = self.client_auth.post(
            reverse('konfirmasi_pembelian', args=[self.produk_ready.id])
        )
        pesanan = PesananPembelian.objects.get(produk=self.produk_ready)
        self.assertRedirects(created, reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan]))
        self.assertEqual(pesanan.metode_pembayaran, 'kredit')
        self.assertEqual(pesanan.saluran_pembayaran, 'transfer')
        self.assertEqual(pesanan.status, 'menunggu_kredit')
        credit_status = self.client_auth.get(
            reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan])
        )
        self.assertContains(credit_status, 'Pengajuan Kredit Tercatat')
        self.assertTrue(credit_status.context['tahap_pembayaran_selesai'])
        self.assertFalse(pesanan.pembayaran.exists())
        snap = self.client_auth.post(
            reverse('midtrans_snap_token', args=[pesanan.nomor_pesanan]),
        )
        self.assertEqual(snap.status_code, 400)
        self.assertEqual(snap.json()['status'], 'error')

        self.produk_ready.refresh_from_db()
        self.assertEqual(self.produk_ready.status_stok, 'ready')

        forbidden_review = self.client_auth.post(
            reverse('admin_tinjau_pengajuan_kredit', args=[pesanan.id]),
            {'aksi': 'setujui'},
        )
        self.assertIn(forbidden_review.status_code, (302, 403))
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_kredit')

        premature_payment = self.client_auth.post(
            reverse('konfirmasi_transfer_pesanan', args=[pesanan.nomor_pesanan]),
            {'nomor_referensi': 'TRF-EARLY'},
        )
        self.assertRedirects(
            premature_payment,
            reverse('pesanan_detail', args=[pesanan.nomor_pesanan]),
        )
        self.assertFalse(pesanan.pembayaran.exists())

        client_admin = Client()
        client_admin.login(username='staf_mbg', password='Password123!')
        approved = client_admin.post(
            reverse('admin_tinjau_pengajuan_kredit', args=[pesanan.id]),
            {'aksi': 'setujui'},
        )
        self.assertRedirects(approved, reverse('admin_pesanan_pembelian'))
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_dp_kredit')
        self.produk_ready.refresh_from_db()
        self.assertEqual(self.produk_ready.status_stok, 'ready')

        self.client_auth.post(
            reverse('konfirmasi_transfer_pesanan', args=[pesanan.nomor_pesanan]),
            {
                'nomor_referensi': 'TRF-DP-80M',
                'bukti_transfer': bukti_transfer_pdf(),
            },
        )
        dp = PembayaranPesanan.objects.get(pesanan=pesanan)
        self.assertEqual(dp.jenis, 'dp')
        self.assertEqual(dp.nominal, Decimal('80000000'))
        self.assertEqual(dp.status, 'menunggu_verifikasi')
        self.bersihkan_bukti_transfer(dp)
        dp.verifikasi(self.staf)
        pesanan.refresh_from_db()
        self.produk_ready.refresh_from_db()
        self.assertEqual(pesanan.status, 'kredit_aktif')
        self.assertEqual(pesanan.sisa_pembayaran(), Decimal('320000000'))
        self.assertEqual(self.produk_ready.status_stok, 'in_review')

        detail_kredit_aktif = self.client_auth.get(
            reverse('pesanan_detail', args=[pesanan.nomor_pesanan])
        )
        self.assertContains(detail_kredit_aktif, 'Angsuran tahunan berikutnya')
        self.assertContains(detail_kredit_aktif, 'Saya sudah transfer angsuran')
        self.assertEqual(
            detail_kredit_aktif.context['nominal_pembayaran_transfer'],
            Decimal('20000000'),
        )
        self.assertContains(detail_kredit_aktif, '20.000.000')

        installment_recorded = self.client_auth.post(
            reverse('konfirmasi_transfer_pesanan', args=[pesanan.nomor_pesanan]),
            {
                'nomor_referensi': 'TRF-ANGSURAN-1',
                'bukti_transfer': bukti_transfer_pdf(),
            },
        )
        self.assertRedirects(
            installment_recorded,
            reverse('pesanan_detail', args=[pesanan.nomor_pesanan]),
        )
        installment = PembayaranPesanan.objects.get(pesanan=pesanan, jenis='angsuran')
        self.assertEqual(installment.status, 'menunggu_verifikasi')
        self.bersihkan_bukti_transfer(installment)
        self.client_auth.post(
            reverse('konfirmasi_transfer_pesanan', args=[pesanan.nomor_pesanan]),
            {
                'nomor_referensi': 'TRF-ANGSURAN-DUPLIKAT',
                'bukti_transfer': bukti_transfer_pdf(),
            },
        )
        self.assertEqual(
            PembayaranPesanan.objects.filter(pesanan=pesanan, jenis='angsuran').count(),
            1,
        )
        installment.verifikasi(self.staf)
        self.assertEqual(pesanan.sisa_pembayaran(), Decimal('300000000'))

    def test_admin_dapat_menolak_pengajuan_kredit_tanpa_mengunci_unit(self):
        pesanan = PesananPembelian.buat_dari_produk(
            produk=self.produk_ready,
            pelanggan=self.pelanggan,
            nama_pelanggan='Budi Santoso',
            no_whatsapp=self.pelanggan.no_whatsapp,
            metode_pembayaran='kredit',
            jenis_pembelian='beli_langsung',
            saluran_pembayaran='transfer',
        )
        client_admin = Client()
        client_admin.login(username='staf_mbg', password='Password123!')

        response = client_admin.post(
            reverse('admin_tinjau_pengajuan_kredit', args=[pesanan.id]),
            {'aksi': 'tolak', 'alasan': 'Dokumen belum lengkap'},
        )

        self.assertRedirects(response, reverse('admin_pesanan_pembelian'))
        pesanan.refresh_from_db()
        self.produk_ready.refresh_from_db()
        self.assertEqual(pesanan.status, 'dibatalkan')
        self.assertEqual(self.produk_ready.status_stok, 'ready')
        self.assertEqual(pesanan.riwayat.latest('created_at').catatan, 'Dokumen belum lengkap')

    def test_booking_checkout_jenisnya_ditentukan_url_bukan_input_post(self):
        url_checkout = (
            f"{reverse('checkout_pembelian', args=[self.produk_ready.id])}"
            "?jenis_pembelian=booking"
        )
        self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '081234567890',
            'jenis_pembelian': 'beli_langsung',
            'saluran_pembayaran': 'transfer',
            'metode_pembayaran': 'cash',
            'opsi_penyerahan': 'showroom',
        })
        self.client_auth.post(reverse('konfirmasi_pembelian', args=[self.produk_ready.id]))
        pesanan = PesananPembelian.objects.get(produk=self.produk_ready)
        self.assertEqual(pesanan.jenis_pembelian, 'booking')

    # ── 5. Hak Akses Pesanan ──────────────────────────────────────

    def test_pengguna_lain_tidak_bisa_mengakses_pesanan_orang_lain(self):
        # Buat pesanan milik budi
        url_checkout = reverse('checkout_pembelian', args=[self.produk_ready.id])
        self.client_auth.post(url_checkout, {
            'nama_pelanggan': 'Budi Santoso',
            'no_whatsapp': '0812-3456-7890',
            'metode_pembayaran': 'cash',
            'saluran_pembayaran': 'transfer',
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
            'saluran_pembayaran': 'transfer',
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
            'saluran_pembayaran': 'transfer',
            'opsi_penyerahan': 'showroom',
        })
        self.client_auth.post(reverse('konfirmasi_pembelian', args=[self.produk_ready.id]))
        pesanan = PesananPembelian.objects.get(produk=self.produk_ready)
        PembayaranPesanan.objects.create(
            pesanan=pesanan,
            jenis='booking_fee',
            nominal=pesanan.nominal_booking_fee,
            saluran='transfer',
        )
        pesanan_langsung = PesananPembelian.buat_dari_produk(
            produk=buat_produk(),
            pelanggan=self.pelanggan,
            nama_pelanggan=self.pelanggan.get_full_name(),
            no_whatsapp=self.pelanggan.no_whatsapp,
            metode_pembayaran='cash',
            jenis_pembelian='beli_langsung',
            saluran_pembayaran='transfer',
        )
        pesanan_pelanggan_lain = PesananPembelian.buat_dari_produk(
            produk=buat_produk(),
            pelanggan=self.pelanggan_lain,
            nama_pelanggan=self.pelanggan_lain.get_full_name(),
            no_whatsapp=self.pelanggan_lain.no_whatsapp,
            metode_pembayaran='cash',
        )

        resp_dash = self.client_auth.get(reverse('dashboard_user'))
        self.assertEqual(resp_dash.status_code, 200)
        self.assertContains(resp_dash, pesanan.nomor_pesanan)
        self.assertContains(resp_dash, pesanan_langsung.nomor_pesanan)
        self.assertNotContains(resp_dash, pesanan_pelanggan_lain.nomor_pesanan)
        self.assertContains(resp_dash, 'Booking Kendaraan')
        self.assertContains(resp_dash, 'Pembelian Langsung')
        self.assertContains(resp_dash, 'Riwayat Booking, Pembelian &amp; Pembayaran')
        self.assertContains(resp_dash, 'Menunggu Verifikasi')
        self.assertContains(resp_dash, 'id="riwayat-pesanan"')
        self.assertContains(resp_dash, reverse('pesanan_detail', args=[pesanan.nomor_pesanan]))
        self.assertTrue(resp_dash.context['user_booking_pembelian'].filter(pk=pesanan.pk).exists())
        self.assertTrue(resp_dash.context['user_pembelian_langsung'].filter(pk=pesanan_langsung.pk).exists())
