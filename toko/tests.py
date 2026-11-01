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
