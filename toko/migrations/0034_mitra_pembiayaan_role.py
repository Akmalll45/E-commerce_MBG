# Generated manually for Mitra Pembiayaan (Simulasi) third-party role

from django.db import migrations


MITRA_PEMBIAYAAN_ROLE = 'Mitra Pembiayaan (Simulasi)'
OLD_ADMIN_PEMBIAYAAN_ROLE = 'Admin Pembiayaan (Simulasi)'
KREDIT_PERMISSION_NAMES = {
    'view_kredit_pesananpembelian': 'Can view credit applications (Mitra Pembiayaan)',
    'review_kredit_pesananpembelian': 'Can approve or reject credit applications (Mitra Pembiayaan)',
}


def migrate_to_mitra_pembiayaan_role(apps, schema_editor):
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
        if perm.name != name:
            perm.name = name
            perm.save(using=database, update_fields=['name'])
        desired_permissions.append(perm)

    mitra_group, _ = Group.objects.using(database).get_or_create(name=MITRA_PEMBIAYAAN_ROLE)
    existing_ids = set(
        mitra_group.permissions.db_manager(database).values_list('pk', flat=True)
    )
    desired_ids = {p.pk for p in desired_permissions}
    stale_ids = existing_ids - desired_ids
    if stale_ids:
        mitra_group.permissions.db_manager(database).remove(*stale_ids)
    mitra_group.permissions.db_manager(database).add(*desired_permissions)

    # Jika grup lama Admin Pembiayaan (Simulasi) ada, pindahkan anggotanya ke grup baru lalu hapus izinnya.
    old_group = Group.objects.using(database).filter(name=OLD_ADMIN_PEMBIAYAAN_ROLE).first()
    if old_group:
        for user in old_group.user_set.db_manager(database).all():
            user.groups.db_manager(database).add(mitra_group)
            user.groups.db_manager(database).remove(old_group)
        old_group.permissions.db_manager(database).clear()
        old_group.delete(using=database)


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0033_admin_pembiayaan_role'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='pesananpembelian',
            options={
                'ordering': ['-created_at', '-pk'],
                'permissions': [
                    ('view_kredit_pesananpembelian', 'Can view credit applications (Mitra Pembiayaan)'),
                    ('review_kredit_pesananpembelian', 'Can approve or reject credit applications (Mitra Pembiayaan)'),
                ],
                'verbose_name': 'Pesanan Pembelian',
                'verbose_name_plural': 'Pesanan Pembelian',
            },
        ),
        migrations.RunPython(migrate_to_mitra_pembiayaan_role, migrations.RunPython.noop),
    ]
