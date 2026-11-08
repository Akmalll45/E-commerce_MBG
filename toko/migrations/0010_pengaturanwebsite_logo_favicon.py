from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('toko', '0009_pengaturanwebsite'),
    ]

    operations = [
        migrations.AddField(
            model_name='pengaturanwebsite',
            name='logo',
            field=models.ImageField(blank=True, null=True, upload_to='situs/', verbose_name='Logo Website'),
        ),
        migrations.AddField(
            model_name='pengaturanwebsite',
            name='favicon',
            field=models.ImageField(blank=True, null=True, upload_to='situs/', verbose_name='Favicon'),
        ),
    ]
