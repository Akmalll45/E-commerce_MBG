from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, Produk, FotoMobil, TitipJual, BookingCekUnit, Transaksi

class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ('username', 'email', 'first_name', 'last_name', 'no_whatsapp', 'is_staff', 'is_active')
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'groups')
    search_fields = ('username', 'email', 'first_name', 'last_name', 'no_whatsapp')
    fieldsets = UserAdmin.fieldsets + (
        ('Informasi Tambahan MBG', {'fields': ('no_whatsapp', 'alamat')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Informasi Tambahan MBG', {'fields': ('no_whatsapp', 'alamat')}),
    )

admin.site.register(CustomUser, CustomUserAdmin)
admin.site.register(Produk)
admin.site.register(FotoMobil)
admin.site.register(TitipJual)
admin.site.register(BookingCekUnit)
admin.site.register(Transaksi)
