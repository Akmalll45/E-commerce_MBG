"""
Layanan Integrasi Midtrans Snap Sandbox (Sprint 3)
Mobil Bekas Garasi (MBG) Makassar.

Fitur:
1. Pembuatan transaksi Snap dari backend (Snap Token & Redirect URL).
2. Pencegahan transaksi ganda (idempotensi pembuatan token dan penanganan pembayaran).
3. Validasi webhook Midtrans (SHA512 signature, order_id, nominal gross_amount).
4. Penanganan status transaksi: pending, settlement, capture, expire, cancel, deny.
5. Kepatuhan domain MBG: Pembayaran sukses mencatat PembayaranPesanan berstatus
   'menunggu_verifikasi'; unit kendaraan HANYA dikunci setelah diverifikasi oleh staf admin.
6. Keamanan: Server Key hanya digunakan di sisi backend, tidak pernah diekspos ke klien.
"""
import base64
import hashlib
import hmac
import json
import logging
import uuid
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import PembayaranPesanan, PesananPembelian, RiwayatPesanan

logger = logging.getLogger(__name__)


class MidtransStatusError(Exception):
    """Raised when the authenticated Midtrans Get Status request cannot be trusted."""


def hitung_signature(order_id, status_code, gross_amount, server_key=None):
    """
    Menghitung signature SHA512 sesuai spesifikasi Midtrans:
    SHA512(order_id + status_code + gross_amount + server_key)
    """
    server_key = server_key if server_key is not None else getattr(settings, 'MIDTRANS_SERVER_KEY', '')
    raw = f"{order_id}{status_code}{gross_amount}{server_key}"
    return hashlib.sha512(raw.encode('utf-8')).hexdigest().lower()


def verifikasi_signature(order_id, status_code, gross_amount, signature_key, server_key=None):
    """
    Memvalidasi signature SHA512 dari webhook Midtrans menggunakan perbandingan constant-time.
    """
    if not signature_key:
        return False
    server_key = server_key if server_key is not None else getattr(
        settings, 'MIDTRANS_SERVER_KEY', ''
    )
    if not server_key:
        return False
    expected = hitung_signature(order_id, status_code, gross_amount, server_key=server_key)
    return hmac.compare_digest(expected, str(signature_key).strip().lower())


def petakan_saluran_midtrans(payment_type):
    """
    Memetakan payment_type Midtrans ke pilihan saluran pada model PembayaranPesanan.
    SALURAN_CHOICES = ('transfer', 'va', 'qris', 'ewallet', 'kartu', 'ritel', 'tunai')
    """
    tipe = (payment_type or '').lower()
    if 'qris' in tipe:
        return 'qris'
    if 'gopay' in tipe or 'shopeepay' in tipe:
        return 'ewallet'
    if 'credit_card' in tipe:
        return 'kartu'
    if 'cstore' in tipe or 'indomaret' in tipe or 'alfamart' in tipe:
        return 'ritel'
    if 'va' in tipe or 'bank_transfer' in tipe or 'echannel' in tipe or 'permata' in tipe:
        return 'va'
    return 'transfer'


