import toko.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0007_heroslide'),
    ]

    operations = [
        migrations.CreateModel(
            name='Mitra',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nama', models.CharField(max_length=150, verbose_name='Nama Mitra')),
                ('jenis', models.CharField(choices=[('pembiayaan', 'Pembiayaan / Leasing'), ('bank', 'Bank'), ('asuransi', 'Asuransi'), ('inspeksi', 'Bengkel & Inspeksi'), ('lainnya', 'Lainnya')], default='lainnya', max_length=12, verbose_name='Jenis Kerja Sama')),
                ('keterangan', models.CharField(blank=True, max_length=160, verbose_name='Keterangan Singkat')),
                ('logo', models.ImageField(blank=True, null=True, upload_to='mitra/', verbose_name='Logo')),
                ('situs', models.CharField(blank=True, max_length=300, validators=[toko.models.validasi_situs_mitra], verbose_name='Situs Web')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('published', 'Tayang')], default='draft', max_length=12, verbose_name='Status')),
                ('urutan', models.PositiveSmallIntegerField(default=0, verbose_name='Urutan')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Mitra',
                'verbose_name_plural': 'Mitra',
                'ordering': ['urutan', 'nama'],
            },
        ),
    ]
