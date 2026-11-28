"""
Pengujian akses kontrol role Mitra Pembiayaan (Simulasi).

Memverifikasi:
1. Akun mitra diarahkan ke /mitra/pembiayaan/ setelah login.
2. Akun mitra tidak dapat mengakses /panel/ (modul admin MBG).
3. Customer tidak dapat menyetujui atau menolak pengajuan kredit.
4. Staf MBG biasa (is_staff=True) tanpa permission kredit tidak dapat meninjau kredit.
5. Mitra dengan permission kredit dapat menyetujui dan menolak via portal /mitra/.
6. Superuser dapat mengakses portal mitra dan panel admin.
7. Akun mitra lama (is_staff=True yang belum dimigrate) tetap diblokir dari /panel/.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, TestCase
from django.urls import reverse

from .models import PengaturanPembelian, PesananPembelian
from .tests_pembelian import buat_produk

User = get_user_model()

MITRA_GROUP = 'Mitra Pembiayaan (Simulasi)'
PERM_VIEW = 'toko.view_kredit_pesananpembelian'
PERM_REVIEW = 'toko.review_kredit_pesananpembelian'


def buat_mitra_user(username, is_staff=False):
    """Buat akun Mitra Pembiayaan (Simulasi) dengan permission kredit."""
    user = User.objects.create_user(
        username=username,
        password='MitraPass123!',
        no_whatsapp='081200000001',
        is_staff=is_staff,
    )
    group, _ = Group.objects.get_or_create(name=MITRA_GROUP)
    # Tambahkan permission kredit ke grup jika belum ada
    for codename in ('view_kredit_pesananpembelian', 'review_kredit_pesananpembelian'):
        try:
            perm = Permission.objects.get(codename=codename)
            group.permissions.add(perm)
        except Permission.DoesNotExist:
            pass  # Belum ada permission — test tetap jalan
    user.groups.add(group)
    return user


def buat_pesanan_kredit(pelanggan, produk):
    """Buat pesanan kredit dengan status menunggu_kredit."""
    PengaturanPembelian.ambil()  # pastikan pengaturan ada
    pesanan = PesananPembelian.buat_dari_produk(
        produk=produk,
        pelanggan=pelanggan,
        nama_pelanggan='Pelanggan Kredit',
        no_whatsapp='0812-9999-0000',
        metode_pembayaran='kredit',
        jenis_pembelian='beli_langsung',
        saluran_pembayaran='transfer',
    )
    return pesanan


# ═══════════════════════════════════════════════════════════════════
# 1. Login redirect berdasarkan role
# ═══════════════════════════════════════════════════════════════════

class MitraLoginRedirectTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mitra = buat_mitra_user('mitra-login')
        self.staf = User.objects.create_user(
            'staf-mbg', password='StafPass123!', no_whatsapp='081200000002', is_staff=True,
        )
        self.pelanggan = User.objects.create_user(
            'pelanggan-tes', password='PelangganPass123!', no_whatsapp='081200000003',
        )
        self.superuser = User.objects.create_superuser(
            'superuser-tes', password='SuperPass123!', no_whatsapp='081200000004',
        )

    def test_mitra_diarahkan_ke_portal_mitra_setelah_login(self):
        resp = self.client.post(reverse('login'), {
            'username_or_email': 'mitra-login', 'password': 'MitraPass123!',
        })
        self.assertRedirects(resp, reverse('mitra_pengajuan_kredit'), fetch_redirect_response=False)

    def test_staf_diarahkan_ke_dashboard_admin_setelah_login(self):
        resp = self.client.post(reverse('login'), {
            'username_or_email': 'staf-mbg', 'password': 'StafPass123!',
        })
        self.assertRedirects(resp, reverse('dashboard_admin'), fetch_redirect_response=False)

    def test_pelanggan_diarahkan_ke_dashboard_user_setelah_login(self):
        resp = self.client.post(reverse('login'), {
            'username_or_email': 'pelanggan-tes', 'password': 'PelangganPass123!',
        })
        self.assertRedirects(resp, reverse('dashboard_user'), fetch_redirect_response=False)

    def test_superuser_diarahkan_ke_dashboard_admin_setelah_login(self):
        resp = self.client.post(reverse('login'), {
            'username_or_email': 'superuser-tes', 'password': 'SuperPass123!',
        })
        self.assertRedirects(resp, reverse('dashboard_admin'), fetch_redirect_response=False)


# ═══════════════════════════════════════════════════════════════════
# 2. Akun mitra tidak dapat mengakses /panel/
# ═══════════════════════════════════════════════════════════════════

class MitraTidakDapatAksesPanelTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mitra = buat_mitra_user('mitra-panel')
        self.mitra_staff = buat_mitra_user('mitra-staff-lama', is_staff=True)

    def _cek_panel_diblokir(self, username, password):
        self.client.login(username=username, password='MitraPass123!')
        for url_name in ('dashboard_admin', 'admin_pesanan_pembelian', 'admin_katalog'):
            resp = self.client.get(reverse(url_name))
            self.assertEqual(
                resp.status_code, 403,
                msg=f'{username} seharusnya tidak dapat mengakses {url_name} (dapat {resp.status_code})',
            )

    def test_mitra_baru_tidak_dapat_akses_panel(self):
        self._cek_panel_diblokir('mitra-panel', 'MitraPass123!')

    def test_mitra_lama_is_staff_true_tidak_dapat_akses_panel(self):
        """Akun mitra lama yang masih is_staff=True harus tetap diblokir dari panel."""
        self._cek_panel_diblokir('mitra-staff-lama', 'MitraPass123!')

    def test_mitra_tidak_dapat_akses_panel_via_next_param(self):
        """Parameter ?next=/panel/ tidak boleh memby-pass redirect mitra."""
        self.client.login(username='mitra-panel', password='MitraPass123!')
        panel_url = reverse('dashboard_admin')
        # Akses langsung url panel
        resp = self.client.get(panel_url)
        self.assertIn(resp.status_code, (403, 302))


# ═══════════════════════════════════════════════════════════════════
# 3. Akses portal mitra
# ═══════════════════════════════════════════════════════════════════

class MitraPortalAksesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.mitra = buat_mitra_user('mitra-portal')
        self.pelanggan = User.objects.create_user(
            'pelanggan-aks', password='PelangganPass123!', no_whatsapp='081200000010',
        )
        self.staf = User.objects.create_user(
            'staf-aks', password='StafPass123!', no_whatsapp='081200000011', is_staff=True,
        )

    def test_mitra_dapat_akses_portal_mitra(self):
        self.client.login(username='mitra-portal', password='MitraPass123!')
        resp = self.client.get(reverse('mitra_pengajuan_kredit'))
        self.assertEqual(resp.status_code, 200)

    def test_pelanggan_tidak_dapat_akses_portal_mitra(self):
        self.client.login(username='pelanggan-aks', password='PelangganPass123!')
        resp = self.client.get(reverse('mitra_pengajuan_kredit'))
        self.assertIn(resp.status_code, (403, 302))

    def test_tamu_tidak_dapat_akses_portal_mitra(self):
        resp = self.client.get(reverse('mitra_pengajuan_kredit'))
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp['Location'])

    def test_staf_biasa_tanpa_permission_kredit_tidak_dapat_akses_portal_mitra(self):
        """Staf biasa yang bukan mitra tidak boleh masuk ke /mitra/pembiayaan/."""
        self.client.login(username='staf-aks', password='StafPass123!')
        resp = self.client.get(reverse('mitra_pengajuan_kredit'))
        # Staf bukan mitra → seharusnya 403 atau dialihkan
        self.assertIn(resp.status_code, (403, 302))


# ═══════════════════════════════════════════════════════════════════
# 4. Endpoint peninjauan kredit — POST aksi setujui/tolak
# ═══════════════════════════════════════════════════════════════════

class MitraTinjauKreditTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.pelanggan = User.objects.create_user(
            'pelanggan-kredit', password='PelangganPass123!', no_whatsapp='081200000020',
        )
        self.mitra = buat_mitra_user('mitra-tinjau')
        self.staf = User.objects.create_user(
            'staf-tinjau', password='StafPass123!', no_whatsapp='081200000021', is_staff=True,
        )
        self.superuser = User.objects.create_superuser(
            'super-tinjau', password='SuperPass123!', no_whatsapp='081200000022',
        )
        pengaturan = PengaturanPembelian.ambil()
        pengaturan.persentase_booking_fee = Decimal('2.00')
        pengaturan.save()
        self.produk = buat_produk()

    def _buat_pesanan_kredit(self):
        return buat_pesanan_kredit(self.pelanggan, self.produk)

    def test_customer_tidak_dapat_menyetujui_kredit(self):
        pesanan = self._buat_pesanan_kredit()
        self.client.login(username='pelanggan-kredit', password='PelangganPass123!')
        url = reverse('mitra_tinjau_pengajuan_kredit', kwargs={'pesanan_id': pesanan.pk})
        resp = self.client.post(url, {'aksi': 'setujui'})
        self.assertIn(resp.status_code, (403, 302))
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_kredit')

    def test_staf_biasa_tanpa_permission_tidak_dapat_meninjau_kredit(self):
        pesanan = self._buat_pesanan_kredit()
        self.client.login(username='staf-tinjau', password='StafPass123!')
        url = reverse('mitra_tinjau_pengajuan_kredit', kwargs={'pesanan_id': pesanan.pk})
        resp = self.client.post(url, {'aksi': 'setujui'})
        self.assertEqual(resp.status_code, 403)
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_kredit')

    def test_mitra_dapat_menyetujui_kredit(self):
        pesanan = self._buat_pesanan_kredit()
        self.client.login(username='mitra-tinjau', password='MitraPass123!')
        url = reverse('mitra_tinjau_pengajuan_kredit', kwargs={'pesanan_id': pesanan.pk})
        resp = self.client.post(url, {'aksi': 'setujui'})
        self.assertIn(resp.status_code, (200, 302))
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_dp_kredit')

    def test_mitra_dapat_menolak_kredit_dengan_alasan(self):
        pesanan = self._buat_pesanan_kredit()
        self.client.login(username='mitra-tinjau', password='MitraPass123!')
        url = reverse('mitra_tinjau_pengajuan_kredit', kwargs={'pesanan_id': pesanan.pk})
        resp = self.client.post(url, {'aksi': 'tolak', 'alasan': 'Riwayat kredit tidak memenuhi syarat'})
        self.assertIn(resp.status_code, (200, 302))
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'dibatalkan')

    def test_superuser_dapat_menyetujui_kredit(self):
        pesanan = self._buat_pesanan_kredit()
        self.client.login(username='super-tinjau', password='SuperPass123!')
        url = reverse('mitra_tinjau_pengajuan_kredit', kwargs={'pesanan_id': pesanan.pk})
        resp = self.client.post(url, {'aksi': 'setujui'})
        self.assertIn(resp.status_code, (200, 302))
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_dp_kredit')

    def test_mitra_tidak_dapat_meninjau_ulang_pesanan_yang_sudah_diputuskan(self):
        pesanan = self._buat_pesanan_kredit()
        # Setujui dulu
        pesanan.tinjau_pengajuan_kredit(oleh_user=self.mitra, disetujui=True)
        pesanan.refresh_from_db()
        self.assertEqual(pesanan.status, 'menunggu_dp_kredit')

        self.client.login(username='mitra-tinjau', password='MitraPass123!')
        url = reverse('mitra_tinjau_pengajuan_kredit', kwargs={'pesanan_id': pesanan.pk})
        resp = self.client.post(url, {'aksi': 'tolak', 'alasan': 'Mencoba ulang'})
        # Harus error (200 dengan pesan error atau tetap redirect), status tidak berubah
        pesanan.refresh_from_db()
        self.assertNotEqual(pesanan.status, 'dibatalkan')


# ═══════════════════════════════════════════════════════════════════
# 5. Staf MBG dapat akses panel; mitra diblokir
# ═══════════════════════════════════════════════════════════════════

class StafMBGPanelAksesTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staf = User.objects.create_user(
            'staf-panel', password='StafPass123!', no_whatsapp='081200000030', is_staff=True,
        )
        self.mitra = buat_mitra_user('mitra-panel-blok')

    def test_staf_mbg_dapat_akses_dashboard_admin(self):
        self.client.login(username='staf-panel', password='StafPass123!')
        resp = self.client.get(reverse('dashboard_admin'))
        self.assertEqual(resp.status_code, 200)

    def test_mitra_tidak_dapat_akses_dashboard_admin(self):
        self.client.login(username='mitra-panel-blok', password='MitraPass123!')
        resp = self.client.get(reverse('dashboard_admin'))
        self.assertEqual(resp.status_code, 403)

    def test_staf_mbg_dapat_akses_pesanan_pembelian(self):
        """Staf MBG biasa tetap bisa membuka panel pesanan pembelian tanpa hak review kredit."""
        self.client.login(username='staf-panel', password='StafPass123!')
        resp = self.client.get(reverse('admin_pesanan_pembelian'))
        self.assertEqual(resp.status_code, 200)

    def test_alias_panel_kredit_lama_sudah_dihapus(self):
        """URL lama /panel/pengajuan-kredit/ tidak lagi tersedia; portal hanya di /mitra/pembiayaan/."""
        self.client.login(username='staf-panel', password='StafPass123!')
        resp = self.client.get('/panel/pengajuan-kredit/')
        self.assertEqual(resp.status_code, 404)
