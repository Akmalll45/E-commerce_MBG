from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
import re
import uuid
from django.core.files.uploadedfile import UploadedFile
from django.utils import timezone

from .models import Produk, KategoriArtikel, Artikel, HalamanWebsite

User = get_user_model()


class LoginForm(forms.Form):
    """
    Form login fleksibel: mendukung autentikasi via Username atau Alamat Email.
    """
    username_or_email = forms.CharField(
        label="Username atau Email",
        max_length=254,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'nama@email.com atau username',
            'autocomplete': 'username',
            'autofocus': True,
        })
    )
    password = forms.CharField(
        label="Kata Sandi",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Masukkan kata sandi Anda',
            'autocomplete': 'current-password',
        })
    )

    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request', None)
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        username_or_email = cleaned_data.get('username_or_email')
        password = cleaned_data.get('password')

        if username_or_email and password:
            # 1. Cari user berdasarkan email atau username
            user_obj = None
            if '@' in username_or_email:
                user_obj = User.objects.filter(email__iexact=username_or_email).first()
            if not user_obj:
                user_obj = User.objects.filter(username__iexact=username_or_email).first()

            if user_obj:
                # Otentikasi dengan username asli user
                self.user_cache = authenticate(
                    self.request,
                    username=user_obj.username,
                    password=password
                )
            else:
                self.user_cache = None

            if self.user_cache is None:
                raise ValidationError(
                    "Username/Email atau kata sandi yang Anda masukkan salah. Silakan periksa kembali.",
                    code='invalid_login'
                )
            else:
                if not self.user_cache.is_active:
                    raise ValidationError(
                        "Akun Anda sedang dinonaktifkan. Silakan hubungi tim Customer Service MBG.",
                        code='inactive'
                    )

        return cleaned_data

    def get_user(self):
        return self.user_cache


