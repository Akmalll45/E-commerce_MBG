from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError

from toko.models import ROLE_MITRA_PEMBIAYAAN


class Command(BaseCommand):
    help = (
        'Buat atau tetapkan akun pihak ketiga Mitra Pembiayaan (Simulasi) '
        'dengan is_staff=False dan is_superuser=False.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--username', required=True, help='Username akun mitra.')
        parser.add_argument('--email', default='', help='Email akun mitra (opsional).')
        parser.add_argument('--nama', default='', help='Nama lengkap akun mitra (opsional).')

    def handle(self, *args, **options):
        User = get_user_model()
        username = options['username'].strip()
        if not username:
            raise CommandError('Username tidak boleh kosong.')

        group = Group.objects.filter(name=ROLE_MITRA_PEMBIAYAAN).first()
        if group is None:
            raise CommandError(
                f'Group "{ROLE_MITRA_PEMBIAYAAN}" belum ada. Jalankan "python manage.py migrate" terlebih dahulu.'
            )

        user = User.objects.filter(username=username).first()
        if user is None:
            password = getpass('Masukkan password untuk akun Mitra Pembiayaan: ')
            confirm = getpass('Ulangi password: ')
            if not password or password != confirm:
                raise CommandError('Password kosong atau tidak cocok.')
            user = User.objects.create_user(
                username=username,
                email=options['email'].strip(),
                first_name=options['nama'].strip(),
                password=password,
                is_staff=False,
                is_superuser=False,
            )
            action = 'dibuat'
        else:
            if user.is_superuser:
                raise CommandError('Akun superuser tidak boleh dijadikan akun mitra biasa.')
            user.is_staff = False
            user.save(update_fields=['is_staff'])
            action = 'diperbarui'

        user.groups.add(group)
        self.stdout.write(
            self.style.SUCCESS(
                f'Akun "{user.username}" berhasil {action} sebagai {ROLE_MITRA_PEMBIAYAAN} (is_staff=False).'
            )
        )