def buat_snap_transaksi(pesanan, server_key=None, snap_url=None):
    """
    Membuat transaksi Snap ke API Midtrans.
    Mencegah transaksi ganda dengan mengembalikan token yang sudah tersimpan jika masih berlaku.

    Kembalian:
        (snap_token, redirect_url, order_id)
    """
    server_key = server_key if server_key is not None else getattr(settings, 'MIDTRANS_SERVER_KEY', '')
    if not server_key:
        key_name = (
            'MIDTRANS_SANDBOX_SERVER_KEY'
            if settings.DEBUG
            else 'MIDTRANS_SERVER_KEY'
        )
        raise ValueError(f'{key_name} belum dikonfigurasi.')

    snap_url = snap_url if snap_url is not None else getattr(
        settings, 'MIDTRANS_SNAP_URL', 'https://app.sandbox.midtrans.com/snap/v1/transactions'
    )

    with transaction.atomic():
        pesanan = PesananPembelian.objects.select_for_update().get(pk=pesanan.pk)
        if pesanan.status != 'menunggu_booking_fee':
            raise ValueError('Pesanan tidak dalam status menunggu Booking Fee.')

        pembayaran_aktif = pesanan.pembayaran.filter(
            jenis='booking_fee',
            status__in=['terverifikasi', 'menunggu_verifikasi'],
        ).first()
        if pembayaran_aktif:
            if pembayaran_aktif.status == 'terverifikasi':
                raise ValueError('Booking Fee untuk pesanan ini sudah diverifikasi.')
            raise ValueError('Pembayaran Booking Fee sudah tercatat dan sedang menunggu verifikasi staf MBG.')

        if pesanan.midtrans_snap_token:
            return (
                pesanan.midtrans_snap_token,
                pesanan.midtrans_snap_redirect_url,
                pesanan.midtrans_order_id,
            )

        gross_amount = int(pesanan.nominal_booking_fee)
        if (
            not pesanan.midtrans_order_id
            or pesanan.midtrans_transaction_status in ('expire', 'cancel', 'deny')
        ):
            # Midtrans requires a fresh order ID after a terminal transaction.
            if (
                pesanan.midtrans_order_id
                and pesanan.midtrans_order_id not in pesanan.midtrans_order_id_history
            ):
                pesanan.midtrans_order_id_history = [
                    *pesanan.midtrans_order_id_history, pesanan.midtrans_order_id,
                ]
            pesanan.midtrans_order_id = f'{pesanan.nomor_pesanan}-{uuid.uuid4().hex[:16].upper()}'
            pesanan.midtrans_transaction_status = ''
            pesanan.save(update_fields=[
                'midtrans_order_id', 'midtrans_order_id_history',
                'midtrans_transaction_status', 'updated_at',
            ])
        order_id = pesanan.midtrans_order_id

        customer_email = (
            pesanan.email.strip()
            if pesanan.email else f'customer.{pesanan.pelanggan_id}@mbgmakassar.id'
        )
        nama_depan = pesanan.nama_pelanggan.strip() or 'Pelanggan MBG'
        payload = {
            'transaction_details': {
                'order_id': order_id,
                'gross_amount': gross_amount,
            },
            'customer_details': {
                'first_name': nama_depan,
                'email': customer_email,
                'phone': pesanan.no_whatsapp,
            },
            'item_details': [{
                'id': f'BF-{pesanan.sku_kendaraan[:20]}',
                'price': gross_amount,
                'quantity': 1,
                'name': f'Booking Fee {pesanan.nama_kendaraan[:35]}',
            }],
        }

        auth_str = base64.b64encode(f'{server_key}:'.encode('utf-8')).decode('ascii')
        req = urllib.request.Request(
            snap_url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': f'Basic {auth_str}',
            },
            method='POST',
        )

        api_error = None
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as exc:
            logger.error('Midtrans Snap API Error [HTTP %s]', exc.code)
            api_error = ValueError(f'Gagal menghubungi Midtrans Snap: HTTP {exc.code}.')
        except (
            urllib.error.URLError, OSError, TimeoutError,
            json.JSONDecodeError, UnicodeDecodeError,
        ) as exc:
            logger.error('Midtrans Snap connection/response error [%s]', type(exc).__name__)
            api_error = ValueError('Gagal menghubungi gateway Midtrans.')

        if api_error is None:
            if not isinstance(data, dict):
                api_error = ValueError('Respons Midtrans Snap tidak valid.')
            else:
                token = data.get('token')
                redirect_url = data.get('redirect_url', '')
                if not isinstance(token, str) or not token.strip():
                    api_error = ValueError('Midtrans tidak mengembalikan Snap token.')
                elif not isinstance(redirect_url, str):
                    api_error = ValueError('URL redirect Midtrans tidak valid.')

        if api_error is None:
            pesanan.midtrans_snap_token = token
            pesanan.midtrans_snap_redirect_url = redirect_url
            pesanan.save(update_fields=[
                'midtrans_snap_token', 'midtrans_snap_redirect_url', 'updated_at',
            ])
            return token, redirect_url, order_id

    # Commit the attempt ID even if the request times out so concurrent/retry calls
    # cannot create a second transaction for the same payment attempt.
    raise api_error


