from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, ProviderProfile, CustomerProfile


class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ['email', 'phone', 'user_type', 'is_staff', 'is_active']
    ordering = ['email']
    search_fields = ['email', 'phone']

    # Remove username from fieldsets since we removed it from the model
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone', 'user_type')}),
        ('Permissions', {
            'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions'),
        }),
        ('Important dates', {'fields': ('last_login',)}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password', 'phone', 'user_type', 'is_staff', 'is_active')}
        ),
    )


@admin.register(ProviderProfile)
class ProviderProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'status', 'created_at', 'updated_at']
    list_filter = ['status']
    search_fields = ['user__email']
    ordering = ['-created_at']
    list_editable = ['status']


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'created_at', 'updated_at']
    search_fields = ['user__email']
    ordering = ['-created_at']


admin.site.register(CustomUser, CustomUserAdmin)
