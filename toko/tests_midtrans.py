"""
Unit & Integration Tests untuk Midtrans Snap Sandbox (Sprint 3)
Mobil Bekas Garasi (MBG) Makassar.

Cakupan Pengujian:
1. Pembuatan transaksi Snap dari backend & pencegahan transaksi ganda.
2. Endpoint backend snap-token dan Get Status (otentikasi, otorisasi, keamanan server key).
3. Validasi Webhook: signature SHA512, order_id, nominal gross_amount.
4. Penanganan seluruh status transaksi: pending, settlement, capture, expire, cancel, deny.
5. Idempotensi webhook/status sync terhadap notifikasi berulang dan notifikasi terlambat.
6. Aturan Inti MBG: Midtrans mengunci unit otomatis setelah pembayaran terkonfirmasi;
   transfer bank manual baru mengunci unit setelah persetujuan admin.
7. Tidak ada kebocoran MIDTRANS_SERVER_KEY ke frontend / template / response JSON.
"""
import base64
import hashlib
import io
import json
from decimal import Decimal
from unittest import mock
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from katalog_mbg.settings import _resolve_midtrans_config

from .midtrans_service import (
    buat_snap_transaksi,
    hitung_signature,
    petakan_saluran_midtrans,
    proses_notifikasi_webhook,
    proses_status_get_midtrans,
    verifikasi_signature,
)
from .models import (
    PembayaranPesanan,
    PengaturanPembelian,
    PengaturanWebsite,
    PesananPembelian,
    Produk,
)

User = get_user_model()

DUMMY_SERVER_KEY = "SB-Mid-server-TESTKEY123456"
DUMMY_CLIENT_KEY = "SB-Mid-client-TESTCLIENT789"


class MidtransConfigurationTests(TestCase):
    def test_debug_uses_only_sandbox_keys_and_urls(self):
        sandbox_server_key = 'sandbox-server-key-from-dashboard'
        sandbox_client_key = 'sandbox-client-key-from-dashboard'
        config = _resolve_midtrans_config(True, {
            'MIDTRANS_IS_PRODUCTION': 'false',
            'MIDTRANS_SERVER_KEY': 'Mid-server-production-placeholder',
            'MIDTRANS_CLIENT_KEY': 'Mid-client-production-placeholder',
            'MIDTRANS_SANDBOX_SERVER_KEY': sandbox_server_key,
            'MIDTRANS_SANDBOX_CLIENT_KEY': sandbox_client_key,
        })

        self.assertEqual(config[0], sandbox_server_key)
        self.assertEqual(config[1], sandbox_client_key)
        self.assertFalse(config[2])
        self.assertEqual(
            config[3], 'https://app.sandbox.midtrans.com/snap/v1/transactions'
        )
        self.assertEqual(config[4], 'https://app.sandbox.midtrans.com/snap/snap.js')

    def test_debug_does_not_fall_back_to_production_keys(self):
        config = _resolve_midtrans_config(True, {
            'MIDTRANS_IS_PRODUCTION': 'false',
            'MIDTRANS_SERVER_KEY': 'Mid-server-production-placeholder',
            'MIDTRANS_CLIENT_KEY': 'Mid-client-production-placeholder',
        })

        self.assertEqual(config[0], '')
        self.assertEqual(config[1], '')
        self.assertFalse(config[2])

    def test_debug_rejects_production_mode(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'must be false'):
            _resolve_midtrans_config(True, {'MIDTRANS_IS_PRODUCTION': 'true'})

    def test_invalid_production_mode_is_rejected(self):
        with self.assertRaisesMessage(ImproperlyConfigured, 'boolean value'):
            _resolve_midtrans_config(True, {'MIDTRANS_IS_PRODUCTION': 'sandbox-ish'})


