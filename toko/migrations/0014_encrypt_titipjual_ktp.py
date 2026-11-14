from cryptography.fernet import InvalidToken
from django.db import migrations

import toko.fields


def _process_ktp(apps, schema_editor, decrypt=False):
    TitipJual = apps.get_model('toko', 'TitipJual')
    connection = schema_editor.connection
    table = connection.ops.quote_name(TitipJual._meta.db_table)
    pk_column = connection.ops.quote_name(TitipJual._meta.pk.column)
    ktp_column = connection.ops.quote_name(TitipJual._meta.get_field('no_ktp').column)
    last_pk = None
    cipher = None

    while True:
        where = f'{ktp_column} IS NOT NULL AND {ktp_column} <> %s'
        params = ['']
        if last_pk is not None:
            where = f'{pk_column} > %s AND {where}'
            params.insert(0, last_pk)
        with connection.cursor() as cursor:
            cursor.execute(
                f'SELECT {pk_column}, {ktp_column} FROM {table} '
                f'WHERE {where} ORDER BY {pk_column} LIMIT 500',
                params,
            )
            rows = cursor.fetchall()
        if not rows:
            break

        if cipher is None:
            cipher = toko.fields.get_ktp_fernet()

        updates = []
        for pk, value in rows:
            value = value if isinstance(value, str) else value.decode('utf-8')
            try:
                plaintext = cipher.decrypt(value.encode('ascii')).decode('utf-8')
                is_encrypted = True
            except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError):
                plaintext = value
                is_encrypted = False

            if decrypt:
                if not is_encrypted:
                    raise RuntimeError(
                        f'Titip-Jual {pk} does not contain decryptable KTP data.'
                    )
                updates.append((plaintext, pk))
            elif is_encrypted:
                continue
            elif value.startswith('gAAAA'):
                raise RuntimeError(
                    'KTP ciphertext was found but could not be decrypted. '
                    'Verify C2C_KTP_ENCRYPTION_KEY before retrying.'
                )
            else:
                updates.append((cipher.encrypt(value.encode('utf-8')).decode('ascii'), pk))

        if updates:
            with connection.cursor() as cursor:
                cursor.executemany(
                    f'UPDATE {table} SET {ktp_column} = %s WHERE {pk_column} = %s',
                    updates,
                )
        last_pk = rows[-1][0]


def encrypt_legacy_ktp(apps, schema_editor):
    _process_ktp(apps, schema_editor)


def decrypt_ktp_for_rollback(apps, schema_editor):
    from django.conf import settings

    if not getattr(settings, 'C2C_KTP_ALLOW_PLAINTEXT_ROLLBACK', False):
        raise RuntimeError(
            'KTP migration rollback would restore plaintext data. '
            'Set C2C_KTP_ALLOW_PLAINTEXT_ROLLBACK=true only for an approved rollback.'
        )
    _process_ktp(apps, schema_editor, decrypt=True)


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0013_checklistinspeksic2c_serahterimaunit_foto_kondisi_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='titipjual',
            name='no_ktp',
            field=toko.fields.EncryptedCharField(
                blank=True,
                help_text='Disimpan terenkripsi; jangan mengubah kunci enkripsi tanpa rencana rotasi.',
                max_length=255,
                max_plaintext_length=20,
                verbose_name='Nomor KTP',
            ),
        ),
        migrations.RunPython(encrypt_legacy_ktp, decrypt_ktp_for_rollback),
    ]
