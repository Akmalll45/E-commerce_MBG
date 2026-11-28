# Generated manually following 0015_c2c_permissions_and_inspection_default.py

from django.db import migrations, models


ADMIN_PEMBIAYAAN_ROLE = 'Admin Pembiayaan (Simulasi)'
KREDIT_PERMISSION_NAMES = {
    'view_kredit_pesananpembelian': 'Can view credit applications (Admin Pembiayaan)',
    'review_kredit_pesananpembelian': 'Can approve or reject credit applications (Admin Pembiayaan)',
}


def create_admin_pembiayaan_role(apps, schema_editor):
    database = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    content_type, _ = ContentType.objects.using(database).get_or_create(
        app_label='toko',
        model='pesananpembelian',
    )

    desired_permissions = []
    for codename, name in KREDIT_PERMISSION_NAMES.items():
        perm, _ = Permission.objects.using(database).get_or_create(
            content_type_id=content_type.pk,
            codename=codename,
            defaults={'name': name},
        )
        desired_permissions.append(perm)

    group, _ = Group.objects.using(database).get_or_create(name=ADMIN_PEMBIAYAAN_ROLE)
    # Role ini hanya boleh memiliki permission review/view kredit, bukan akses admin/stok/katalog lain.
    existing_ids = set(
        group.permissions.db_manager(database).values_list('pk', flat=True)
    )
    desired_ids = {p.pk for p in desired_permissions}
    stale_ids = existing_ids - desired_ids
    if stale_ids:
        group.permissions.db_manager(database).remove(*stale_ids)
    group.permissions.db_manager(database).add(*desired_permissions)


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0032_alter_bookingcekunit_options_bookingcekunit_catatan_and_more'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='pesananpembelian',
            options={
                'ordering': ['-created_at', '-pk'],
                'permissions': [
                    ('view_kredit_pesananpembelian', 'Can view credit applications (Admin Pembiayaan)'),
                    ('review_kredit_pesananpembelian', 'Can approve or reject credit applications (Admin Pembiayaan)'),
                ],
                'verbose_name': 'Pesanan Pembelian',
                'verbose_name_plural': 'Pesanan Pembelian',
            },
        ),
        migrations.RunPython(create_admin_pembiayaan_role, migrations.RunPython.noop),
    ]
