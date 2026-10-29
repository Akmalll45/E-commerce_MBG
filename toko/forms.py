from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
import re
from django.core.files.uploadedfile import UploadedFile

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