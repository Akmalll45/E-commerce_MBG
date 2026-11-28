# Memastikan akun Mitra Pembiayaan (Simulasi) yang sudah ada di database
# tidak memiliki is_staff=True. Migration 0034 memindahkan anggota grup lama
# tetapi belum meng-update flag is_staff. Migration ini melengkapinya secara idempoten.

from django.db import migrations

MITRA_PEMBIAYAAN_ROLE = 'Mitra Pembiayaan (Simulasi)'


def set_mitra_is_staff_false(apps, schema_editor):
    """
    Untuk setiap anggota grup Mitra Pembiayaan (Simulasi) yang bukan superuser,
    pastikan is_staff=False agar mereka tidak dapat mengakses modul panel admin MBG.
    Superuser tidak disentuh.
    """
    database = schema_editor.connection.alias
    Group = apps.get_model('auth', 'Group')
    User = apps.get_model('toko', 'CustomUser')

    mitra_group = Group.objects.using(database).filter(name=MITRA_PEMBIAYAAN_ROLE).first()
    if not mitra_group:
        return  # Belum ada grup, tidak ada yang perlu diubah

    # Ambil semua anggota grup mitra yang bukan superuser dan masih is_staff=True
    to_update = []
    for user in mitra_group.user_set.db_manager(database).filter(is_staff=True, is_superuser=False):
        to_update.append(user.pk)

    if to_update:
        User.objects.using(database).filter(pk__in=to_update).update(is_staff=False)


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0034_mitra_pembiayaan_role'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.RunPython(set_mitra_is_staff_false, migrations.RunPython.noop),
    ]
