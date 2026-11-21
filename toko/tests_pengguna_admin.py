from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class AdminPenggunaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='admin-pengguna',
            email='admin-pengguna@example.test',
            password='SecureAdminPass!2026',
        )
        self.client.force_login(self.admin)

    def test_daftar_menampilkan_statistik_dan_filter_pengguna(self):
        group = Group.objects.create(name='Petugas Uji')
        cocok = User.objects.create_user(
            username='pelanggan-uji',
            email='pelanggan@example.test',
            password='SecureUserPass!2026',
            is_active=True,
        )
        cocok.groups.add(group)
        User.objects.create_user(
            username='nonaktif-uji',
            email='nonaktif@example.test',
            password='SecureUserPass!2026',
            is_active=False,
        )

        response = self.client.get(reverse('admin_pengguna'), {
            'q': 'pelanggan',
            'group': str(group.pk),
            'status': 'aktif',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['pengguna_list']), [cocok])
        self.assertEqual(response.context['total_pengguna'], 3)
        self.assertEqual(response.context['pengguna_aktif'], 2)
        self.assertContains(response, 'Data Pengguna')
        self.assertContains(response, '<meta property="og:image"')
        self.assertContains(response, 'http://testserver/static/toko/images/mbg-logo.')

    def test_tambah_pengguna_menyimpan_password_dan_role_pelanggan(self):
        response = self.client.post(reverse('admin_pengguna_tambah'), {
            'username': 'akun-baru',
            'first_name': 'Akun',
            'last_name': 'Baru',
            'email': 'akun-baru@example.test',
            'is_active': 'on',
            'password1': 'SafeUserPassword!2026',
            'password2': 'SafeUserPassword!2026',
        })

        self.assertRedirects(response, reverse(
            'admin_pengguna_detail',
            kwargs={'pengguna_id': User.objects.get(username='akun-baru').pk},
        ))
        pengguna = User.objects.get(username='akun-baru')
        self.assertTrue(pengguna.check_password('SafeUserPassword!2026'))
        self.assertFalse(pengguna.is_staff)
        self.assertFalse(pengguna.is_superuser)

    def test_staff_membutuhkan_permission_view_customuser(self):
        staff = User.objects.create_user(
            username='staff-tanpa-izin',
            password='SecureStaffPass!2026',
            is_staff=True,
        )
        self.client.force_login(staff)

        response = self.client.get(reverse('admin_pengguna'))

        self.assertEqual(response.status_code, 403)

    def test_status_akun_berubah_dengan_post_dan_admin_tidak_dapat_mematikan_dirinya(self):
        pengguna = User.objects.create_user(
            username='akun-nonaktif',
            password='SecureUserPass!2026',
            is_active=False,
        )
        response = self.client.post(reverse(
            'admin_pengguna_status',
            kwargs={'pengguna_id': pengguna.pk},
        ))
        pengguna.refresh_from_db()
        self.assertTrue(pengguna.is_active)
        self.assertRedirects(response, reverse('admin_pengguna'))

        response = self.client.post(reverse(
            'admin_pengguna_status',
            kwargs={'pengguna_id': self.admin.pk},
        ))
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertRedirects(response, reverse('admin_pengguna'))

    def test_superuser_aktif_terakhir_tidak_dapat_dinonaktifkan(self):
        content_type = ContentType.objects.get_for_model(User)
        staff = User.objects.create_user(
            username='staff-pengelola-pengguna',
            password='SecureStaffPass!2026',
            is_staff=True,
        )
        staff.user_permissions.add(
            Permission.objects.get(content_type=content_type, codename='view_customuser'),
            Permission.objects.get(content_type=content_type, codename='change_customuser'),
        )
        self.client.force_login(staff)

        response = self.client.post(reverse(
            'admin_pengguna_status',
            kwargs={'pengguna_id': self.admin.pk},
        ))

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertRedirects(response, reverse('admin_pengguna'))

    def test_admin_tidak_dapat_menonaktifkan_dirinya_sendiri_dari_form_edit(self):
        response = self.client.post(reverse(
            'admin_pengguna_edit',
            kwargs={'pengguna_id': self.admin.pk},
        ), {
            'username': self.admin.username,
            'first_name': self.admin.first_name,
            'last_name': self.admin.last_name,
            'email': self.admin.email,
        })

        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Anda tidak dapat menonaktifkan akun yang sedang digunakan.')

    def test_group_hanya_dapat_diubah_dengan_permission_change_group(self):
        group = Group.objects.create(name='Group Bisa Dipilih')
        content_type = ContentType.objects.get_for_model(Group)
        permission = Permission.objects.get(
            content_type=content_type,
            codename='change_group',
        )
        staff = User.objects.create_user(
            username='staff-group',
            password='SecureStaffPass!2026',
            is_staff=True,
        )
        staff.user_permissions.add(
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(User),
                codename='view_customuser',
            ),
            Permission.objects.get(
                content_type=ContentType.objects.get_for_model(User),
                codename='add_customuser',
            ),
        )
        self.client.force_login(staff)

        response = self.client.post(reverse('admin_pengguna_tambah'), {
            'username': 'staff-created-user',
            'email': 'created@example.test',
            'is_active': 'on',
            'password1': 'SafeUserPassword!2026',
            'password2': 'SafeUserPassword!2026',
            'groups': [str(group.pk)],
        })

        self.assertEqual(response.status_code, 302)
        self.assertFalse(User.objects.get(username='staff-created-user').groups.exists())
        staff.user_permissions.add(permission)
        staff = User.objects.get(pk=staff.pk)
        self.client.force_login(staff)
        response = self.client.post(reverse('admin_pengguna_tambah'), {
            'username': 'staff-created-user-with-group',
            'email': 'created-with-group@example.test',
            'is_active': 'on',
            'password1': 'SafeUserPassword!2026',
            'password2': 'SafeUserPassword!2026',
            'groups': [str(group.pk)],
        })
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            User.objects.get(username='staff-created-user-with-group').groups.filter(pk=group.pk).exists()
        )
