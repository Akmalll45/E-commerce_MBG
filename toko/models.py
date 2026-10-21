from django.db import models
from django.contrib.auth.models import AbstractUser

# 1. Custom User
class CustomUser(AbstractUser):
    ROLE_CHOICES = (
        ('admin', 'Admin'),
        ('client', 'Client'),
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='client')

# 2. Tabel Kategori
class Category(models.Model):
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

# 3. Tabel Produk (Diperbarui dengan default)
class Product(models.Model):
    STATUS_CHOICES = (
        ('tersedia', 'Tersedia'),
        ('dibooking', 'Dibooking'),
        ('terjual', 'Terjual'),
    )
    TRANSMISI_CHOICES = (
        ('AT', 'Automatic'),
        ('MT', 'Manual'),
    )

    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='products')
    price = models.DecimalField(max_digits=15, decimal_places=2)
    description = models.TextField()
    
    image = models.ImageField(upload_to='mobil/', blank=True, null=True)
    tahun = models.IntegerField(default=2015, help_text="Tahun pembuatan mobil") 
    transmisi = models.CharField(max_length=2, choices=TRANSMISI_CHOICES, default='AT')
    kilometer = models.IntegerField(default=0, help_text="Jarak tempuh kendaraan")
    bahan_bakar = models.CharField(max_length=50, default="Bensin")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default='tersedia')

    def __str__(self):
        return f"{self.name} ({self.tahun})"

# 4. Tabel Pages
class Page(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True) 
    content = models.TextField()

    def __str__(self):
        return self.title

# 5. Tabel Artikel
class Artikel(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    author = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title