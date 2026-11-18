from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.signing import Signer, BadSignature
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
    class WorkflowType(models.TextChoices):
        STUDIO = 'STUDIO', _('Media & Studio Inspection Workflow')
        AUDIO = 'AUDIO', _('Audio Field Gear Protocol')
        LAB = 'LAB', _('Precision Lab & Calibration Workflow')
        COMPUTING = 'COMPUTING', _('Spatial VR & Mobile Computing Protocol')

    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    workflow_type = models.CharField(
        max_length=20,
        choices=WorkflowType.choices,
        default=WorkflowType.STUDIO,
    )
    station_location = models.CharField(max_length=100, default='Media Lab Station 01')
    inspection_checklist = models.TextField(
        blank=True,
        default='1. Optical casing integrity\n2. Power & sensor cycle test\n3. Accessories & cabling audit'
    )
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
        REJECTED = 'REJECTED', _('Rejected')
        CANCELLED = 'CANCELLED', _('Cancelled')

    class Purpose(models.TextChoices):
        COURSEWORK = 'coursework', _('Academic Coursework')
        RESEARCH = 'research', _('Faculty Research')
        CLUB = 'club', _('Student Club Project')

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
    purpose = models.CharField(
        max_length=32,
        choices=Purpose.choices,
        default=Purpose.COURSEWORK,
    )
    notes = models.TextField(blank=True, default='')
    cancellation_reason = models.CharField(max_length=255, blank=True, null=True)
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

        # Cap booking lead time to 60 days
        if self._state.adding and self.start_time > (timezone.now() + timedelta(days=60)):
            raise ValidationError({"start_time": _("Reservations cannot be booked more than 60 days in advance.")})

        # Max continuous borrowing duration: 14 days
        if (self.end_time - self.start_time).total_seconds() > 14 * 86400:
            raise ValidationError({"end_time": _("Reservation duration cannot exceed 14 consecutive days.")})

        if self.quantity <= 0:
            raise ValidationError({"quantity": _("Reservation quantity must be at least 1.")})

        if self.quantity > self.item.total_quantity:
            raise ValidationError({"quantity": _("Requested quantity exceeds total item inventory.")})

        # Conflict check applies to capacity-holding statuses:
        # PENDING requests reserve capacity immediately to prevent double-booking.
        # APPROVED, ACTIVE, and OVERDUE also consume physical inventory.
        if self.status not in (
            self.Status.PENDING, self.Status.APPROVED, self.Status.ACTIVE,
        ):
            return

        # Conflict prevention: overlapping intervals [s1, e1) and [s2, e2) intersect
        # iff s1 < e2 and s2 < e1 (Allen's Interval Algebra).
        overlapping_reservations = Reservation.objects.filter(
            item=self.item,
            start_time__lt=self.end_time,
            end_time__gt=self.start_time,
            status__in=[
                self.Status.APPROVED,
                self.Status.ACTIVE,
                self.Status.OVERDUE,
            ],
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

    def generate_signed_pass_token(self) -> str:
        """Cryptographically signed HMAC token for QR pass verification."""
        signer = Signer(sep=':')
        payload = f"{self.id}:{self.user_id}:{self.item_id}:{self.status}:{int(self.end_time.timestamp())}"
        return signer.sign(payload)

    @classmethod
    def verify_signed_pass_token(cls, token: str) -> dict:
        signer = Signer(sep=':')
        try:
            original = signer.unsign(token)
            res_id, user_id, item_id, status, expiry = original.split(':')
            return {
                "valid": True,
                "reservation_id": int(res_id),
                "user_id": int(user_id),
                "item_id": int(item_id),
                "status": status,
                "expired": timezone.now().timestamp() > int(expiry),
            }
        except (BadSignature, ValueError):
            return {"valid": False}

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
        Reservation, on_delete=models.PROTECT, related_name='circulation_logs'
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


class SystemLog(models.Model):
    class Severity(models.TextChoices):
        INFO = 'INFO', _('Info')
        WARNING = 'WARNING', _('Warning')
        CRITICAL = 'CRITICAL', _('Critical')

    class EventType(models.TextChoices):
        TX_COMMIT = 'TX_COMMIT', _('Transaction Commit')
        INTERVAL_CONFLICT = 'INTERVAL_CONFLICT', _('Interval Conflict Blocked')
        ROW_LOCK = 'ROW_LOCK', _('Row Lock Acquired')
        CAPACITY_UPDATE = 'CAPACITY_UPDATE', _('Live Capacity Update')
        AUTH_EVENT = 'AUTH_EVENT', _('Authentication Event')
        SCHEMA_AUDIT = 'SCHEMA_AUDIT', _('3NF Schema Audit')
        INTEGRITY_CHECK = 'INTEGRITY_CHECK', _('SQLite Integrity Check')

    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    event_type = models.CharField(max_length=30, choices=EventType.choices, db_index=True)
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.INFO, db_index=True)
    module = models.CharField(max_length=100, default='backend.core')
    message = models.TextField()
    execution_time_ms = models.FloatField(null=True, blank=True)
    metadata = models.TextField(blank=True, default='')

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'System Log'
        verbose_name_plural = 'System Logs'

    def __str__(self):
        return f"[{self.severity}] {self.event_type} @ {self.timestamp:%H:%M:%S}: {self.message[:60]}"


def log_system_event(event_type, message, severity=SystemLog.Severity.INFO, module='backend.core', execution_time_ms=None, metadata=''):
    try:
        return SystemLog.objects.create(
            event_type=event_type,
            message=message,
            severity=severity,
            module=module,
            execution_time_ms=execution_time_ms,
            metadata=str(metadata),
        )
    except Exception:
        return None