@override_settings(
    MIDTRANS_SERVER_KEY=DUMMY_SERVER_KEY,
    MIDTRANS_CLIENT_KEY=DUMMY_CLIENT_KEY,
    MIDTRANS_IS_PRODUCTION=False,
    MIDTRANS_SNAP_URL="https://app.sandbox.midtrans.com/snap/v1/transactions",
    MIDTRANS_SNAP_JS_URL="https://app.sandbox.midtrans.com/snap/snap.js",
)
class MidtransSnapServiceTests(TestCase):
    def setUp(self):
        PengaturanPembelian.objects.all().delete()
        self.pengaturan_pembelian = PengaturanPembelian.objects.create(
            persentase_booking_fee=Decimal('2.00'),
        )
        self.pembeli = User.objects.create_user(
            username='pembeli_midtrans',
            password='Password123!',
            email='pembeli@example.test',
            no_whatsapp='081234567890',
        )
        self.pembeli_lain = User.objects.create_user(
            username='pembeli_lain',
            password='Password123!',
            email='lain@example.test',
            no_whatsapp='081299998888',
        )
        self.admin = User.objects.create_superuser(
            username='admin_mbg',
            password='Password123!',
            email='admin@mbgmakassar.id',
            is_staff=True,
        )
        self.produk = Produk.objects.create(
            nama='Honda HR-V E Special Edition 2021',
            merek='Honda',
            tahun=2021,
            jarak_tempuh=28000,
            transmisi='AT',
            harga=Decimal('250000000'),
            tipe_penjual='B2C',
            status_stok='ready',
            sku='HRV-2021-001',
            plat_nomor='DD 1234 XY',
        )
        self.pesanan = PesananPembelian.buat_dari_produk(
            produk=self.produk,
            pelanggan=self.pembeli,
            nama_pelanggan='Budi Santoso',
            no_whatsapp='081234567890',
            metode_pembayaran='cash',
            opsi_penyerahan='showroom',
            email='pembeli@example.test',
        )

    # ── 1. Pembuatan Transaksi Snap Backend ────────────────────────

    def test_missing_debug_key_names_sandbox_setting(self):
        with override_settings(DEBUG=True, MIDTRANS_SERVER_KEY=''):
            with self.assertRaisesMessage(
                ValueError, 'MIDTRANS_SANDBOX_SERVER_KEY'
            ):
                buat_snap_transaksi(self.pesanan)

    @mock.patch('urllib.request.urlopen')
    def test_buat_snap_transaksi_sukses(self, mock_urlopen):
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps({
            'token': 'snap-token-abc-123',
            'redirect_url': 'https://app.sandbox.midtrans.com/snap/v2/vtweb/snap-token-abc-123',
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        token, redirect_url, order_id = buat_snap_transaksi(self.pesanan)

        self.assertEqual(token, 'snap-token-abc-123')
        self.assertTrue(order_id.startswith(f'{self.pesanan.nomor_pesanan}-'))
        self.assertIn('snap-token-abc-123', redirect_url)

        # Pastikan data tersimpan di model pesanan
        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.midtrans_snap_token, 'snap-token-abc-123')
        self.assertEqual(self.pesanan.midtrans_order_id, order_id)

        # Verifikasi request yang dikirim ke Midtrans
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertEqual(req.full_url, 'https://app.sandbox.midtrans.com/snap/v1/transactions')
        self.assertEqual(req.method, 'POST')

        # Verifikasi Basic Auth dengan Server Key
        expected_auth = base64.b64encode(f"{DUMMY_SERVER_KEY}:".encode('utf-8')).decode('utf-8')
        self.assertEqual(req.headers.get('Authorization'), f"Basic {expected_auth}")

        # Verifikasi payload
        sent_data = json.loads(req.data.decode('utf-8'))
        self.assertEqual(sent_data['transaction_details']['order_id'], order_id)
        self.assertEqual(sent_data['transaction_details']['gross_amount'], int(self.pesanan.nominal_booking_fee))
        self.assertEqual(sent_data['customer_details']['phone'], self.pesanan.no_whatsapp)

    @mock.patch('urllib.request.urlopen')
    def test_buat_snap_transaksi_beli_langsung_mengirim_100_persen_harga(self, mock_urlopen):
        pesanan = self._buat_pesanan_beli_langsung()
        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = json.dumps({
            'token': 'snap-direct-100',
            'redirect_url': 'https://app.sandbox.midtrans.com/snap/v2/vtweb/snap-direct-100',
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        buat_snap_transaksi(pesanan)

        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode('utf-8'))
        self.assertEqual(
            payload['transaction_details']['gross_amount'],
            int(pesanan.harga_kendaraan),
        )
        self.assertIn('Pembelian Langsung', payload['item_details'][0]['name'])

    @mock.patch('urllib.request.urlopen')
    def test_api_error_does_not_log_or_return_credentials(self, mock_urlopen):
        mock_urlopen.side_effect = HTTPError(
            'https://app.sandbox.midtrans.com/snap/v1/transactions',
            401,
            'Unauthorized',
            {},
            io.BytesIO(DUMMY_SERVER_KEY.encode('utf-8')),
        )

        with self.assertLogs('toko.midtrans_service', level='ERROR') as logs:
            with self.assertRaises(ValueError) as error:
                buat_snap_transaksi(self.pesanan)

        self.assertNotIn(DUMMY_SERVER_KEY, str(error.exception))
        self.assertNotIn(DUMMY_SERVER_KEY, '\n'.join(logs.output))
        self.pesanan.refresh_from_db()
        self.assertTrue(self.pesanan.midtrans_order_id)

    @mock.patch('urllib.request.urlopen')
    def test_api_retry_reuses_same_attempt_id(self, mock_urlopen):
        mock_urlopen.side_effect = [
            HTTPError(
                'https://app.sandbox.midtrans.com/snap/v1/transactions',
                502,
                'Bad Gateway',
                {},
                io.BytesIO(b'gateway error'),
            ),
            mock.MagicMock(
                __enter__=mock.Mock(return_value=mock.MagicMock(
                    read=mock.Mock(return_value=json.dumps({
                        'token': 'snap-token-retry',
                        'redirect_url': 'https://app.sandbox.midtrans.com/snap/retry',
                    }).encode('utf-8'))
                ))
            ),
        ]
        with self.assertRaises(ValueError):
            buat_snap_transaksi(self.pesanan)
        first_order_id = self.pesanan.__class__.objects.get(pk=self.pesanan.pk).midtrans_order_id
        token, _, next_order_id = buat_snap_transaksi(self.pesanan)

        self.assertEqual(token, 'snap-token-retry')
        self.assertEqual(next_order_id, first_order_id)

    @mock.patch('urllib.request.urlopen')
    def test_hindari_transaksi_ganda_token_sudah_ada(self, mock_urlopen):
        """Jika token sudah tersimpan pada pesanan, kembalikan token lama tanpa memanggil ulang API Midtrans."""
        self.pesanan.midtrans_snap_token = 'token-lama-reuse'
        self.pesanan.midtrans_snap_redirect_url = 'https://app.sandbox.midtrans.com/snap/lama'
        self.pesanan.midtrans_order_id = self.pesanan.nomor_pesanan
        self.pesanan.save()

        token, redirect_url, order_id = buat_snap_transaksi(self.pesanan)

        self.assertEqual(token, 'token-lama-reuse')
        self.assertEqual(order_id, self.pesanan.midtrans_order_id)
        # urlopen tidak boleh dipanggil sama sekali
        mock_urlopen.assert_not_called()

    def test_tolak_snap_jika_status_bukan_menunggu_booking_fee(self):
        self.pesanan.status = 'booking_aktif'
        self.pesanan.save(update_fields=['status'])

        with self.assertRaisesMessage(ValueError, 'tidak dalam status menunggu Booking Fee'):
            buat_snap_transaksi(self.pesanan)

    def test_tolak_snap_jika_pembayaran_sudah_tercatat(self):
        PembayaranPesanan.objects.create(
            pesanan=self.pesanan,
            jenis='booking_fee',
            nominal=self.pesanan.nominal_booking_fee,
            saluran='va',
            status='menunggu_verifikasi',
        )
        with self.assertRaisesMessage(ValueError, 'sedang menunggu verifikasi'):
            buat_snap_transaksi(self.pesanan)

    # ── 2. Endpoint Backend: midtrans_snap_token ───────────────────

    def test_snap_token_endpoint_memerlukan_login(self):
        client = Client()
        url = reverse('midtrans_snap_token', args=[self.pesanan.nomor_pesanan])
        resp = client.get(url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp.url)

    def test_snap_token_endpoint_menolak_pengguna_lain(self):
        client = Client()
        client.login(username='pembeli_lain', password='Password123!')
        url = reverse('midtrans_snap_token', args=[self.pesanan.nomor_pesanan])
        resp = client.post(url)
        self.assertEqual(resp.status_code, 403)

    @mock.patch('toko.views.buat_snap_transaksi')
    def test_snap_token_endpoint_menolak_pesanan_transfer_bank(self, mock_buat):
        produk = Produk.objects.create(
            nama='Toyota Yaris 2022',
            merek='Toyota',
            tahun=2022,
            jarak_tempuh=18000,
            transmisi='AT',
            harga=Decimal('220000000'),
            tipe_penjual='B2C',
            status_stok='ready',
            sku='YARIS-2022-002',
            plat_nomor='DD 4321 YZ',
        )
        pesanan_transfer = PesananPembelian.buat_dari_produk(
            produk=produk,
            pelanggan=self.pembeli,
            nama_pelanggan='Budi Santoso',
            no_whatsapp='081234567890',
            metode_pembayaran='cash',
            saluran_pembayaran='transfer',
        )
        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')

        response = client.post(
            reverse('midtrans_snap_token', args=[pesanan_transfer.nomor_pesanan]),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['status'], 'error')
        mock_buat.assert_not_called()

    @mock.patch('toko.views.buat_snap_transaksi')
    def test_snap_token_endpoint_sukses_untuk_pemilik_dan_tidak_membocorkan_server_key(self, mock_buat):
        mock_buat.return_value = ('token-xyz-789', 'https://midtrans.test/redirect', self.pesanan.nomor_pesanan)

        client = Client(enforce_csrf_checks=True)
        client.login(username='pembeli_midtrans', password='Password123!')
        client.get(reverse('pesanan_detail', args=[self.pesanan.nomor_pesanan]))
        csrf_token = client.cookies['csrftoken'].value
        url = reverse('midtrans_snap_token', args=[self.pesanan.nomor_pesanan])
        self.assertEqual(client.post(url).status_code, 403)
        resp = client.post(url, HTTP_X_CSRFTOKEN=csrf_token)

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.assertEqual(data['snap_token'], 'token-xyz-789')
        self.assertEqual(data['client_key'], DUMMY_CLIENT_KEY)

        # Pastikan SERVER KEY tidak ada di response body
        self.assertNotIn(DUMMY_SERVER_KEY, resp.content.decode('utf-8'))
        self.assertNotIn('server_key', resp.content.decode('utf-8').lower())

    # ── 3. Validasi Webhook & Keamanan Signature ───────────────────

    def _buat_payload_webhook(self, order_id=None, status_code='200', gross_amount=None,
                              transaction_status='settlement', fraud_status='accept',
                              payment_type='bank_transfer', transaction_id='trx-midtrans-001'):
        order_id = order_id or self.pesanan.midtrans_order_id or self.pesanan.nomor_pesanan
        if not self.pesanan.midtrans_order_id and order_id == self.pesanan.nomor_pesanan:
            self.pesanan.midtrans_order_id = order_id
            self.pesanan.save(update_fields=['midtrans_order_id'])
        gross_amount = gross_amount if gross_amount is not None else f"{int(self.pesanan.nominal_pembayaran_midtrans)}.00"
        sig = hitung_signature(order_id, status_code, gross_amount, server_key=DUMMY_SERVER_KEY)
        return {
            'order_id': order_id,
            'status_code': status_code,
            'gross_amount': gross_amount,
            'signature_key': sig,
            'transaction_id': transaction_id,
            'transaction_status': transaction_status,
            'fraud_status': fraud_status,
            'payment_type': payment_type,
            'transaction_time': '2026-11-16 08:00:00',
            'settlement_time': '2026-11-16 08:02:00',
        }

    def _buat_pesanan_beli_langsung(self):
        self.pesanan.batalkan_tanpa_pembayaran(
            self.pembeli,
            'Membuat pesanan baru untuk pengujian beli langsung.',
        )
        self.pesanan = PesananPembelian.buat_dari_produk(
            produk=self.produk,
            pelanggan=self.pembeli,
            nama_pelanggan='Budi Santoso',
            no_whatsapp='081234567890',
            metode_pembayaran='cash',
            opsi_penyerahan='showroom',
            email='pembeli@example.test',
            jenis_pembelian='beli_langsung',
        )
        self.pesanan.midtrans_order_id = self.pesanan.nomor_pesanan
        self.pesanan.save(update_fields=['midtrans_order_id'])
        return self.pesanan

    def test_webhook_menolak_signature_palsu(self):
        payload = self._buat_payload_webhook()
        payload['signature_key'] = 'signature_palsu_yang_salah_total'

        client = Client()
        url = reverse('midtrans_webhook')
        resp = client.post(url, data=json.dumps(payload), content_type='application/json')

        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.json()['error'], 'invalid_signature')

    def test_webhook_menolak_signature_jika_server_key_tidak_dikonfigurasi(self):
        payload = self._buat_payload_webhook()
        result = proses_notifikasi_webhook(payload, server_key='')
        self.assertFalse(result['success'])
        self.assertEqual(result['error'], 'invalid_signature')

    def test_webhook_menolak_order_id_yang_tidak_ada(self):
        payload = self._buat_payload_webhook(order_id='PB-MBG-TIDAK-ADA-999')

        client = Client()
        url = reverse('midtrans_webhook')
        resp = client.post(url, data=json.dumps(payload), content_type='application/json')

        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()['error'], 'order_not_found')

    def test_webhook_menolak_nominal_yang_tidak_sesuai(self):
        # Tagihan asli 5.000.000, notifikasi manipulasi 1.000.000
        payload = self._buat_payload_webhook(gross_amount='1000000.00')

        client = Client()
        url = reverse('midtrans_webhook')
        resp = client.post(url, data=json.dumps(payload), content_type='application/json')

        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()['error'], 'nominal_mismatch')

    def test_webhook_menolak_nominal_dengan_desimal_yang_tidak_sesuai(self):
        payload = self._buat_payload_webhook(gross_amount='5000000.99')
        response = Client().post(
            reverse('midtrans_webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'nominal_mismatch')

    def test_webhook_menyimpan_status_pending_untuk_polling(self):
        payload = self._buat_payload_webhook(
            transaction_status='pending',
            transaction_id='TRX-POLL-001',
        )
        response = Client().post(
            reverse('midtrans_webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.midtrans_transaction_status, 'pending')

    def test_webhook_json_bukan_object_ditolak(self):
        response = Client().post(
            reverse('midtrans_webhook'),
            data='[]',
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)

    # ── 4. Penanganan Status Transaksi Midtrans & Aturan Inti MBG ──

    def test_webhook_settlement_memverifikasi_booking_dan_mengunci_unit_otomatis(self):
        payload = self._buat_payload_webhook(
            transaction_status='settlement',
            payment_type='echannel',
            transaction_id='TRX-SETTLE-001',
        )

        client = Client()
        resp = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['status'], 'ok')

        # Cek PembayaranPesanan tercatat
        pembayaran = PembayaranPesanan.objects.filter(pesanan=self.pesanan, jenis='booking_fee').first()
        self.assertIsNotNone(pembayaran)
        self.assertEqual(pembayaran.nominal, self.pesanan.nominal_booking_fee)
        self.assertEqual(pembayaran.status, 'terverifikasi')
        self.assertIsNone(pembayaran.diverifikasi_oleh_id)
        self.assertEqual(pembayaran.saluran, 'va')
        self.assertEqual(pembayaran.nomor_referensi, 'TRX-SETTLE-001')
        customer = Client()
        customer.login(username='pembeli_midtrans', password='Password123!')
        success_page = customer.get(
            reverse('pesanan_berhasil', args=[self.pesanan.nomor_pesanan])
        )
        self.assertContains(success_page, 'Booking Aktif — Unit Dikunci')

        self.pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(self.pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')
        self.assertIsNotNone(self.pesanan.booking_berakhir_pada)

    def test_beli_langsung_webhook_memvalidasi_100_persen_ot_r_mengunci_dan_idempotent(self):
        pesanan = self._buat_pesanan_beli_langsung()
        payload = self._buat_payload_webhook(
            transaction_status='settlement',
            transaction_id='TRX-DIRECT-001',
            payment_type='qris',
        )

        response = Client().post(
            reverse('midtrans_webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['action'], 'direct_purchase_verified')
        pembayaran = PembayaranPesanan.objects.get(pesanan=pesanan)
        self.assertEqual(pembayaran.jenis, 'pelunasan')
        self.assertEqual(pembayaran.nominal, pesanan.harga_kendaraan)
        self.assertEqual(pembayaran.status, 'terverifikasi')
        self.assertIsNone(pembayaran.diverifikasi_oleh_id)
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'booking_aktif')
        self.assertEqual(pesanan.sisa_pembayaran(), Decimal('0'))
        self.assertEqual(self.produk.status_stok, 'in_review')

        repeated = Client().post(
            reverse('midtrans_webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(repeated.status_code, 200)
        self.assertEqual(repeated.json()['result']['action'], 'already_verified')
        self.assertEqual(PembayaranPesanan.objects.filter(pesanan=pesanan).count(), 1)

        customer = Client()
        customer.login(username='pembeli_midtrans', password='Password123!')
        detail = customer.get(reverse('pesanan_detail', args=[pesanan.nomor_pesanan]))
        self.assertContains(detail, 'Lunas — Menunggu Serah Terima')
        self.assertContains(detail, 'directPurchaseSuccessModalDetail')

        admin = Client()
        admin.login(username='admin_mbg', password='Password123!')
        panel = admin.get(reverse('admin_pesanan_pembelian'))
        self.assertContains(panel, pesanan.nomor_pesanan)
        self.assertContains(panel, 'Beli Langsung')
        selesai = admin.post(
            reverse('admin_selesaikan_pesanan_pembelian', args=[pesanan.pk])
        )
        self.assertEqual(selesai.status_code, 302)
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'selesai')
        self.assertEqual(self.produk.status_stok, 'sold')
        success_page = customer.get(
            reverse('pesanan_berhasil', args=[pesanan.nomor_pesanan])
        )
        self.assertContains(success_page, 'Pembelian Selesai')

    def test_webhook_capture_accept_memverifikasi_booking_dan_mengunci_unit(self):
        payload = self._buat_payload_webhook(
            transaction_status='capture',
            fraud_status='accept',
            payment_type='credit_card',
            transaction_id='TRX-CAP-ACCEPT',
        )

        client = Client()
        resp = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

        pembayaran = PembayaranPesanan.objects.filter(pesanan=self.pesanan, jenis='booking_fee').first()
        self.assertIsNotNone(pembayaran)
        self.assertEqual(pembayaran.status, 'terverifikasi')
        self.pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(self.pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')

    def test_webhook_capture_challenge_tidak_dihitung_sebagai_pembayaran(self):
        payload = self._buat_payload_webhook(
            transaction_status='capture',
            fraud_status='challenge',
            payment_type='credit_card',
            transaction_id='TRX-CAP-CHALLENGE',
        )
        response = Client().post(
            reverse('midtrans_webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['action'], 'challenge_review_required')
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=self.pesanan).exists())
        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.midtrans_transaction_status, 'challenge')

    def test_webhook_pending_tidak_mencatat_pembayaran_lunas(self):
        payload = self._buat_payload_webhook(
            transaction_status='pending',
            payment_type='qris',
            transaction_id='TRX-PENDING-001',
        )

        client = Client()
        resp = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

        # Tidak ada pembayaran yang berstatus terverifikasi
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=self.pesanan, status='terverifikasi').exists())
        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.status, 'menunggu_booking_fee')

    def test_webhook_expire_mereset_token_snap(self):
        self.pesanan.midtrans_snap_token = 'token-yang-sudah-expired'
        self.pesanan.midtrans_snap_redirect_url = 'https://snap.test/expire'
        self.pesanan.save()

        payload = self._buat_payload_webhook(
            transaction_status='expire',
            payment_type='bank_transfer',
            transaction_id='TRX-EXPIRED-001',
        )

        client = Client()
        resp = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.midtrans_snap_token, '')
        self.assertEqual(self.pesanan.midtrans_snap_redirect_url, '')
        self.assertEqual(self.pesanan.midtrans_transaction_status, 'expire')

    @mock.patch('urllib.request.urlopen')
    def test_retry_setelah_expire_membuat_order_id_baru_dan_mengabaikan_notifikasi_lama(self, mock_urlopen):
        old_order_id = self.pesanan.nomor_pesanan
        self.pesanan.midtrans_order_id = old_order_id
        self.pesanan.save(update_fields=['midtrans_order_id'])
        old_settlement = self._buat_payload_webhook(
            order_id=old_order_id,
            transaction_status='settlement',
            transaction_id='TRX-OLD-SETTLEMENT',
        )
        expiration = self._buat_payload_webhook(
            order_id=old_order_id,
            transaction_status='expire',
            transaction_id='TRX-OLD-EXPIRE',
        )
        stale_pending = self._buat_payload_webhook(
            order_id=old_order_id,
            transaction_status='pending',
            transaction_id='TRX-OLD-PENDING',
        )
        client = Client()
        response = client.post(
            reverse('midtrans_webhook'),
            data=json.dumps(expiration),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        response = client.post(
            reverse('midtrans_webhook'),
            data=json.dumps(stale_pending),
            content_type='application/json',
        )
        self.assertEqual(response.json()['result']['action'], 'ignored_stale_status')
        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.midtrans_transaction_status, 'expire')

        mock_response = mock.MagicMock()
        mock_response.read.return_value = json.dumps({
            'token': 'snap-token-after-expire',
            'redirect_url': 'https://app.sandbox.midtrans.com/snap/new-attempt',
        }).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = mock_response
        _, _, new_order_id = buat_snap_transaksi(self.pesanan)
        self.assertNotEqual(new_order_id, old_order_id)
        self.pesanan.refresh_from_db()
        self.assertIn(old_order_id, self.pesanan.midtrans_order_id_history)

        response = client.post(
            reverse('midtrans_webhook'),
            data=json.dumps(old_settlement),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['result']['action'], 'ignored_stale_transaction')
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=self.pesanan).exists())

    def test_webhook_cancel_dan_deny_menolak_pembayaran_pending(self):
        pembayaran = PembayaranPesanan.objects.create(
            pesanan=self.pesanan,
            jenis='booking_fee',
            nominal=self.pesanan.nominal_booking_fee,
            saluran='va',
            nomor_referensi='TRX-CANCEL-001',
            status='menunggu_verifikasi',
        )

        payload = self._buat_payload_webhook(
            transaction_status='cancel',
            payment_type='bank_transfer',
            transaction_id='TRX-CANCEL-001',
        )

        client = Client()
        resp = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)

        pembayaran.refresh_from_db()
        self.assertEqual(pembayaran.status, 'ditolak')

    # ── 5. Idempotensi & Notifikasi Terlambat ───────────────────────

    def test_webhook_idempotent_notifikasi_berulang(self):
        """Memastikan pengiriman notifikasi settlement berkali-kali tidak membuat duplikat PembayaranPesanan."""
        payload = self._buat_payload_webhook(
            transaction_status='settlement',
            transaction_id='TRX-IDEMPOTENT-001',
        )
        client = Client()

        # Panggilan pertama
        resp1 = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(resp1.json()['result']['action'], 'booking_fee_verified')

        # Panggilan kedua (notifikasi berulang dari Midtrans)
        resp2 = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()['result']['action'], 'already_verified')

        # Verifikasi tepat hanya 1 pembayaran di database
        total_pembayaran = PembayaranPesanan.objects.filter(pesanan=self.pesanan, jenis='booking_fee').count()
        self.assertEqual(total_pembayaran, 1)

    def test_settlement_memverifikasi_pembayaran_midtrans_yang_sebelumnya_menunggu(self):
        payload = self._buat_payload_webhook(
            transaction_status='settlement',
            transaction_id='TRX-PREEXISTING-SETTLEMENT',
        )
        self.pesanan.midtrans_transaction_status = 'pending'
        self.pesanan.save(update_fields=['midtrans_transaction_status'])
        pembayaran = PembayaranPesanan.objects.create(
            pesanan=self.pesanan,
            jenis='booking_fee',
            nominal=self.pesanan.nominal_booking_fee,
            saluran='qris',
            nomor_referensi='TRX-PREEXISTING-SETTLEMENT',
            status='menunggu_verifikasi',
        )

        result = proses_notifikasi_webhook(payload)

        self.assertTrue(result['success'])
        self.pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        pembayaran.refresh_from_db()
        self.assertEqual(self.pesanan.midtrans_transaction_status, 'settlement')
        self.assertEqual(pembayaran.status, 'terverifikasi')
        self.assertEqual(self.pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')
        self.assertEqual(
            PembayaranPesanan.objects.filter(pesanan=self.pesanan, jenis='booking_fee').count(),
            1,
        )

    def test_webhook_idempotent_jika_pembayaran_midtrans_sudah_terverifikasi(self):
        payload = self._buat_payload_webhook(
            transaction_status='settlement',
            transaction_id='TRX-IDEMPOTENT-002',
        )
        client = Client()
        client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')

        pembayaran = PembayaranPesanan.objects.get(pesanan=self.pesanan, jenis='booking_fee')
        self.assertEqual(pembayaran.status, 'terverifikasi')

        # Notifikasi terlambat/berulang
        resp = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['result']['action'], 'already_verified')

    def test_webhook_menangani_pesanan_dibatalkan_dengan_aman(self):
        """Jika pesanan sudah dibatalkan pelanggan saat notifikasi webhook tiba, tidak menimbulkan error tak tertangani."""
        self.pesanan.batalkan_tanpa_pembayaran(self.pembeli, 'Berubah pikiran')

        payload = self._buat_payload_webhook(
            transaction_status='settlement',
            transaction_id='TRX-LATE-001',
        )
        client = Client()
        resp = client.post(reverse('midtrans_webhook'), data=json.dumps(payload), content_type='application/json')

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()['result']['action'], 'ignored_order_inactive')

    # ── 6. UI & Template Context Keamanan ──────────────────────────

    def test_pesanan_berhasil_menyediakan_client_key_dan_tidak_membocorkan_server_key(self):
        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')
        url = reverse('pesanan_berhasil', args=[self.pesanan.nomor_pesanan])
        resp = client.get(url)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context['midtrans_client_key'], DUMMY_CLIENT_KEY)
        self.assertIn('midtrans_snap_js_url', resp.context)

        # Server key tidak boleh pernah muncul di HTML
        html = resp.content.decode('utf-8')
        self.assertNotIn(DUMMY_SERVER_KEY, html)
        self.assertIn(DUMMY_CLIENT_KEY, html)
        self.assertIn('btnPaySnap', html)
        self.assertIn('payment-status', html)

    def test_status_pembayaran_hanya_dapat_diakses_pemilik(self):
        client = Client()
        client.login(username='pembeli_lain', password='Password123!')
        response = client.get(
            reverse('midtrans_payment_status', args=[self.pesanan.nomor_pesanan])
        )
        self.assertEqual(response.status_code, 403)

    @mock.patch('toko.views.sinkronkan_status_midtrans')
    def test_status_pembayaran_menunjukkan_pending_dan_booking_fee_tercatat(self, mock_sync):
        self.pesanan.midtrans_order_id = self.pesanan.nomor_pesanan
        self.pesanan.midtrans_transaction_status = 'pending'
        self.pesanan.save(update_fields=[
            'midtrans_order_id', 'midtrans_transaction_status',
        ])
        mock_sync.return_value = {'success': True, 'action': 'pending_acknowledged'}
        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')
        response = client.get(
            reverse('midtrans_payment_status', args=[self.pesanan.nomor_pesanan])
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['transaction_status'], 'pending')
        self.assertTrue(response.json()['can_pay'])
        mock_sync.assert_called_once()

    @mock.patch('urllib.request.urlopen')
    def test_get_status_settlement_mengunci_booking_qris_otomatis(self, mock_urlopen):
        self.pesanan.midtrans_order_id = self.pesanan.nomor_pesanan
        self.pesanan.save(update_fields=['midtrans_order_id'])
        api_response = {
            'order_id': self.pesanan.midtrans_order_id,
            'status_code': '200',
            'gross_amount': f'{int(self.pesanan.nominal_booking_fee)}.00',
            'transaction_id': 'TRX-GET-STATUS-QRIS',
            'transaction_status': 'settlement',
            'payment_type': 'qris',
            'settlement_time': '2026-11-16 08:02:00',
        }
        response_context = mock.MagicMock()
        response_context.read.return_value = json.dumps(api_response).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = response_context

        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')
        response = client.get(
            reverse('midtrans_payment_status', args=[self.pesanan.nomor_pesanan])
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['transaction_status'], 'settlement')
        self.assertEqual(data['payment_status'], 'terverifikasi')
        self.assertFalse(data['can_pay'])
        self.assertFalse(data['status_sync_error'])

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            f'https://api.sandbox.midtrans.com/v2/{self.pesanan.midtrans_order_id}/status',
        )
        self.assertEqual(request.method, 'GET')
        expected_auth = base64.b64encode(f'{DUMMY_SERVER_KEY}:'.encode()).decode()
        self.assertEqual(request.headers.get('Authorization'), f'Basic {expected_auth}')

        pembayaran = PembayaranPesanan.objects.get(pesanan=self.pesanan, jenis='booking_fee')
        self.assertEqual(pembayaran.status, 'terverifikasi')
        self.assertEqual(pembayaran.saluran, 'qris')
        self.pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(self.pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')

        detail = client.get(reverse('pesanan_detail', args=[self.pesanan.nomor_pesanan]))
        self.assertContains(detail, 'Booking Aktif')
        self.assertContains(detail, 'Terverifikasi')

    @mock.patch('urllib.request.urlopen')
    def test_get_status_beli_langsung_memvalidasi_pembayaran_penuh_otomatis(self, mock_urlopen):
        pesanan = self._buat_pesanan_beli_langsung()
        api_response = {
            'order_id': pesanan.midtrans_order_id,
            'status_code': '200',
            'gross_amount': f'{int(pesanan.harga_kendaraan)}.00',
            'transaction_id': 'TRX-GET-DIRECT-001',
            'transaction_status': 'settlement',
            'payment_type': 'qris',
            'settlement_time': '2026-11-16 08:02:00',
        }
        response_context = mock.MagicMock()
        response_context.read.return_value = json.dumps(api_response).encode('utf-8')
        mock_urlopen.return_value.__enter__.return_value = response_context

        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')
        response = client.get(
            reverse('midtrans_payment_status', args=[pesanan.nomor_pesanan])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['purchase_type'], 'beli_langsung')
        self.assertEqual(response.json()['payment_status'], 'terverifikasi')
        self.assertEqual(response.json()['amount_due'], int(pesanan.harga_kendaraan))
        self.assertFalse(response.json()['can_pay'])
        pembayaran = PembayaranPesanan.objects.get(pesanan=pesanan)
        self.assertEqual(pembayaran.jenis, 'pelunasan')
        self.assertEqual(pembayaran.status, 'terverifikasi')
        pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')

    def test_panel_admin_menampilkan_booking_midtrans_yang_sudah_otomatis_terverifikasi(self):
        payload = self._buat_payload_webhook(
            transaction_status='settlement',
            transaction_id='TRX-ADMIN-VERIFY-001',
        )
        webhook = Client().post(
            reverse('midtrans_webhook'),
            data=json.dumps(payload),
            content_type='application/json',
        )
        self.assertEqual(webhook.status_code, 200)

        admin = Client()
        admin.login(username='admin_mbg', password='Password123!')
        dashboard = admin.get(reverse('dashboard_admin'))
        self.assertContains(dashboard, 'Transaksi &amp; DP')
        self.assertContains(dashboard, 'Pembayaran menunggu verifikasi')
        self.assertEqual(dashboard.context['total_transaksi'], 1)
        self.assertEqual(dashboard.context['transaksi_berhasil'], 1)
        self.assertEqual(dashboard.context['transaksi_pending'], 0)
        self.assertContains(dashboard, self.pesanan.nomor_pesanan)
        self.assertNotContains(dashboard, '/admin/toko/transaksi/')
        self.assertRedirects(
            admin.get('/admin/toko/transaksi/'),
            reverse('admin_pesanan_pembelian'),
            fetch_redirect_response=False,
        )
        page = admin.get(reverse('admin_pesanan_pembelian'))
        self.assertContains(page, self.pesanan.nomor_pesanan)
        self.assertContains(page, 'Budi Santoso')
        self.assertContains(page, self.produk.nama)
        self.assertContains(page, '0 Pembayaran menunggu verifikasi')
        self.assertNotContains(page, 'Verifikasi Booking Fee')
        payment = PembayaranPesanan.objects.get(pesanan=self.pesanan)
        self.assertEqual(payment.status, 'terverifikasi')
        self.pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(self.pesanan.status, 'booking_aktif')
        self.assertEqual(self.produk.status_stok, 'in_review')

    @mock.patch('urllib.request.urlopen', side_effect=URLError('network unavailable'))
    def test_get_status_error_dilaporkan_tanpa_mengubah_status_pesanan(self, mock_urlopen):
        self.pesanan.midtrans_order_id = self.pesanan.nomor_pesanan
        self.pesanan.save(update_fields=['midtrans_order_id'])
        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')

        with self.assertLogs('toko.midtrans_service', level='WARNING'):
            response = client.get(
                reverse('midtrans_payment_status', args=[self.pesanan.nomor_pesanan])
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['status_sync_error'])
        self.assertIn('belum dapat disinkronkan', response.json()['message'])
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=self.pesanan).exists())
        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.status, 'menunggu_booking_fee')

    def test_get_status_menolak_order_id_dan_nominal_yang_tidak_cocok(self):
        response = {
            'order_id': self.pesanan.nomor_pesanan,
            'gross_amount': f'{int(self.pesanan.nominal_booking_fee)}.00',
            'transaction_status': 'settlement',
        }
        self.pesanan.midtrans_order_id = f'{self.pesanan.nomor_pesanan}-CURRENT'
        self.pesanan.save(update_fields=['midtrans_order_id'])

        result = proses_status_get_midtrans(
            response, expected_order_id=self.pesanan.midtrans_order_id,
        )
        self.assertEqual(result['error'], 'order_id_mismatch')

        response['order_id'] = self.pesanan.midtrans_order_id
        response['gross_amount'] = '1.00'
        result = proses_status_get_midtrans(
            response, expected_order_id=self.pesanan.midtrans_order_id,
        )
        self.assertEqual(result['error'], 'nominal_mismatch')
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=self.pesanan).exists())

        response['gross_amount'] = f'{int(self.pesanan.nominal_booking_fee)}.00'
        response.pop('transaction_status')
        result = proses_status_get_midtrans(
            response, expected_order_id=self.pesanan.midtrans_order_id,
        )
        self.assertEqual(result['error'], 'invalid_response')

    def test_channel_pembayaran_midtrans_dipetakan_dengan_tepat(self):
        self.assertEqual(petakan_saluran_midtrans('qris'), 'qris')
        self.assertEqual(petakan_saluran_midtrans('gopay'), 'ewallet')
        self.assertEqual(petakan_saluran_midtrans('credit_card'), 'kartu')
        self.assertEqual(petakan_saluran_midtrans('cstore'), 'ritel')
        self.assertEqual(petakan_saluran_midtrans('future_payment_type'), 'lainnya')

    def test_checkout_menjelaskan_kredit_tanpa_mengklaim_integrasi_leasing(self):
        self.pesanan.batalkan_tanpa_pembayaran(
            self.pembeli,
            'Melepas unit sebelum memeriksa informasi checkout.',
        )
        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')
        response = client.get(reverse('checkout_pembelian', args=[self.produk.pk]))
        self.assertContains(response, 'Belum tersedia integrasi bank atau leasing')

    def test_admin_mencatat_dan_memverifikasi_dp_dan_pelunasan(self):
        booking = PembayaranPesanan.objects.create(
            pesanan=self.pesanan,
            jenis='booking_fee',
            nominal=self.pesanan.nominal_booking_fee,
            saluran='va',
            nomor_referensi='TRX-BOOKING-MANUAL-FLOW',
        )
        booking.verifikasi(self.admin)

        admin = Client()
        admin.login(username='admin_mbg', password='Password123!')
        record_url = reverse('admin_catat_pembayaran_pesanan', args=[self.pesanan.pk])
        response = admin.post(record_url, {
            'jenis': 'dp',
            'nominal': '50000000',
            'saluran': 'transfer',
            'nomor_referensi': 'TRF-DP-001',
            'catatan': 'DP manual',
        })
        self.assertEqual(response.status_code, 302)
        dp = PembayaranPesanan.objects.get(pesanan=self.pesanan, jenis='dp')
        self.assertEqual(dp.status, 'menunggu_verifikasi')
        self.assertIsNotNone(dp.tanggal_bayar)

        admin.post(reverse('admin_verifikasi_pembayaran_pesanan', args=[dp.pk]))
        dp.refresh_from_db()
        self.assertEqual(dp.catatan_verifikasi, 'DP manual')
        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.sisa_pembayaran(), Decimal('195000000'))

        admin.post(record_url, {
            'jenis': 'pelunasan',
            'nominal': '195000000',
            'saluran': 'tunai',
            'nomor_referensi': '',
            'catatan': 'Pelunasan di showroom',
        })
        pelunasan = PembayaranPesanan.objects.get(pesanan=self.pesanan, jenis='pelunasan')
        self.assertEqual(pelunasan.saluran, 'tunai')
        admin.post(reverse('admin_verifikasi_pembayaran_pesanan', args=[pelunasan.pk]))
        self.pesanan.refresh_from_db()
        self.assertEqual(self.pesanan.sisa_pembayaran(), Decimal('0'))

    def test_pelanggan_tidak_dapat_mencatat_pembayaran_manual_melalui_panel(self):
        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')
        response = client.post(
            reverse('admin_catat_pembayaran_pesanan', args=[self.pesanan.pk]),
            {
                'jenis': 'booking_fee',
                'nominal': str(self.pesanan.nominal_booking_fee),
                'saluran': 'tunai',
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(PembayaranPesanan.objects.filter(pesanan=self.pesanan).exists())

    def test_pelanggan_mengajukan_refund_admin_memproses_dan_mencatat_pengembalian(self):
        pembayaran = PembayaranPesanan.objects.create(
            pesanan=self.pesanan,
            jenis='booking_fee',
            nominal=self.pesanan.nominal_booking_fee,
            saluran='va',
            nomor_referensi='TRX-REFUND-001',
        )
        pembayaran.verifikasi(self.admin)

        customer = Client()
        customer.login(username='pembeli_midtrans', password='Password123!')
        response = customer.post(
            reverse('pesanan_detail', args=[self.pesanan.nomor_pesanan]),
            {'alasan': 'Perubahan rencana pembelian'},
        )
        self.assertEqual(response.status_code, 302)
        refund = self.pesanan.refund.get()
        self.assertEqual(refund.pembayaran, pembayaran)
        self.assertEqual(refund.status, 'diajukan')

        admin = Client()
        admin.login(username='admin_mbg', password='Password123!')
        admin.post(reverse('admin_setujui_refund_pesanan', args=[refund.pk]))
        refund.refresh_from_db()
        self.pesanan.refresh_from_db()
        self.produk.refresh_from_db()
        self.assertEqual(refund.status, 'disetujui')
        self.assertEqual(self.pesanan.status, 'dibatalkan')
        self.assertEqual(self.produk.status_stok, 'ready')

        admin.post(
            reverse('admin_catat_pengembalian_refund', args=[refund.pk]),
            {'nomor_referensi': 'REFUND-001', 'catatan': 'Dana dikembalikan'},
        )
        refund.refresh_from_db()
        self.assertEqual(refund.status, 'dikembalikan')
        self.assertEqual(refund.nomor_referensi_pengembalian, 'REFUND-001')

    def test_snap_token_endpoint_hanya_menerima_post(self):
        client = Client()
        client.login(username='pembeli_midtrans', password='Password123!')
        response = client.get(
            reverse('midtrans_snap_token', args=[self.pesanan.nomor_pesanan])
        )
        self.assertEqual(response.status_code, 405)