def _proses_status_pembayaran(data, server_key=None, sekarang=None, require_signature=True):
    """
    Memproses notifikasi webhook HTTP POST dari Midtrans secara aman dan idempotent.

    Validasi:
    1. Signature SHA512.
    2. Keberadaan pesanan berdasarkan order_id.
    3. Kecocokan nominal gross_amount dengan nominal Booking Fee pesanan.

    Aturan Utama MBG:
    Pembayaran sukses (settlement / capture) mencatat PembayaranPesanan berstatus
    'menunggu_verifikasi'. Status pesanan tetap 'menunggu_booking_fee' dan stok kendaraan
    tetap 'ready'. Unit hanya dikunci saat admin memverifikasi pembayaran.
    """
    sekarang = sekarang or timezone.now()

    order_id = str(data.get('order_id', '')).strip()
    status_code = str(data.get('status_code', '')).strip()
    gross_amount = str(data.get('gross_amount', '')).strip()
    signature_key = str(data.get('signature_key', '')).strip()

    transaction_id = str(data.get('transaction_id', '')).strip()
    transaction_status = str(data.get('transaction_status', '')).strip().lower()
    fraud_status = str(data.get('fraud_status', '')).strip().lower()
    if transaction_status == 'capture' and fraud_status == 'deny':
        transaction_status = 'deny'
    payment_type = str(data.get('payment_type', '')).strip()
    settlement_time = data.get('settlement_time') or data.get('transaction_time')

    # 1. Validasi Signature
    if require_signature and not verifikasi_signature(
        order_id, status_code, gross_amount, signature_key, server_key=server_key
    ):
        logger.warning("Midtrans Webhook: Signature tidak valid untuk order_id=%s", order_id)
        return {'success': False, 'error': 'invalid_signature', 'message': 'Signature tidak valid.'}

    # 2. Cari Pesanan Pembelian
    pesanan = PesananPembelian.objects.filter(midtrans_order_id=order_id).first()
    pesanan_lama = False
    if not pesanan:
        pesanan = PesananPembelian.objects.filter(
            midtrans_order_id_history__contains=[order_id],
        ).first()
        pesanan_lama = pesanan is not None
    if not pesanan:
        # Older Snap transactions used the purchase number as their order ID.
        pesanan = PesananPembelian.objects.filter(
            nomor_pesanan=order_id,
            midtrans_order_id=order_id,
        ).first()
    if not pesanan:
        logger.warning("Midtrans Webhook: Pesanan tidak ditemukan untuk order_id=%s", order_id)
        return {'success': False, 'error': 'order_not_found', 'message': f'Pesanan {order_id} tidak ditemukan.'}

    # 3. Validasi Nominal Pembayaran
    try:
        nominal_notif = Decimal(gross_amount)
    except (InvalidOperation, ValueError):
        return {'success': False, 'error': 'invalid_amount', 'message': 'Nominal pembayaran tidak valid.'}

    if not nominal_notif.is_finite() or nominal_notif != pesanan.nominal_booking_fee:
        logger.warning(
            "Midtrans Webhook: Mismatch nominal order_id=%s. Notif=%s, Pesanan=%s",
            order_id, nominal_notif, pesanan.nominal_booking_fee
        )
        return {
            'success': False,
            'error': 'nominal_mismatch',
            'message': f'Nominal tidak cocok: diterima {nominal_notif}, tagihan {pesanan.nominal_booking_fee}.'
        }

    if pesanan_lama:
        return {
            'success': True,
            'action': 'ignored_stale_transaction',
            'pesanan_id': pesanan.pk,
        }

    # 4. Proses Transaksi secara Idempotent dengan Row-Level Lock
    with transaction.atomic():
        pesanan = PesananPembelian.objects.select_for_update().get(pk=pesanan.pk)

        if order_id != pesanan.midtrans_order_id:
            return {
                'success': True,
                'action': 'ignored_stale_transaction',
                'pesanan_id': pesanan.pk,
            }

        # Cek jika pesanan sudah dibatalkan atau kedaluwarsa (notifikasi terlambat)
        if pesanan.status in ('dibatalkan', 'kedaluwarsa'):
            logger.info("Midtrans Webhook: Pesanan %s sudah berstatus %s.", pesanan.nomor_pesanan, pesanan.status)
            return {
                'success': True,
                'action': 'ignored_order_inactive',
                'pesanan_id': pesanan.pk,
                'status_pesanan': pesanan.status,
            }

        nomor_referensi = transaction_id or order_id
        pembayaran_booking = PembayaranPesanan.objects.select_for_update().filter(
            pesanan=pesanan, jenis='booking_fee', nomor_referensi=nomor_referensi,
        ).first()
        status_saat_ini = pesanan.midtrans_transaction_status
        status_terminal = ('expire', 'cancel', 'deny')

        if (
            status_saat_ini in status_terminal
            and transaction_status not in status_terminal
            and transaction_status not in ('settlement', 'capture')
        ):
            return {
                'success': True,
                'action': 'ignored_stale_status',
                'pesanan_id': pesanan.pk,
            }

        # A. Pembayaran Sukses (settlement atau capture yang di-accept)
        if transaction_status == 'settlement' or (transaction_status == 'capture' and fraud_status == 'accept'):
            if pembayaran_booking:
                if pembayaran_booking.status == 'terverifikasi':
                    # Sudah diverifikasi oleh admin sebelumnya (idempotent)
                    return {
                        'success': True,
                        'action': 'already_verified',
                        'pesanan_id': pesanan.pk,
                        'pembayaran_id': pembayaran_booking.pk,
                    }
                if pembayaran_booking.status == 'menunggu_verifikasi':
                    if pesanan.midtrans_transaction_status != transaction_status:
                        pesanan.midtrans_transaction_status = transaction_status
                        pesanan.save(update_fields=['midtrans_transaction_status', 'updated_at'])
                    return {
                        'success': True,
                        'action': 'already_recorded',
                        'pesanan_id': pesanan.pk,
                        'pembayaran_id': pembayaran_booking.pk,
                    }
                status_sebelumnya = pembayaran_booking.status
                pembayaran_booking.status = 'menunggu_verifikasi'
                pembayaran_booking.catatan_verifikasi = (
                    f'Midtrans mengonfirmasi {transaction_status} setelah status '
                    f'{status_sebelumnya}. Menunggu verifikasi admin MBG.'
                )
                pembayaran_booking.save(update_fields=[
                    'status', 'catatan_verifikasi', 'updated_at',
                ])
                pesanan.midtrans_transaction_status = transaction_status
                pesanan.save(update_fields=['midtrans_transaction_status', 'updated_at'])
                return {
                    'success': True,
                    'action': 'payment_recorded',
                    'pesanan_id': pesanan.pk,
                    'pembayaran_id': pembayaran_booking.pk,
                }

            pembayaran_lain = PembayaranPesanan.objects.select_for_update().filter(
                pesanan=pesanan,
                jenis='booking_fee',
                status__in=['terverifikasi', 'menunggu_verifikasi'],
            ).first()

            # Catat pembayaran baru
            waktu_bayar = parse_datetime(str(settlement_time)) if settlement_time else sekarang
            if not waktu_bayar:
                waktu_bayar = sekarang
            elif timezone.is_naive(waktu_bayar):
                waktu_bayar = timezone.make_aware(waktu_bayar)

            saluran = petakan_saluran_midtrans(payment_type)
            pembayaran = PembayaranPesanan.objects.create(
                pesanan=pesanan,
                jenis='booking_fee',
                nominal=pesanan.nominal_booking_fee,
                saluran=saluran,
                nomor_referensi=transaction_id or order_id,
                tanggal_bayar=waktu_bayar,
                status='menunggu_verifikasi',
                catatan_verifikasi=(
                    f"Midtrans {transaction_status} ({payment_type}). "
                    f"TRX: {transaction_id}. "
                    + (
                        'Transaksi tambahan; tinjau kemungkinan pembayaran ganda sebelum diproses.'
                        if pembayaran_lain else 'Menunggu verifikasi admin MBG.'
                    )
                ),
            )
            pesanan.midtrans_transaction_status = transaction_status
            pesanan.save(update_fields=['midtrans_transaction_status', 'updated_at'])

            # Catat pada riwayat status pesanan
            RiwayatPesanan.objects.create(
                pesanan=pesanan,
                status_dari=pesanan.status,
                status_ke=pesanan.status,
                catatan=(
                    f"Pembayaran Booking Fee diterima dari Midtrans ({payment_type}, TRX: {transaction_id}). "
                    f"Menunggu verifikasi admin MBG."
                ),
            )

            if pembayaran_lain:
                logger.error(
                    'Additional successful Midtrans payment requires review for order %s.',
                    pesanan.nomor_pesanan,
                )
                return {
                    'success': True,
                    'action': 'duplicate_payment_review_required',
                    'pesanan_id': pesanan.pk,
                    'pembayaran_id': pembayaran.pk,
                }
            return {
                'success': True,
                'action': 'payment_recorded',
                'pesanan_id': pesanan.pk,
                'pembayaran_id': pembayaran.pk,
            }

        # B. Status Pending
        if transaction_status == 'pending':
            if status_saat_ini not in ('', 'pending'):
                return {
                    'success': True,
                    'action': 'ignored_stale_status',
                    'pesanan_id': pesanan.pk,
                }
            if pembayaran_booking and pembayaran_booking.status in (
                'menunggu_verifikasi', 'terverifikasi',
            ):
                return {
                    'success': True,
                    'action': 'ignored_stale_status',
                    'pesanan_id': pesanan.pk,
                    'pembayaran_id': pembayaran_booking.pk,
                }
            pesanan.midtrans_transaction_status = 'pending'
            pesanan.save(update_fields=['midtrans_transaction_status', 'updated_at'])
            return {
                'success': True,
                'action': 'pending_acknowledged',
                'pesanan_id': pesanan.pk,
            }

        if transaction_status == 'capture' and fraud_status == 'challenge':
            if status_saat_ini not in ('', 'pending', 'challenge'):
                return {
                    'success': True,
                    'action': 'ignored_stale_status',
                    'pesanan_id': pesanan.pk,
                }
            if pembayaran_booking and pembayaran_booking.status in (
                'menunggu_verifikasi', 'terverifikasi',
            ):
                return {
                    'success': True,
                    'action': 'ignored_stale_status',
                    'pesanan_id': pesanan.pk,
                    'pembayaran_id': pembayaran_booking.pk,
                }
            pesanan.midtrans_transaction_status = 'challenge'
            pesanan.save(update_fields=['midtrans_transaction_status', 'updated_at'])
            return {
                'success': True,
                'action': 'challenge_review_required',
                'pesanan_id': pesanan.pk,
            }

        # C. Status Expire, Cancel, Deny
        if transaction_status in ('expire', 'cancel', 'deny'):
            if status_saat_ini in status_terminal and transaction_status != status_saat_ini:
                return {
                    'success': True,
                    'action': 'ignored_stale_status',
                    'pesanan_id': pesanan.pk,
                }
            if status_saat_ini in ('settlement', 'capture') or (
                pembayaran_booking and pembayaran_booking.status == 'terverifikasi'
            ):
                return {
                    'success': True,
                    'action': 'ignored_stale_status',
                    'pesanan_id': pesanan.pk,
                    'pembayaran_id': pembayaran_booking.pk,
                }
            if pembayaran_booking and pembayaran_booking.status == 'menunggu_verifikasi':
                pembayaran_booking.status = 'ditolak'
                pembayaran_booking.catatan_verifikasi = (
                    f"Transaksi {transaction_status} di Midtrans ({payment_type}). TRX: {transaction_id}."
                )
                pembayaran_booking.save(update_fields=['status', 'catatan_verifikasi', 'updated_at'])

            pesanan.midtrans_transaction_status = transaction_status
            pesanan.midtrans_snap_token = ''
            pesanan.midtrans_snap_redirect_url = ''
            pesanan.save(update_fields=[
                'midtrans_transaction_status', 'midtrans_snap_token',
                'midtrans_snap_redirect_url', 'updated_at',
            ])

            RiwayatPesanan.objects.create(
                pesanan=pesanan,
                status_dari=pesanan.status,
                status_ke=pesanan.status,
                catatan=f"Transaksi Midtrans {transaction_status} ({payment_type}, TRX: {transaction_id}).",
            )

            return {
                'success': True,
                'action': f"{transaction_status}_recorded",
                'pesanan_id': pesanan.pk,
            }

    return {
        'success': True,
        'action': 'status_unhandled',
        'status': transaction_status,
        'pesanan_id': pesanan.pk,
    }


