from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import Produk, TitipJual


def buat_produk(nama, **kw):
    """Pabrik data uji. Field wajib diberi nilai bawaan; gambar berupa path teks (tanpa berkas)."""
    n = Produk.objects.count() + 1
    data = dict(
        sku=f'SKU-{n:04d}', nama=nama, merek='Toyota', kategori='MPV', tahun=2020,
        transmisi='AT', jarak_tempuh=40000, warna='Putih', plat_nomor='DD 1 XX',
        harga=Decimal('200000000'), tipe_penjual='B2C', status_stok='ready',
        gambar_utama='mobil/uji.jpg', deskripsi_singkat='Ringkas', deskripsi_detail='Detail',
    )
    data.update(kw)
    return Produk.objects.create(**data)


class KatalogTests(TestCase):
    def setUp(self):
        self.url = reverse('katalog')
        self.avanza = buat_produk('Toyota Avanza 1.3 G MT 2018', tahun=2018, transmisi='MT',
                                  harga=Decimal('140000000'), jarak_tempuh=80000)
        self.hrv = buat_produk('Honda HR-V 1.5 E AT 2021', merek='Honda', kategori='SUV', tahun=2021,
                               harga=Decimal('280000000'), jarak_tempuh=20000)
        self.fortuner = buat_produk('Toyota Fortuner 2.4 VRZ AT 2022', kategori='SUV', tahun=2022,
                                    harga=Decimal('420000000'), jarak_tempuh=15000)
        self.titip = buat_produk('Suzuki Ertiga 2019 Titip', merek='Suzuki', tahun=2019,
                                 harga=Decimal('160000000'), tipe_penjual='C2C')
        # Tidak boleh tampil di publik
        self.terjual = buat_produk('Toyota Rush Terjual', status_stok='sold')
        self.booking = buat_produk('Toyota Yaris Di-booking', status_stok='in_review')

    def nama(self, resp):
        return {m.nama for m in resp.context['mobil_list']}

    # ── Dasar ─────────────────────────────────────────
    def test_url_dan_template(self):
        self.assertEqual(self.url, '/katalog/')
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, 'katalog.html')
        self.assertTemplateUsed(r, 'base_publik.html')

    def test_hanya_unit_ready_tampil(self):
        r = self.client.get(self.url)
        self.assertEqual(r.context['total_hasil'], 4)
        self.assertNotIn('Toyota Rush Terjual', self.nama(r))
        self.assertNotIn('Toyota Yaris Di-booking', self.nama(r))

    def test_kartu_memuat_link_detail(self):
        r = self.client.get(self.url)
        self.assertContains(r, reverse('detail_produk', args=[self.avanza.id]))
        self.assertEqual(self.client.get(reverse('detail_produk', args=[self.avanza.id])).status_code, 200)

    def test_gambar_kosong_tidak_error(self):
        buat_produk('Mobil Tanpa Foto', gambar_utama='')
        r = self.client.get(self.url, {'q': 'Tanpa Foto'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Foto unit dalam proses')

    # ── Pencarian ─────────────────────────────────────
    def test_cari_nama_dan_merek_tidak_peka_huruf(self):
        self.assertEqual(self.nama(self.client.get(self.url, {'q': 'avanza'})), {self.avanza.nama})
        self.assertEqual(self.nama(self.client.get(self.url, {'q': 'HONDA'})), {self.hrv.nama})

    def test_cari_beberapa_kata_bersifat_and(self):
        r = self.client.get(self.url, {'q': 'toyota fortuner'})
        self.assertEqual(self.nama(r), {self.fortuner.nama})

    def test_cari_tanpa_hasil_menampilkan_empty_state(self):
        r = self.client.get(self.url, {'q': 'tidak-ada-mobil-ini'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total_hasil'], 0)
        self.assertContains(r, 'Tidak Ada Mobil yang Cocok')

    def test_katalog_kosong_total(self):
        Produk.objects.all().delete()
        r = self.client.get(self.url)
        self.assertContains(r, 'Katalog Masih Kosong')

    # ── Filter ────────────────────────────────────────
    def test_filter_merek_kategori_transmisi_tipe(self):
        self.assertEqual(self.nama(self.client.get(self.url, {'merek': 'Honda'})), {self.hrv.nama})
        self.assertEqual(self.nama(self.client.get(self.url, {'kategori': 'SUV'})), {self.hrv.nama, self.fortuner.nama})
        self.assertEqual(self.nama(self.client.get(self.url, {'transmisi': 'MT'})), {self.avanza.nama})
        self.assertEqual(self.nama(self.client.get(self.url, {'tipe': 'C2C'})), {self.titip.nama})

    def test_filter_harga_preset(self):
        self.assertEqual(self.nama(self.client.get(self.url, {'harga': 'lt150'})), {self.avanza.nama})
        self.assertEqual(self.nama(self.client.get(self.url, {'harga': '250-350'})), {self.hrv.nama})
        self.assertEqual(self.nama(self.client.get(self.url, {'harga': 'gt350'})), {self.fortuner.nama})

    def test_filter_tahun_dan_kilometer(self):
        r = self.client.get(self.url, {'tahun_min': 2020})
        self.assertEqual(self.nama(r), {self.hrv.nama, self.fortuner.nama})
        r = self.client.get(self.url, {'tahun_max': 2018})
        self.assertEqual(self.nama(r), {self.avanza.nama})
        r = self.client.get(self.url, {'km': '25000'})
        self.assertEqual(self.nama(r), {self.hrv.nama, self.fortuner.nama})

    def test_tahun_terbalik_ditukar(self):
        r = self.client.get(self.url, {'tahun_min': 2022, 'tahun_max': 2021})
        self.assertEqual(self.nama(r), {self.hrv.nama, self.fortuner.nama})

    def test_filter_gabungan(self):
        r = self.client.get(self.url, {'merek': 'Toyota', 'kategori': 'SUV', 'transmisi': 'AT', 'harga': 'gt350'})
        self.assertEqual(self.nama(r), {self.fortuner.nama})
        r = self.client.get(self.url, {'q': 'toyota', 'transmisi': 'AT'})
        self.assertEqual(self.nama(r), {self.fortuner.nama})

    def test_parameter_tidak_valid_diabaikan(self):
        r = self.client.get(self.url, {
            'merek': "x' OR 1=1 --", 'kategori': 'zzz', 'transmisi': 'CVT', 'tipe': 'ABC', 'harga': '999',
            'tahun_min': 'abc', 'tahun_max': '-5', 'km': '1', 'urutan': 'hack', 'page': 'abc',
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total_hasil'], 4)
        self.assertEqual(r.context['querystring'], '')

    def test_opsi_filter_dari_data_tayang(self):
        r = self.client.get(self.url)
        self.assertEqual(r.context['daftar_merek'], ['Honda', 'Suzuki', 'Toyota'])

    # ── Pengurutan ────────────────────────────────────
    def test_urutan(self):
        urut = lambda k: [m.nama for m in self.client.get(self.url, {'urutan': k}).context['mobil_list']]
        self.assertEqual(urut('harga_asc')[0], self.avanza.nama)
        self.assertEqual(urut('harga_asc')[-1], self.fortuner.nama)
        self.assertEqual(urut('harga_desc')[0], self.fortuner.nama)
        self.assertEqual(urut('tahun_desc')[0], self.fortuner.nama)
        self.assertEqual(urut('terbaru')[0], self.titip.nama)  # terakhir dibuat di antara yang tayang

    # ── Pagination ────────────────────────────────────
    def test_pagination_mempertahankan_pencarian_dan_filter(self):
        for i in range(12):
            buat_produk(f'Daihatsu Xenia {i:02d}', merek='Daihatsu', harga=Decimal(100_000_000 + i))
        params = {'merek': 'Daihatsu', 'urutan': 'harga_asc'}
        r1 = self.client.get(self.url, params)
        self.assertEqual(len(r1.context['mobil_list']), 9)
        self.assertEqual(r1.context['total_hasil'], 12)
        self.assertEqual(r1.context['page_obj'].paginator.num_pages, 2)
        # tautan halaman 2 membawa filter + urutan
        self.assertContains(r1, 'merek=Daihatsu&amp;urutan=harga_asc&amp;page=2')

        r2 = self.client.get(self.url, {**params, 'page': 2})
        self.assertEqual(len(r2.context['mobil_list']), 3)
        # tanpa tumpang-tindih antar halaman
        semua = [m.id for m in r1.context['mobil_list']] + [m.id for m in r2.context['mobil_list']]
        self.assertEqual(len(semua), len(set(semua)))

    def test_halaman_di_luar_rentang(self):
        for i in range(10):
            buat_produk(f'Mazda {i}', merek='Mazda')
        r = self.client.get(self.url, {'merek': 'Mazda', 'page': 999})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['page_obj'].number, 2)

    # ── Chip filter aktif ─────────────────────────────
    def test_chip_filter_aktif_dapat_dihapus_satu_per_satu(self):
        r = self.client.get(self.url, {'merek': 'Toyota', 'transmisi': 'AT'})
        urls = {f['label']: f['url'] for f in r.context['filter_aktif']}
        self.assertEqual(urls['Merek: Toyota'], '?transmisi=AT')
        self.assertEqual(urls['Otomatis (AT)'], '?merek=Toyota')
        r = self.client.get(self.url, {'merek': 'Toyota'})
        self.assertEqual(r.context['filter_aktif'][0]['url'], self.url)


class BerandaDanIntegrasiTests(TestCase):
    def test_beranda_hanya_mobil_pilihan(self):
        for i in range(10):
            buat_produk(f'Unit {i}')
        buat_produk('Unit Terjual', status_stok='sold')
        r = self.client.get(reverse('beranda'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context['katalog_mobil']), 6)
        self.assertEqual(r.context['total_unit_tayang'], 10)
        self.assertContains(r, reverse('katalog'))
        self.assertNotContains(r, 'Unit Terjual')

    def test_beranda_kosong(self):
        r = self.client.get(reverse('beranda'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Katalog Masih Kosong')

    def test_titip_jual_tidak_lagi_membuat_tautan_detail_salah(self):
        """Dulu pengajuan TitipJual disuntik ke beranda dengan id TitipJual, tetapi
        tautan detailnya memakai id Produk (salah/404). Kini hanya Produk yang tayang."""
        TitipJual.objects.create(nama_pemilik='A', no_whatsapp='08', merek_model='Honda Jazz 2018',
                                 tahun=2018, transmisi='AT', harga_harapan=150000000,
                                 foto_kendaraan='titip_jual/x.jpg', status='disetujui')
        r = self.client.get(reverse('beranda'))
        self.assertNotContains(r, 'Honda Jazz 2018')

    def test_detail_menaut_ke_katalog(self):
        p = buat_produk('Unit Detail')
        r = self.client.get(reverse('detail_produk', args=[p.id]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'href="/katalog/"')
        self.assertNotContains(r, '#katalog')

    def test_halaman_lain_tetap_berfungsi(self):
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)
        self.assertEqual(self.client.get(reverse('register')).status_code, 200)
        # dashboard butuh login → diarahkan ke halaman login
        for nama in ('dashboard_user', 'dashboard_admin', 'profil_user'):
            r = self.client.get(reverse(nama))
            self.assertEqual(r.status_code, 302)
            self.assertIn('/login/', r['Location'])

    def test_navbar_pengguna_login_di_katalog(self):
        from django.contrib.auth import get_user_model
        u = get_user_model().objects.create_user('budi', 'b@x.id', 'pw12345!x', first_name='Budi')
        self.client.force_login(u)
        r = self.client.get(reverse('katalog'))
        self.assertContains(r, 'Dashboard Pelanggan')
        self.assertContains(r, 'Budi')


# ═══════════════════════════════════════════════════════════════════
# PANEL ADMIN — KELOLA KATALOG
# ═══════════════════════════════════════════════════════════════════
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image

from .models import BookingCekUnit, FotoMobil

MEDIA_UJI = tempfile.mkdtemp(prefix='media_uji_admin_')


def gambar_uji(nama='foto.jpg', format='JPEG', ukuran=(64, 48)):
    b = io.BytesIO()
    Image.new('RGB', ukuran, (30, 90, 200)).save(b, format)
    return SimpleUploadedFile(nama, b.getvalue(), content_type='image/jpeg')


def data_form(**kw):
    data = dict(
        sku='mbg-test-001', nama='Toyota  Avanza 1.5 G AT 2021', merek='toyota', kategori='mpv',
        tahun=2021, warna='Putih', plat_nomor='dd 1234 xx', lokasi='Makassar',
        transmisi='AT', jarak_tempuh=35000, harga=210000000, harga_promo='',
        tipe_penjual='B2C', status_stok='ready', lulus_inspeksi='on',
        ringkasan_inspeksi='Bebas banjir.', deskripsi_singkat='Terawat.', deskripsi_detail='Detail lengkap.',
        gambar_utama=gambar_uji(),
    )
    data.update(kw)
    return data


@override_settings(MEDIA_ROOT=MEDIA_UJI)
class PanelAdminKatalogTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_UJI, ignore_errors=True)

    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_user('admin1', 'a@x.id', 'pw12345!x', is_staff=True)
        self.user = U.objects.create_user('budi', 'b@x.id', 'pw12345!x')
        self.client.force_login(self.admin)

    # ── Akses ───────────────────────────────────────
    def test_hanya_staf_yang_bisa_masuk(self):
        p = buat_produk('Unit A')
        urls = [reverse('admin_katalog'), reverse('admin_katalog_tambah'),
                reverse('admin_katalog_edit', args=[p.id])]
        anon = self.client_class()
        for u in urls:
            r = anon.get(u); self.assertEqual(r.status_code, 302); self.assertIn('/login/', r['Location'])
        self.client.force_login(self.user)
        for u in urls:
            r = self.client.get(u); self.assertRedirects(r, reverse('dashboard_user'), fetch_redirect_response=False)
        # aksi tulis pun ditolak untuk non-staf
        r = self.client.post(reverse('admin_katalog_hapus', args=[p.id]))
        self.assertEqual(r.status_code, 302); self.assertTrue(Produk.objects.filter(id=p.id).exists())
        r = self.client.post(reverse('admin_katalog_tambah'), data_form())
        self.assertFalse(Produk.objects.filter(sku='MBG-TEST-001').exists())

    def test_aksi_tulis_hanya_lewat_post(self):
        p = buat_produk('Unit A')
        self.assertEqual(self.client.get(reverse('admin_katalog_hapus', args=[p.id])).status_code, 405)
        self.assertEqual(self.client.get(reverse('admin_katalog_status', args=[p.id])).status_code, 405)
        self.assertTrue(Produk.objects.filter(id=p.id).exists())

    # ── Daftar ──────────────────────────────────────
    def test_daftar_menampilkan_semua_status(self):
        buat_produk('Siap', status_stok='ready'); buat_produk('Terjual', status_stok='sold')
        r = self.client.get(reverse('admin_katalog'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total_hasil'], 2)
        r = self.client.get(reverse('admin_katalog'), {'status': 'sold'})
        self.assertEqual([p.nama for p in r.context['produk_list']], ['Terjual'])
        r = self.client.get(reverse('admin_katalog'), {'q': 'sku-0001'})
        self.assertEqual(r.context['total_hasil'], 1)

    def test_form_tambah_tampil(self):
        r = self.client.get(reverse('admin_katalog_tambah'))
        self.assertEqual(r.status_code, 200)
        for nama in ('sku', 'nama', 'merek', 'harga', 'harga_promo', 'status_stok', 'gambar_utama'):
            self.assertContains(r, f'name="{nama}"')
        self.assertContains(r, 'enctype="multipart/form-data"')

    # ── Tambah ──────────────────────────────────────
    def test_tambah_unit_tersimpan_dan_tampil_di_katalog_publik(self):
        r = self.client.post(reverse('admin_katalog_tambah'), data_form())
        p = Produk.objects.get(sku='MBG-TEST-001')  # SKU dinormalkan ke huruf besar
        self.assertRedirects(r, reverse('admin_katalog_edit', args=[p.id]), fetch_redirect_response=False)
        self.assertEqual(p.nama, 'Toyota Avanza 1.5 G AT 2021')      # spasi ganda dirapikan
        self.assertEqual(p.plat_nomor, 'DD 1234 XX')
        self.assertTrue(p.gambar_utama.name.startswith('mobil/') and p.gambar_utama.name.endswith('.jpg'))
        self.assertTrue(p.gambar_utama.storage.exists(p.gambar_utama.name))
        # langsung muncul di katalog & beranda publik
        pub = self.client_class().get(reverse('katalog'))
        self.assertIn(p.nama, {m.nama for m in pub.context['mobil_list']})

    def test_merek_dan_kategori_disamakan_dengan_data_yang_ada(self):
        buat_produk('Lama', merek='Toyota', kategori='MPV')
        self.client.post(reverse('admin_katalog_tambah'), data_form(merek='TOYOTA', kategori='mpv'))
        p = Produk.objects.get(sku='MBG-TEST-001')
        self.assertEqual((p.merek, p.kategori), ('Toyota', 'MPV'))

    def test_validasi_menolak_data_buruk(self):
        buat_produk('Ada', sku='DUP-1')
        kasus = {
            'sku duplikat (tak peka huruf)': dict(sku='dup-1'),
            'sku berkarakter aneh': dict(sku='A B<script>'),
            'tahun terlalu lama': dict(tahun=1900),
            'tahun masa depan': dict(tahun=2999),
            'km negatif': dict(jarak_tempuh=-5),
            'harga nol': dict(harga=0),
            'promo >= harga': dict(harga_promo=210000000),
            'status tak dikenal': dict(status_stok='hacked'),
            'transmisi tak dikenal': dict(transmisi='CVT'),
        }
        for nama, ubah in kasus.items():
            n = Produk.objects.count()
            r = self.client.post(reverse('admin_katalog_tambah'), data_form(**ubah))
            self.assertEqual(r.status_code, 200, nama)
            self.assertEqual(Produk.objects.count(), n, nama)

    def test_foto_wajib_dan_harus_gambar_asli(self):
        r = self.client.post(reverse('admin_katalog_tambah'), data_form(gambar_utama=''))
        self.assertEqual(Produk.objects.count(), 0)
        palsu = SimpleUploadedFile('virus.jpg', b'bukan gambar', content_type='image/jpeg')
        self.client.post(reverse('admin_katalog_tambah'), data_form(gambar_utama=palsu))
        self.assertEqual(Produk.objects.count(), 0)
        gif = io.BytesIO(); Image.new('RGB', (10, 10)).save(gif, 'GIF')
        self.client.post(reverse('admin_katalog_tambah'),
                         data_form(gambar_utama=SimpleUploadedFile('a.jpg', gif.getvalue())))
        self.assertEqual(Produk.objects.count(), 0)      # format GIF ditolak walau berekstensi .jpg

    def test_foto_terlalu_besar_ditolak(self):
        from . import forms as f
        asli = f.FOTO_MOBIL_MAKS_BYTE
        f.FOTO_MOBIL_MAKS_BYTE = 100
        try:
            self.client.post(reverse('admin_katalog_tambah'), data_form())
        finally:
            f.FOTO_MOBIL_MAKS_BYTE = asli
        self.assertEqual(Produk.objects.count(), 0)

    # ── Ubah ────────────────────────────────────────
    def test_edit_tanpa_ganti_foto_mempertahankan_foto(self):
        self.client.post(reverse('admin_katalog_tambah'), data_form())
        p = Produk.objects.get(sku='MBG-TEST-001'); foto = p.gambar_utama.name
        d = data_form(nama='Nama Baru', harga=199000000, gambar_utama='')
        r = self.client.post(reverse('admin_katalog_edit', args=[p.id]), d)
        self.assertRedirects(r, reverse('admin_katalog'), fetch_redirect_response=False)
        p.refresh_from_db()
        self.assertEqual((p.nama, int(p.harga), p.gambar_utama.name), ('Nama Baru', 199000000, foto))

    def test_edit_ganti_foto_menghapus_file_lama(self):
        self.client.post(reverse('admin_katalog_tambah'), data_form())
        p = Produk.objects.get(sku='MBG-TEST-001'); lama = p.gambar_utama.name
        self.client.post(reverse('admin_katalog_edit', args=[p.id]), data_form(gambar_utama=gambar_uji('baru.png', 'PNG')))
        p.refresh_from_db()
        self.assertNotEqual(p.gambar_utama.name, lama)
        self.assertTrue(p.gambar_utama.name.endswith('.png'))
        self.assertFalse(p.gambar_utama.storage.exists(lama))

    def test_edit_sku_sendiri_tidak_dianggap_duplikat(self):
        self.client.post(reverse('admin_katalog_tambah'), data_form())
        p = Produk.objects.get(sku='MBG-TEST-001')
        r = self.client.post(reverse('admin_katalog_edit', args=[p.id]), data_form(gambar_utama=''))
        self.assertEqual(r.status_code, 302)

    def test_ubah_status_cepat(self):
        p = buat_produk('Unit A')
        r = self.client.post(reverse('admin_katalog_status', args=[p.id]), {'status_stok': 'sold'})
        p.refresh_from_db(); self.assertEqual(p.status_stok, 'sold')
        self.client.post(reverse('admin_katalog_status', args=[p.id]), {'status_stok': 'ngawur'})
        p.refresh_from_db(); self.assertEqual(p.status_stok, 'sold')
        # unit terjual hilang dari katalog publik
        pub = self.client_class().get(reverse('katalog'))
        self.assertEqual(pub.context['total_hasil'], 0)

    def test_redirect_next_eksternal_ditolak(self):
        p = buat_produk('Unit A')
        r = self.client.post(reverse('admin_katalog_status', args=[p.id]),
                             {'status_stok': 'sold', 'next': 'https://jahat.example/'})
        self.assertRedirects(r, reverse('admin_katalog'), fetch_redirect_response=False)

    # ── Hapus ───────────────────────────────────────
    def test_hapus_unit_beserta_file(self):
        self.client.post(reverse('admin_katalog_tambah'), data_form())
        p = Produk.objects.get(sku='MBG-TEST-001'); nama_file = p.gambar_utama.name; st = p.gambar_utama.storage
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('admin_katalog_hapus', args=[p.id]))
        self.assertFalse(Produk.objects.filter(id=p.id).exists())
        self.assertFalse(st.exists(nama_file))

    def test_unit_dengan_booking_tidak_boleh_dihapus(self):
        p = buat_produk('Sudah Dibooking')
        BookingCekUnit.objects.create(produk=p, nama_pemesan='X', no_whatsapp='08', email='x@x.id',
                                      tanggal_cek_unit='2026-12-01', jam_cek_unit='10:00')
        r = self.client.post(reverse('admin_katalog_hapus', args=[p.id]))
        self.assertTrue(Produk.objects.filter(id=p.id).exists())
        self.assertEqual(BookingCekUnit.objects.count(), 1)
        self.assertContains(self.client.get(reverse('admin_katalog')), 'Tidak bisa dihapus')

    # ── Galeri ──────────────────────────────────────
    def test_galeri_tambah_banyak_dan_hapus(self):
        p = buat_produk('Unit A')
        r = self.client.post(reverse('admin_galeri_tambah', args=[p.id]),
                             {'gambar': [gambar_uji('a.jpg'), gambar_uji('b.png', 'PNG')], 'keterangan': 'Interior'})
        self.assertEqual(p.galeri_foto.count(), 2)
        self.assertEqual(p.galeri_foto.first().keterangan, 'Interior')
        f = p.galeri_foto.first(); berkas = f.gambar.name; st = f.gambar.storage
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('admin_galeri_hapus', args=[p.id, f.id]))
        self.assertEqual(p.galeri_foto.count(), 1)
        self.assertFalse(st.exists(berkas))
        # tampil di halaman detail publik
        self.assertContains(self.client_class().get(reverse('detail_produk', args=[p.id])), p.galeri_foto.first().gambar.url)

    def test_galeri_menolak_file_palsu_dan_membatasi_kuota(self):
        p = buat_produk('Unit A')
        self.client.post(reverse('admin_galeri_tambah', args=[p.id]),
                         {'gambar': [SimpleUploadedFile('x.jpg', b'bukan gambar')]})
        self.assertEqual(p.galeri_foto.count(), 0)
        for i in range(12):
            FotoMobil.objects.create(produk=p, gambar=f'mobil/galeri/{i}.jpg')
        self.client.post(reverse('admin_galeri_tambah', args=[p.id]), {'gambar': [gambar_uji()]})
        self.assertEqual(p.galeri_foto.count(), 12)

    def test_tidak_bisa_menghapus_foto_milik_unit_lain(self):
        a, b = buat_produk('A'), buat_produk('B')
        f = FotoMobil.objects.create(produk=b, gambar='mobil/galeri/z.jpg')
        r = self.client.post(reverse('admin_galeri_hapus', args=[a.id, f.id]))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(FotoMobil.objects.filter(id=f.id).exists())

    # ── Integrasi dashboard ─────────────────────────
    def test_dashboard_admin_menaut_ke_panel_baru(self):
        r = self.client.get(reverse('dashboard_admin'))
        self.assertContains(r, reverse('admin_katalog_tambah'))
        self.assertContains(r, reverse('admin_katalog'))
        self.assertNotContains(r, '/admin/toko/produk/')
        for nama in ('profil_admin', 'ubah_password_admin'):
            self.assertContains(self.client.get(reverse(nama)), reverse('admin_katalog'))


# ═══════════════════════════════════════════════════════════════════
# HALAMAN WEBSITE — halaman informasi (Tentang Kami, S&K, dll.)
# ═══════════════════════════════════════════════════════════════════
from unittest import mock

from django.db import DatabaseError

from .models import HalamanWebsite


def data_halaman(**kw):
    """Data POST form halaman. Nilai None = field tidak dikirim (mis. checkbox mati)."""
    data = dict(
        judul='Tentang Kami', slug='', ringkasan='', isi='Profil singkat MBG.',
        status='published', tampil_di_footer='on', urutan=0,
        meta_title='', meta_description='',
    )
    data.update(kw)
    return {k: v for k, v in data.items() if v is not None}


class HalamanWebsiteTests(TestCase):
    def setUp(self):
        U = get_user_model()
        self.admin = U.objects.create_user('admin1', 'a@x.id', 'pw12345!x', is_staff=True)
        self.user = U.objects.create_user('budi', 'b@x.id', 'pw12345!x')
        self.terbit = HalamanWebsite.objects.create(
            judul='Syarat dan Ketentuan', isi='Isi syarat dan ketentuan.',
            status='published', tampil_di_footer=True, urutan=1,
        )
        self.draft = HalamanWebsite.objects.create(
            judul='Rencana Rahasia', isi='Belum untuk publik.',
            status='draft', tampil_di_footer=True,
        )

    # ── Model ───────────────────────────────────────
    def test_slug_otomatis_dan_unik(self):
        self.assertEqual(self.terbit.slug, 'syarat-dan-ketentuan')
        lagi = HalamanWebsite.objects.create(judul='Syarat dan Ketentuan', isi='x')
        self.assertEqual(lagi.slug, 'syarat-dan-ketentuan-1')

    def test_slug_cadangan_jika_judul_hanya_simbol(self):
        self.assertEqual(HalamanWebsite.objects.create(judul='???', isi='x').slug, 'halaman')

    def test_status_bawaan_draft(self):
        self.assertEqual(HalamanWebsite.objects.create(judul='Baru', isi='x').status, 'draft')

    # ── Halaman publik ──────────────────────────────
    def test_publik_hanya_halaman_terbit(self):
        r = self.client.get(reverse('halaman_detail', args=[self.terbit.slug]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Isi syarat dan ketentuan.')
        self.assertEqual(
            self.client.get(reverse('halaman_detail', args=[self.draft.slug])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('halaman_detail', args=['tidak-ada'])).status_code, 404)

    def test_isi_halaman_di_escape(self):
        h = HalamanWebsite.objects.create(
            judul='Uji Escape', isi='<script>alert(1)</script>', status='published')
        r = self.client.get(h.get_absolute_url())
        self.assertNotContains(r, '<script>alert(1)</script>')
        self.assertContains(r, '&lt;script&gt;')

    # ── Footer ──────────────────────────────────────
    def test_footer_memuat_halaman_terbit_saja(self):
        for nama in ('beranda', 'katalog'):
            r = self.client.get(reverse(nama))
            self.assertContains(r, reverse('halaman_detail', args=[self.terbit.slug]))
            self.assertContains(r, 'Syarat dan Ketentuan')
            self.assertNotContains(r, self.draft.slug)

    def test_footer_kembali_ke_tautan_bawaan_jika_kosong(self):
        HalamanWebsite.objects.all().delete()
        self.assertContains(self.client.get(reverse('katalog')), 'Kebijakan Privasi')

    def test_footer_dibatasi_enam_tautan(self):
        for i in range(8):
            HalamanWebsite.objects.create(
                judul=f'Info {i}', isi='x', status='published',
                tampil_di_footer=True, urutan=10 + i)
        # 1 halaman dari setUp (urutan 1) + 5 pertama dari yang baru = 6 tautan
        self.assertContains(self.client.get(reverse('katalog')), '/halaman/', count=6)

    def test_footer_tahan_galat_database(self):
        with mock.patch('toko.context_processors.HalamanWebsite') as model_palsu:
            model_palsu.objects.filter.side_effect = DatabaseError('tabel belum ada')
            r = self.client.get(reverse('katalog'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Kebijakan Privasi')

    # ── Panel admin: akses ──────────────────────────
    def test_panel_hanya_untuk_staf(self):
        urls = [reverse('admin_halaman'), reverse('admin_halaman_tambah')] + [
            reverse(n, args=[self.terbit.id])
            for n in ('admin_halaman_edit', 'admin_halaman_hapus', 'admin_halaman_preview')
        ]
        for url in urls:                                   # belum login
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertIn('/login/', r['Location'])
        self.client.force_login(self.user)                 # login tapi bukan staf
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302)
            self.assertEqual(r['Location'], reverse('dashboard_user'))

    def test_panel_menampilkan_daftar_dan_menu_sidebar(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin_halaman'))
        self.assertContains(r, 'Syarat dan Ketentuan')
        self.assertContains(r, 'Rencana Rahasia')
        self.assertContains(r, 'href="%s"' % reverse('admin_halaman'))
        for nama, args in [
            ('admin_halaman_tambah', []),
            ('admin_halaman_edit', [self.draft.id]),
            ('admin_halaman_hapus', [self.draft.id]),
            ('admin_halaman_preview', [self.draft.id]),
        ]:
            self.assertEqual(self.client.get(reverse(nama, args=args)).status_code, 200)

    def test_filter_status_dan_pencarian(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('admin_halaman'), {'status': 'draft'})
        self.assertContains(r, 'Rencana Rahasia')
        self.assertNotContains(r, 'Syarat dan Ketentuan')
        r = self.client.get(reverse('admin_halaman'), {'q': 'syarat'})
        self.assertContains(r, 'Syarat dan Ketentuan')
        self.assertNotContains(r, 'Rencana Rahasia')

    # ── Panel admin: tambah / edit / hapus ──────────
    def test_tambah_edit_hapus(self):
        self.client.force_login(self.admin)
        r = self.client.post(reverse('admin_halaman_tambah'),
                             data_halaman(judul='Tentang Kami', urutan=2))
        self.assertRedirects(r, reverse('admin_halaman'))
        h = HalamanWebsite.objects.get(slug='tentang-kami')
        self.assertTrue(h.tampil_di_footer)
        self.assertEqual(h.status, 'published')
        self.assertEqual(h.urutan, 2)

        r = self.client.post(
            reverse('admin_halaman_edit', args=[h.id]),
            data_halaman(judul='  Tentang   MBG ', slug='tentang-kami', status='draft',
                         tampil_di_footer=None, isi='Profil baru.'))
        self.assertRedirects(r, reverse('admin_halaman'))
        h.refresh_from_db()
        self.assertEqual(h.judul, 'Tentang MBG')           # spasi dirapikan
        self.assertEqual(h.slug, 'tentang-kami')           # slug tidak ikut berubah
        self.assertEqual(h.status, 'draft')
        self.assertFalse(h.tampil_di_footer)

        r = self.client.post(reverse('admin_halaman_hapus', args=[h.id]))
        self.assertRedirects(r, reverse('admin_halaman'))
        self.assertFalse(HalamanWebsite.objects.filter(id=h.id).exists())

    def test_hapus_butuh_konfirmasi_post(self):
        self.client.force_login(self.admin)
        self.client.get(reverse('admin_halaman_hapus', args=[self.terbit.id]))
        self.assertTrue(HalamanWebsite.objects.filter(id=self.terbit.id).exists())

    def test_slug_input_dirapikan(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('admin_halaman_tambah'),
                         data_halaman(judul='Kebijakan Privasi', slug='Kebijakan Privasi!!'))
        self.assertTrue(HalamanWebsite.objects.filter(slug='kebijakan-privasi').exists())

    def test_validasi_form(self):
        self.client.force_login(self.admin)
        # slug sudah dipakai halaman lain
        r = self.client.post(reverse('admin_halaman_tambah'),
                             data_halaman(judul='Lain', slug=self.terbit.slug))
        self.assertEqual(r.status_code, 200)
        # judul dan isi kosong
        r = self.client.post(reverse('admin_halaman_tambah'),
                             data_halaman(judul='  ', isi='   '))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(HalamanWebsite.objects.count(), 2)   # tidak ada yang tersimpan
