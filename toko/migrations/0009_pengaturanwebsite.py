import toko.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0008_mitra'),
    ]

    operations = [
        migrations.CreateModel(
            name='PengaturanWebsite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('whatsapp', models.CharField(default='6281234567890', max_length=15, validators=[toko.models.validasi_whatsapp], verbose_name='Nomor WhatsApp')),
                ('email', models.EmailField(blank=True, default='halo@mobilbekasgarasi.com', max_length=254, verbose_name='Email')),
                ('alamat', models.CharField(default='Jl. A.P. Pettarani No. 88, Bonto-Bontoa, Makassar, Sulawesi Selatan 90222', max_length=250, verbose_name='Alamat Lengkap')),
                ('alamat_singkat', models.CharField(default='Jl. A.P. Pettarani No. 88, Makassar', max_length=120, verbose_name='Alamat Singkat')),
                ('jam_buka', models.CharField(default='08.30 – 18.00 WITA Setiap Hari', max_length=120, verbose_name='Jam Operasional')),
                ('link_peta', models.CharField(blank=True, max_length=500, validators=[toko.models.validasi_situs_mitra], verbose_name='Link Google Maps')),
                ('deskripsi_footer', models.TextField(blank=True, default='Platform e-commerce otomotif terpercaya di Makassar dan Sulawesi Selatan. Mengutamakan transparansi harga, garansi mesin, dan jaminan 100% lulus 150 titik inspeksi dengan model Online-to-Offline (O2O).', verbose_name='Deskripsi Footer')),
                ('instagram', models.CharField(blank=True, max_length=300, validators=[toko.models.validasi_situs_mitra], verbose_name='Instagram')),
                ('facebook', models.CharField(blank=True, max_length=300, validators=[toko.models.validasi_situs_mitra], verbose_name='Facebook')),
                ('tiktok', models.CharField(blank=True, max_length=300, validators=[toko.models.validasi_situs_mitra], verbose_name='TikTok')),
                ('youtube', models.CharField(blank=True, max_length=300, validators=[toko.models.validasi_situs_mitra], verbose_name='YouTube')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Pengaturan Website',
                'verbose_name_plural': 'Pengaturan Website',
            },
        ),
    ]
