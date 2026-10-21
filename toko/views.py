from django.shortcuts import render
from .models import Product # Mengambil tabel Product yang sudah di buat buat

def beranda(request):
    # Mengambil semua mobil dari database yang statusnya 'tersedia' saja
    mobil_tersedia = Product.objects.filter(status='tersedia')

    # Membungkus data tersebut ke dalam sebuah 'dictionary' (paket pengiriman)
    context = {
        'katalog_mobil': mobil_tersedia,
    }

    # Mengirimkan paket data tersebut ke file desain HTML bernama 'beranda.html'
    return render(request, 'beranda.html', context)