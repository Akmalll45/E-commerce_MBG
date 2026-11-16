from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0022_refundpesanan_pembayaran'),
    ]

    operations = [
        migrations.AlterField(
            model_name='pembayaranpesanan',
            name='saluran',
            field=models.CharField(
                choices=[
                    ('transfer', 'Transfer Bank Manual'),
                    ('va', 'Virtual Account'),
                    ('qris', 'QRIS'),
                    ('ewallet', 'E-Wallet'),
                    ('kartu', 'Kartu Kredit'),
                    ('ritel', 'Gerai Retail'),
                    ('tunai', 'Tunai di Showroom'),
                    ('lainnya', 'Metode Online Lainnya'),
                ],
                default='transfer',
                max_length=10,
                verbose_name='Saluran Pembayaran',
            ),
        ),
    ]
