from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class CustomUser(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'ADMIN', _('Admin')
        MANAGER = 'MANAGER', _('Manager')
        STUDENT = 'STUDENT', _('Student')

    role = models.CharField(
        max_length=10,
        choices=Role.choices,
        default=Role.STUDENT,
        db_index=True,
    )
    student_id = models.CharField(max_length=20, unique=True, null=True, blank=True)

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = 'Categories'

    def __str__(self):
        return self.name


class Item(models.Model):
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name='items')
    name = models.CharField(max_length=200, db_index=True)
    description = models.TextField(blank=True)
    total_quantity = models.PositiveIntegerField(default=1)
    image_url = models.URLField(max_length=500, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Reservation(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', _('Pending')
        APPROVED = 'APPROVED', _('Approved')
        ACTIVE = 'ACTIVE', _('Active')
        RETURNED = 'RETURNED', _('Returned')
        OVERDUE = 'OVERDUE', _('Overdue')

    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='reservations')
    item = models.ForeignKey(Item, on_delete=models.PROTECT, related_name='reservations')
    quantity = models.PositiveIntegerField(default=1)
    start_time = models.DateTimeField(db_index=True)
    end_time = models.DateTimeField(db_index=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['item', 'start_time', 'end_time']),
            models.Index(fields=['status', 'end_time']),
        ]

    def clean(self):
        super().clean()

        if not self.start_time or not self.end_time:
            return

        if self.start_time >= self.end_time:
            raise ValidationError({"end_time": _("End time must be strictly after start time.")})

        # Allow a 5-minute clock drift / latency buffer for newly created reservations
        if self._state.adding and self.start_time < (timezone.now() - timedelta(minutes=5)):
            raise ValidationError({"start_time": _("Reservation start time cannot be in the past.")})

        # Max continuous borrowing duration: 14 days
        if (self.end_time - self.start_time).total_seconds() > 14 * 86400:
            raise ValidationError({"end_time": _("Reservation duration cannot exceed 14 consecutive days.")})

        if self.quantity <= 0:
            raise ValidationError({"quantity": _("Reservation quantity must be at least 1.")})

        if self.quantity > self.item.total_quantity:
            raise ValidationError({"quantity": _("Requested quantity exceeds total item inventory.")})

        # Conflict prevention: overlapping intervals [s1, e1) and [s2, e2) intersect iff s1 < e2 and e1 > s2
        overlapping_reservations = Reservation.objects.filter(
            item=self.item,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
            status__in=[self.Status.APPROVED, self.Status.ACTIVE],
        )

        if self.pk:
            overlapping_reservations = overlapping_reservations.exclude(pk=self.pk)

        consumed_quantity = overlapping_reservations.aggregate(
            total_consumed=Sum('quantity')
        )['total_consumed'] or 0

        if (consumed_quantity + self.quantity) > self.item.total_quantity:
            available = max(0, self.item.total_quantity - consumed_quantity)
            raise ValidationError(
                _("Inventory conflict: Only %(available)s items available during this time period.") % {
                    'available': available
                }
            )

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.user.username} - {self.item.name} ({self.status})"


class PenaltyRecord(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='penalties')
    reservation = models.OneToOneField(Reservation, on_delete=models.CASCADE, related_name='penalty')
    amount = models.DecimalField(max_digits=6, decimal_places=2)
    reason = models.TextField()
    is_paid = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    def clean(self):
        super().clean()
        if self.amount < 0:
            raise ValidationError({"amount": _("Penalty amount cannot be negative.")})
        if self.is_paid and not self.resolved_at:
            self.resolved_at = timezone.now()
        elif not self.is_paid:
            self.resolved_at = None

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Penalty: {self.user.username} - ${self.amount} (Paid: {self.is_paid})"


class CirculationLog(models.Model):
    class Action(models.TextChoices):
        APPROVED = 'APPROVED', _('Approved')
        CHECKED_OUT = 'CHECKED_OUT', _('Checked Out')
        RETURNED = 'RETURNED', _('Returned')
        REJECTED = 'REJECTED', _('Rejected')

    reservation = models.ForeignKey(
        Reservation, on_delete=models.CASCADE, related_name='circulation_logs'
    )
    actor = models.ForeignKey(
        CustomUser, on_delete=models.SET_NULL, null=True, blank=True, related_name='actions_logged'
    )
    action_type = models.CharField(
        max_length=20, choices=Action.choices, db_index=True
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Circulation Log'
        verbose_name_plural = 'Circulation Logs'

    def __str__(self):
        return f"{self.get_action_type_display()} - Res #{self.reservation_id} by {self.actor or 'System'}"