def proses_notifikasi_webhook(data, server_key=None, sekarang=None):
    """Process a Midtrans webhook after requiring its SHA512 signature."""
    return _proses_status_pembayaran(
        data, server_key=server_key, sekarang=sekarang, require_signature=True,
    )


def proses_status_get_midtrans(data, expected_order_id, sekarang=None):
    """Process a response from the authenticated Midtrans Get Status API."""
    if not isinstance(data, dict):
        return {'success': False, 'error': 'invalid_response'}

    order_id = str(data.get('order_id', '')).strip()
    if not expected_order_id or order_id != expected_order_id:
        return {'success': False, 'error': 'order_id_mismatch'}
    if not isinstance(data.get('transaction_status'), str) or not data['transaction_status'].strip():
        return {'success': False, 'error': 'invalid_response'}

    return _proses_status_pembayaran(
        data, sekarang=sekarang, require_signature=False,
    )


def sinkronkan_status_midtrans(pesanan, server_key=None, sekarang=None):
    """Fetch and safely apply the current transaction status from Midtrans."""
    server_key = server_key if server_key is not None else getattr(
        settings, 'MIDTRANS_SERVER_KEY', ''
    )
    if not server_key:
        raise MidtransStatusError('Kredensial Midtrans belum dikonfigurasi.')

    order_id = str(pesanan.midtrans_order_id or '').strip()
    if not order_id:
        raise MidtransStatusError('Order ID Midtrans belum tersedia.')

    api_base = (
        'https://api.midtrans.com'
        if getattr(settings, 'MIDTRANS_IS_PRODUCTION', False)
        else 'https://api.sandbox.midtrans.com'
    )
    status_url = f'{api_base}/v2/{quote(order_id, safe="")}/status'

    authorization = base64.b64encode(f'{server_key}:'.encode('utf-8')).decode('ascii')
    request = urllib.request.Request(
        status_url,
        headers={
            'Accept': 'application/json',
            'Authorization': f'Basic {authorization}',
        },
        method='GET',
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as exc:
        logger.warning('Midtrans Get Status API error [HTTP %s]', exc.code)
        raise MidtransStatusError('Status pembayaran belum dapat disinkronkan.') from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning('Midtrans Get Status connection/response error [%s]', type(exc).__name__)
        raise MidtransStatusError('Status pembayaran belum dapat disinkronkan.') from exc

    if not isinstance(data, dict):
        raise MidtransStatusError('Respons status Midtrans tidak valid.')

    result = proses_status_get_midtrans(
        data, expected_order_id=order_id, sekarang=sekarang,
    )
    if not result.get('success'):
        logger.warning(
            'Midtrans Get Status response rejected [%s]',
            result.get('error', 'unknown_error'),
        )
        raise MidtransStatusError('Respons status Midtrans tidak cocok dengan pesanan.')
    return result
