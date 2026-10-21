from django.contrib import admin
from .models import CustomUser, Category, Product, Page, Artikel

# Mendaftarkan tabel agar bisa dikelola lewat website
admin.site.register(CustomUser)
admin.site.register(Category)
admin.site.register(Product)
admin.site.register(Page)
admin.site.register(Artikel)