from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0005_add_kategoriartikel_artikel'),
    ]

    operations = [
        migrations.CreateModel(
            name='HalamanWebsite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('judul', models.CharField(max_length=200, verbose_name='Judul Halaman')),
                ('slug', models.SlugField(blank=True, max_length=220, unique=True)),
                ('ringkasan', models.TextField(blank=True, verbose_name='Ringkasan')),
                ('isi', models.TextField(verbose_name='Isi Halaman')),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('published', 'Diterbitkan')], default='draft', max_length=12, verbose_name='Status')),
                ('tampil_di_footer', models.BooleanField(default=False, verbose_name='Tampilkan di footer')),
                ('urutan', models.PositiveSmallIntegerField(default=0, verbose_name='Urutan')),
                ('meta_title', models.CharField(blank=True, max_length=200, verbose_name='Meta Title')),
                ('meta_description', models.TextField(blank=True, verbose_name='Meta Description')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Halaman Website',
                'verbose_name_plural': 'Halaman Website',
                'ordering': ['urutan', 'judul'],
            },
        ),
    ]