class RegisterForm(forms.Form):
    """
    Form pendaftaran akun baru pelanggan MBG dengan validasi lengkap.
    """
    nama_lengkap = forms.CharField(
        label="Nama Lengkap",
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Contoh: Andi Muhammad Pratama',
            'autocomplete': 'name',
            'autofocus': True,
        })
    )
    username = forms.CharField(
        label="Username",
        max_length=150,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Contoh: andipratama',
            'autocomplete': 'username',
        })
    )
    email = forms.EmailField(
        label="Alamat Email",
        widget=forms.EmailInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'nama@email.com',
            'autocomplete': 'email',
        })
    )
    no_whatsapp = forms.CharField(
        label="Nomor WhatsApp / HP",
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Contoh: 081234567890',
            'autocomplete': 'tel',
        })
    )
    password = forms.CharField(
        label="Kata Sandi",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Minimal 8 karakter',
            'autocomplete': 'new-password',
        })
    )
    password_confirm = forms.CharField(
        label="Konfirmasi Kata Sandi",
        widget=forms.PasswordInput(attrs={
            'class': 'form-control form-control-lg',
            'placeholder': 'Ulangi kata sandi Anda',
            'autocomplete': 'new-password',
        })
    )

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        if not re.match(r'^[a-zA-Z0-9_.-]+$', username):
            raise ValidationError(
                "Username hanya boleh terdiri dari huruf, angka, titik, strip (-), dan underscore (_)."
            )
        if User.objects.filter(username__iexact=username).exists():
            raise ValidationError(
                "Username ini sudah terdaftar. Silakan pilih username yang lain."
            )
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(
                "Alamat email ini sudah terdaftar. Silakan gunakan email lain atau langsung masuk."
            )
        return email

    def clean_no_whatsapp(self):
        no_wa = self.cleaned_data.get('no_whatsapp', '').strip()
        if no_wa:
            digits = re.sub(r'[^\d+]', '', no_wa)
            if len(digits) < 9 or len(digits) > 16:
                raise ValidationError("Nomor telepon/WhatsApp tidak valid (minimal 9 digit, maksimal 16 digit).")
            return digits
        return no_wa

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')

        if password and password_confirm:
            if password != password_confirm:
                self.add_error('password_confirm', "Konfirmasi kata sandi tidak cocok dengan kata sandi.")
            else:
                # Validasi kekuatan password Django
                try:
                    validate_password(password)
                except ValidationError as error:
                    self.add_error('password', error)

        return cleaned_data

    def save(self, commit=True):
        nama = self.cleaned_data.get('nama_lengkap', '').strip()
        parts = nama.split(' ', 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ''

        user = User(
            username=self.cleaned_data.get('username'),
            email=self.cleaned_data.get('email'),
            first_name=first_name,
            last_name=last_name,
            no_whatsapp=self.cleaned_data.get('no_whatsapp'),
            is_staff=False,
            is_superuser=False,
            is_active=True,
        )
        user.set_password(self.cleaned_data.get('password'))

        if commit:
            user.save()
        return user


class UserProfileForm(forms.ModelForm):
    """
    Form pembaruan data profil untuk pengguna biasa (Customer).
    Aman: tidak mengekspos field administratif (is_staff, is_superuser, groups).
    """
    FOTO_MAKS_BYTE = 2 * 1024 * 1024  # 2 MB
    # Format hasil deteksi Pillow -> ekstensi file yang disimpan
    FOTO_EKSTENSI = {'JPEG': 'jpg', 'MPO': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}

    hapus_foto = forms.BooleanField(
        required=False,
        label='Hapus foto profil saat ini',
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'}),
    )

    class Meta:
        model = User
        fields = ['foto_profil', 'first_name', 'last_name', 'email', 'no_whatsapp', 'alamat']
        labels = {
            'first_name': 'Nama Depan',
            'last_name': 'Nama Belakang',
            'email': 'Alamat Email',
            'no_whatsapp': 'Nomor WhatsApp / HP',
            'alamat': 'Alamat Domisili',
            'foto_profil': 'Foto Profil',
        }
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nama depan'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nama belakang'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'nama@email.com'}),
            'no_whatsapp': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Contoh: 081234567890'}),
            'alamat': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Alamat lengkap di Makassar atau sekitarnya'}),
            'foto_profil': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/jpeg,image/png,image/webp'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Catat nama file lama: saat validasi, instance langsung diisi file baru,
        # sehingga file lama perlu diingat untuk dihapus dari storage setelah simpan.
        self._foto_lama = (
            self.instance.foto_profil.name
            if self.instance.pk and self.instance.foto_profil
            else None
        )

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if not email:
            raise ValidationError("Email wajib diisi.")
        existing = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if existing.exists():
            raise ValidationError("Email ini sudah digunakan oleh akun lain.")
        return email

    def clean_no_whatsapp(self):
        # Field model ini boleh NULL, jadi isian kosong dibersihkan Django menjadi None
        # (bukan string kosong). "or ''" mencegah error pada .strip().
        no_wa = (self.cleaned_data.get('no_whatsapp') or '').strip()
        if no_wa:
            digits = re.sub(r'[^\d+]', '', no_wa)
            if len(digits) < 9 or len(digits) > 16:
                raise ValidationError("Nomor telepon/WhatsApp tidak valid.")
            return digits
        return no_wa

    def clean_foto_profil(self):
        foto = self.cleaned_data.get('foto_profil')

        # Hanya validasi unggahan baru; jika tidak ada unggahan, nilai lama dibiarkan.
        if isinstance(foto, UploadedFile):
            if foto.size > self.FOTO_MAKS_BYTE:
                raise ValidationError("Ukuran foto terlalu besar. Maksimal 2 MB.")

            # forms.ImageField sudah memastikan file benar-benar gambar (via Pillow);
            # format asli gambar yang menentukan boleh/tidaknya, bukan nama file.
            format_gambar = foto.image.format
            if format_gambar not in self.FOTO_EKSTENSI:
                raise ValidationError("Format foto harus JPG, PNG, atau WEBP.")

            # Samakan ekstensi dengan format asli agar nama file tidak menyesatkan.
            foto.name = f"foto.{self.FOTO_EKSTENSI[format_gambar]}"
        return foto

    def save(self, commit=True):
        user = super().save(commit=False)

        ada_foto_baru = isinstance(self.cleaned_data.get('foto_profil'), UploadedFile)
        if self.cleaned_data.get('hapus_foto') and not ada_foto_baru:
            user.foto_profil = None

        if commit:
            user.save()
            # Bersihkan file lama dari storage jika sudah diganti atau dihapus.
            nama_sekarang = user.foto_profil.name if user.foto_profil else None
            if self._foto_lama and self._foto_lama != nama_sekarang:
                user.foto_profil.storage.delete(self._foto_lama)
        return user


