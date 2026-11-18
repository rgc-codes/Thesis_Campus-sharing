# core/admin.py
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, Category, Item, Reservation, PenaltyRecord, CirculationLog


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'role', 'student_id', 'is_staff')
    list_filter = ('role', 'is_staff', 'is_active')
    fieldsets = UserAdmin.fieldsets + (
        ('Campus Info', {'fields': ('role', 'student_id')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Campus Info', {'fields': ('role', 'student_id')}),
    )


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'workflow_type', 'station_location', 'created_at')
    list_filter = ('workflow_type',)
    search_fields = ('name', 'station_location')


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'total_quantity', 'is_active')
    list_filter = ('category', 'is_active')
    search_fields = ('name',)


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ('user', 'item', 'quantity', 'start_time', 'end_time', 'status')
    list_filter = ('status',)
    search_fields = ('user__username', 'item__name')


@admin.register(PenaltyRecord)
class PenaltyRecordAdmin(admin.ModelAdmin):
    list_display = ('user', 'reservation', 'amount', 'is_paid', 'created_at')
    list_filter = ('is_paid',)


@admin.register(CirculationLog)
class CirculationLogAdmin(admin.ModelAdmin):
    list_display = ('action_type', 'reservation', 'actor', 'timestamp')
    list_filter = ('action_type', 'timestamp')
    search_fields = ('reservation__item__name', 'actor__username', 'reservation__user__username')
    readonly_fields = ('reservation', 'action_type', 'actor', 'timestamp', 'note')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