class AdminProfileForm(forms.ModelForm):
    """
    Form pembaruan data profil untuk administrator MBG.
    """
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email', 'no_whatsapp', 'alamat']
        labels = {
            'first_name': 'Nama Depan',
            'last_name': 'Nama Belakang',
            'email': 'Alamat Email',
            'no_whatsapp': 'Nomor WhatsApp Admin',
            'alamat': 'Catatan / Alamat Operasional',
        }
        widgets = {
            'first_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nama depan'}),
            'last_name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nama belakang'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'admin@mbgmakassar.com'}),
            'no_whatsapp': forms.TextInput(attrs={'class': 'form-control', 'placeholder': '08xxxxxxxxxx'}),
            'alamat': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Alamat operasional atau kantor cabang'}),
        }

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if not email:
            raise ValidationError("Email wajib diisi.")
        existing = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if existing.exists():
            raise ValidationError("Email ini sudah digunakan oleh akun lain.")
        return email


class UbahPasswordForm(PasswordChangeForm):
    """
    Form ubah kata sandi dengan styling Bootstrap dan pesan berbahasa Indonesia.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Masukkan kata sandi lama Anda',
            'autocomplete': 'current-password',
        })
        self.fields['old_password'].label = 'Kata Sandi Saat Ini'
        
        self.fields['new_password1'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Minimal 8 karakter unik',
            'autocomplete': 'new-password',
        })
        self.fields['new_password1'].label = 'Kata Sandi Baru'
        
        self.fields['new_password2'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': 'Ulangi kata sandi baru Anda',
            'autocomplete': 'new-password',
        })
        self.fields['new_password2'].label = 'Konfirmasi Kata Sandi Baru'

# ═══════════════════════════════════════════════════════════════════
# KATALOG MOBIL — FORM PANEL ADMIN
# ═══════════════════════════════════════════════════════════════════

FOTO_MOBIL_MAKS_BYTE = 5 * 1024 * 1024  # 5 MB per foto
FOTO_MOBIL_EKSTENSI = {'JPEG': 'jpg', 'MPO': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}


def validasi_foto_mobil(foto):
    """
    Validasi unggahan foto mobil (utama maupun galeri):
    ukuran maksimal 5 MB, format asli harus JPG/PNG/WEBP (dicek lewat Pillow,
    bukan dari nama file), dan nama file diganti acak agar aman & tidak bentrok.
    Mengembalikan file yang sama; melempar ValidationError jika tidak valid.
    """
    if not isinstance(foto, UploadedFile):
        return foto
    if foto.size > FOTO_MOBIL_MAKS_BYTE:
        raise ValidationError("Ukuran foto terlalu besar. Maksimal 5 MB per foto.")
    try:
        from PIL import Image
        foto.seek(0)
        gambar = Image.open(foto)
        format_gambar = gambar.format
        gambar.verify()
        foto.seek(0)
    except Exception:
        raise ValidationError("File bukan gambar yang valid.")
    if format_gambar not in FOTO_MOBIL_EKSTENSI:
        raise ValidationError("Format foto harus JPG, PNG, atau WEBP.")
    foto.name = f"{uuid.uuid4().hex}.{FOTO_MOBIL_EKSTENSI[format_gambar]}"
    return foto


class ProdukForm(forms.ModelForm):
    """
    Form tambah/ubah unit di katalog. Field mengikuti model `Produk` apa adanya,
    jadi data yang disimpan langsung menjadi isi katalog publik di database.
    """

    class Meta:
        model = Produk
        fields = [
            # Identitas unit
            'sku', 'nama', 'merek', 'kategori', 'tahun', 'warna', 'plat_nomor', 'lokasi',
            # Spesifikasi
            'transmisi', 'jarak_tempuh',
            # Harga
            'harga', 'harga_promo',
            # Status & sumber
            'tipe_penjual', 'status_stok',
            # Inspeksi
            'lulus_inspeksi', 'ringkasan_inspeksi',
            # Media & deskripsi
            'gambar_utama', 'deskripsi_singkat', 'deskripsi_detail',
        ]
        labels = {
            'sku': 'SKU (kode unit)',
            'nama': 'Nama Lengkap Unit',
            'merek': 'Merek',
            'kategori': 'Kategori',
            'tahun': 'Tahun',
            'warna': 'Warna',
            'plat_nomor': 'Plat Nomor',
            'lokasi': 'Lokasi Unit',
            'transmisi': 'Transmisi',
            'jarak_tempuh': 'Jarak Tempuh (km)',
            'harga': 'Harga OTR Normal (Rp)',
            'harga_promo': 'Harga Promo / Subsidi DP (Rp)',
            'tipe_penjual': 'Sumber Unit',
            'status_stok': 'Status Stok',
            'lulus_inspeksi': 'Lulus 150 Titik Inspeksi',
            'ringkasan_inspeksi': 'Ringkasan Hasil Inspeksi',
            'gambar_utama': 'Foto Utama',
            'deskripsi_singkat': 'Deskripsi Singkat (untuk kartu katalog)',
            'deskripsi_detail': 'Deskripsi Detail & Spesifikasi',
        }
        help_texts = {
            'harga_promo': 'Opsional. Kosongkan jika tidak ada promo; harus lebih rendah dari harga normal.',
            'gambar_utama': 'JPG, PNG, atau WEBP. Maksimal 5 MB. Disarankan rasio 4:3 (landscape).',
            'deskripsi_singkat': '2–3 kalimat yang tampil di kartu katalog.',
        }
        widgets = {
            'sku': forms.TextInput(attrs={'placeholder': 'MBG-SUV-HYD-001', 'autocomplete': 'off'}),
            'nama': forms.TextInput(attrs={'placeholder': 'Hyundai Palisade 2.2 Signature AT 2021'}),
            'merek': forms.TextInput(attrs={'placeholder': 'Toyota', 'list': 'daftar-merek', 'autocomplete': 'off'}),
            'kategori': forms.TextInput(attrs={'placeholder': 'SUV, MPV, City Car', 'list': 'daftar-kategori', 'autocomplete': 'off'}),
            'tahun': forms.NumberInput(attrs={'placeholder': '2021', 'inputmode': 'numeric'}),
            'warna': forms.TextInput(attrs={'placeholder': 'Putih'}),
            'plat_nomor': forms.TextInput(attrs={'placeholder': 'DD 1234 XX'}),
            'lokasi': forms.TextInput(attrs={'placeholder': 'Makassar'}),
            'jarak_tempuh': forms.NumberInput(attrs={'placeholder': '35000', 'min': 0, 'inputmode': 'numeric'}),
            'harga': forms.NumberInput(attrs={'placeholder': '250000000', 'min': 0, 'step': 1000, 'data-rupiah': '1', 'inputmode': 'numeric'}),
            'harga_promo': forms.NumberInput(attrs={'placeholder': '(opsional)', 'min': 0, 'step': 1000, 'data-rupiah': '1', 'inputmode': 'numeric'}),
            'ringkasan_inspeksi': forms.Textarea(attrs={'rows': 2}),
            'gambar_utama': forms.FileInput(attrs={'accept': 'image/jpeg,image/png,image/webp'}),
            'deskripsi_singkat': forms.Textarea(attrs={'rows': 3}),
            'deskripsi_detail': forms.Textarea(attrs={'rows': 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Catat foto utama lama agar bisa dihapus dari storage saat diganti.
        self._foto_lama = (
            self.instance.gambar_utama.name
            if self.instance.pk and self.instance.gambar_utama else None
        )

        # Pilihan "Pilih…" yang jelas untuk dropdown, tanpa mengubah pilihan model.
        for nama in ('transmisi', 'tipe_penjual', 'status_stok'):
            self.fields[nama].widget.attrs['class'] = 'form-select'

        # Saran merek/kategori dari data yang sudah ada → penulisan konsisten
        # (mis. tidak muncul "Toyota" dan "toyota" sebagai dua merek berbeda).
        self.saran_merek = list(Produk.objects.order_by('merek').values_list('merek', flat=True).distinct())
        self.saran_kategori = list(Produk.objects.order_by('kategori').values_list('kategori', flat=True).distinct())

        # Kelas Bootstrap untuk field lainnya.
        for nama, field in self.fields.items():
            w = field.widget
            if isinstance(w, forms.CheckboxInput):
                w.attrs['class'] = 'form-check-input'
            elif not w.attrs.get('class'):
                w.attrs['class'] = 'form-control'

    # ── Validasi per field ─────────────────────────────
    def clean_sku(self):
        sku = (self.cleaned_data.get('sku') or '').strip().upper()
        if not re.match(r'^[A-Z0-9][A-Z0-9._-]*$', sku):
            raise ValidationError("SKU hanya boleh huruf, angka, titik, strip (-), dan underscore (_).")
        if Produk.objects.filter(sku__iexact=sku).exclude(pk=self.instance.pk).exists():
            raise ValidationError("SKU ini sudah dipakai unit lain. Gunakan kode yang berbeda.")
        return sku

    def clean_nama(self):
        return ' '.join((self.cleaned_data.get('nama') or '').split())

    def clean_merek(self):
        merek = ' '.join((self.cleaned_data.get('merek') or '').split())
        # Samakan penulisan dengan merek yang sudah ada (tidak peka huruf besar/kecil).
        for ada in self.saran_merek:
            if ada.lower() == merek.lower():
                return ada
        return merek

    def clean_kategori(self):
        kategori = ' '.join((self.cleaned_data.get('kategori') or '').split())
        for ada in self.saran_kategori:
            if ada.lower() == kategori.lower():
                return ada
        return kategori

    def clean_tahun(self):
        tahun = self.cleaned_data.get('tahun')
        batas = timezone.localdate().year + 1
        if tahun is not None and not (1980 <= tahun <= batas):
            raise ValidationError(f"Tahun harus antara 1980 dan {batas}.")
        return tahun

    def clean_jarak_tempuh(self):
        km = self.cleaned_data.get('jarak_tempuh')
        if km is not None and not (0 <= km <= 2_000_000):
            raise ValidationError("Jarak tempuh harus antara 0 dan 2.000.000 km.")
        return km

    def clean_harga(self):
        harga = self.cleaned_data.get('harga')
        if harga is not None and harga <= 0:
            raise ValidationError("Harga harus lebih besar dari 0.")
        return harga

    def clean_plat_nomor(self):
        return ' '.join((self.cleaned_data.get('plat_nomor') or '').upper().split())

    def clean_gambar_utama(self):
        return validasi_foto_mobil(self.cleaned_data.get('gambar_utama'))

    def clean(self):
        data = super().clean()
        harga, promo = data.get('harga'), data.get('harga_promo')
        if harga is not None and promo is not None and promo >= harga:
            self.add_error('harga_promo', "Harga promo harus lebih rendah dari harga normal.")
        return data

    def save(self, commit=True):
        produk = super().save(commit=commit)
        if commit:
            # Hapus file foto lama dari storage jika sudah diganti.
            nama_sekarang = produk.gambar_utama.name if produk.gambar_utama else None
            if self._foto_lama and self._foto_lama != nama_sekarang:
                produk.gambar_utama.storage.delete(self._foto_lama)
        return produk


# ═══════════════════════════════════════════════════════════════════
# FORM KATEGORI ARTIKEL
# ═══════════════════════════════════════════════════════════════════

def validasi_gambar_artikel(f):
    """Validasi tipe dan ukuran gambar untuk artikel (maks 5 MB)."""
    if f is None:
        return f
    if not isinstance(f, UploadedFile):
        return f
    tipe_diizinkan = ['image/jpeg', 'image/png', 'image/webp']
    if hasattr(f, 'content_type') and f.content_type not in tipe_diizinkan:
        raise ValidationError("Format gambar tidak didukung. Gunakan JPEG, PNG, atau WebP.")
    ext = (f.name or '').rsplit('.', 1)[-1].lower()
    if ext not in ('jpg', 'jpeg', 'png', 'webp'):
        raise ValidationError("Ekstensi file tidak valid. Gunakan .jpg, .jpeg, .png, atau .webp.")
    if f.size > 5 * 1024 * 1024:
        raise ValidationError("Ukuran gambar tidak boleh melebihi 5 MB.")
    return f


class KategoriArtikelForm(forms.ModelForm):
    class Meta:
        model  = KategoriArtikel
        fields = ['nama', 'slug', 'deskripsi']
        widgets = {
            'nama': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Nama kategori, mis. Tips Membeli Mobil',
                'maxlength': 100,
            }),
            'slug': forms.TextInput(attrs={
                'class': 'form-control font-monospace',
                'placeholder': 'otomatis-dari-nama (kosongkan untuk auto)',
            }),
            'deskripsi': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Deskripsi singkat kategori (opsional)',
            }),
        }

    def clean_nama(self):
        nama = (self.cleaned_data.get('nama') or '').strip()
        if not nama:
            raise ValidationError("Nama kategori tidak boleh kosong.")
        return nama

    def clean_slug(self):
        slug = (self.cleaned_data.get('slug') or '').strip()
        if slug:
            from django.utils.text import slugify
            slug = slugify(slug)
        return slug


# ═══════════════════════════════════════════════════════════════════
# FORM ARTIKEL & BERITA
# ═══════════════════════════════════════════════════════════════════

class ArtikelForm(forms.ModelForm):
    class Meta:
        model  = Artikel
        fields = [
            'judul', 'slug', 'ringkasan', 'isi',
            'gambar_utama', 'kategori', 'status',
            'meta_title', 'meta_description',
        ]
        widgets = {
            'judul': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Judul artikel',
                'maxlength': 200,
            }),
            'slug': forms.TextInput(attrs={
                'class': 'form-control font-monospace',
                'placeholder': 'otomatis-dari-judul (kosongkan untuk auto)',
            }),
            'ringkasan': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Ringkasan singkat yang tampil di daftar artikel',
            }),
            'isi': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 15,
                'placeholder': 'Isi artikel lengkap...',
                'id': 'id_isi',
            }),
            'kategori': forms.Select(attrs={'class': 'form-select'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'meta_title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Meta title untuk SEO (opsional)',
                'maxlength': 200,
            }),
            'meta_description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Meta description untuk SEO (opsional)',
            }),
            'gambar_utama': forms.ClearableFileInput(attrs={
                'class': 'form-control',
                'accept': 'image/jpeg,image/png,image/webp',
            }),
        }

    def clean_judul(self):
        judul = (self.cleaned_data.get('judul') or '').strip()
        if not judul:
            raise ValidationError("Judul artikel tidak boleh kosong.")
        return judul

    def clean_ringkasan(self):
        v = (self.cleaned_data.get('ringkasan') or '').strip()
        if not v:
            raise ValidationError("Ringkasan tidak boleh kosong.")
        return v

    def clean_isi(self):
        v = (self.cleaned_data.get('isi') or '').strip()
        if not v:
            raise ValidationError("Isi artikel tidak boleh kosong.")
        return v

    def clean_slug(self):
        slug = (self.cleaned_data.get('slug') or '').strip()
        if slug:
            from django.utils.text import slugify
            slug = slugify(slug)
        return slug

    def clean_gambar_utama(self):
        return validasi_gambar_artikel(self.cleaned_data.get('gambar_utama'))


# ═══════════════════════════════════════════════════════════════════
# FORM HALAMAN WEBSITE
# ═══════════════════════════════════════════════════════════════════

class HalamanWebsiteForm(forms.ModelForm):
    class Meta:
        model  = HalamanWebsite
        fields = [
            'judul', 'slug', 'ringkasan', 'isi',
            'status', 'tampil_di_footer', 'urutan',
            'meta_title', 'meta_description',
        ]
        labels = {
            'urutan': 'Urutan tampil',
        }
        widgets = {
            'judul': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Contoh: Tentang Kami, Syarat & Ketentuan',
                'maxlength': 200,
            }),
            'slug': forms.TextInput(attrs={
                'class': 'form-control font-monospace',
                'placeholder': 'otomatis-dari-judul (kosongkan untuk auto)',
            }),
            'ringkasan': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 2,
                'placeholder': 'Satu atau dua kalimat pengantar (opsional)',
            }),
            'isi': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 16,
                'placeholder': 'Isi halaman lengkap...',
            }),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'tampil_di_footer': forms.CheckboxInput(attrs={
                'class': 'form-check-input',
                'role': 'switch',
            }),
            'urutan': forms.NumberInput(attrs={
                'class': 'form-control',
                'min': 0,
                'inputmode': 'numeric',
            }),
            'meta_title': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'Meta title untuk SEO (opsional)',
                'maxlength': 200,
            }),
            'meta_description': forms.Textarea(attrs={
                'class': 'form-control',
                'rows': 3,
                'placeholder': 'Meta description untuk SEO (opsional)',
            }),
        }

    def clean_judul(self):
        judul = ' '.join((self.cleaned_data.get('judul') or '').split())
        if not judul:
            raise ValidationError("Judul halaman tidak boleh kosong.")
        return judul

    def clean_ringkasan(self):
        return (self.cleaned_data.get('ringkasan') or '').strip()

    def clean_isi(self):
        isi = (self.cleaned_data.get('isi') or '').strip()
        if not isi:
            raise ValidationError("Isi halaman tidak boleh kosong.")
        return isi

    def clean_slug(self):
        slug = (self.cleaned_data.get('slug') or '').strip()
        if slug:
            from django.utils.text import slugify
            slug = slugify(slug)
        return slug
