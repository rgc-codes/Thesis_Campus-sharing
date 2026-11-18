import csv
import os
import platform
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import OperationalError, connection, transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import (
    Category, CirculationLog, CustomUser, Item, PenaltyRecord, Reservation,
    SystemLog, log_system_event,
)
from .qr_utils import generate_qr_base64

# Hourly penalty rate for overdue returns (USD)
OVERDUE_PENALTY_RATE = Decimal("5.00")


# ---------------------------------------------------------------------------
# Decorators & Navigation Dispatch
# ---------------------------------------------------------------------------

def role_required(*allowed_roles):
    """Restrict view access to users whose *role* is in `allowed_roles`."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"{settings.LOGIN_URL}?next={request.path}")
            if request.user.role not in allowed_roles:
                return HttpResponseForbidden("Permission denied: insufficient role privileges.")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


@login_required
def login_dispatch_view(request):
    """Direct users to their dedicated dashboard based on role."""
    log_system_event(
        SystemLog.EventType.AUTH_EVENT,
        f"User '{request.user.username}' ({request.user.get_role_display()}) signed in successfully.",
        severity=SystemLog.Severity.INFO,
        module='core.auth'
    )
    if request.user.role == CustomUser.Role.ADMIN:
        return redirect('admin_system_dashboard')
    elif request.user.role == CustomUser.Role.MANAGER:
        return redirect('manager_dashboard')
    return redirect('catalog')


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Statuses that physically occupy inventory or reserve capacity in 3NF
_CAPACITY_HOLDING = [
    Reservation.Status.PENDING,
    Reservation.Status.APPROVED,
    Reservation.Status.ACTIVE,
    Reservation.Status.OVERDUE,
]


def _mark_overdue_reservations():
    """Transition ACTIVE reservations past their deadline to OVERDUE."""
    count = Reservation.objects.filter(
        status=Reservation.Status.ACTIVE,
        end_time__lt=timezone.now(),
    ).update(status=Reservation.Status.OVERDUE)
    if count > 0:
        log_system_event(
            SystemLog.EventType.CAPACITY_UPDATE,
            f"FSM transition: {count} reservation(s) marked as OVERDUE.",
            severity=SystemLog.Severity.WARNING,
            module='core.fsm'
        )
    return count


def _get_available_quantity(item, exclude_pk=None):
    """Calculate instantaneous unallocated stock for display in 3NF.
    Checks all capacity-holding reservations that are active or unreturned.
    """
    now = timezone.now()
    active_qs = Reservation.objects.filter(
        item=item,
        status__in=_CAPACITY_HOLDING,
        end_time__gt=now,
    )
    if exclude_pk:
        active_qs = active_qs.exclude(pk=exclude_pk)
    allocated = active_qs.aggregate(total=Sum('quantity'))['total'] or 0
    return max(0, item.total_quantity - allocated)


def _sanitize_csv_cell(value):
    """Prefix cells that begin with formula-trigger characters to prevent injection."""
    s = str(value)
    if s and s[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + s
    return s


# ---------------------------------------------------------------------------
# Student & Public Views
# ---------------------------------------------------------------------------

def catalog_view(request):
    """Public equipment catalog with real-time 3NF availability.
    Manager and Admin are strictly routed away from student-facing catalog.
    """
    _mark_overdue_reservations()

    if request.user.is_authenticated:
        if request.user.role == CustomUser.Role.MANAGER:
            return redirect('manager_dashboard')
        elif request.user.role == CustomUser.Role.ADMIN:
            return redirect('admin_system_dashboard')

    categories = Category.objects.all()
    items_qs = Item.objects.filter(is_active=True).select_related('category')

    query = request.GET.get('q', '').strip()
    if query:
        items_qs = items_qs.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )

    category_id = request.GET.get('category', '').strip()
    if category_id and category_id.isdigit():
        items_qs = items_qs.filter(category_id=int(category_id))

    items = list(items_qs)
    for item in items:
        item.available_quantity = _get_available_quantity(item)

    if request.GET.get('in_stock'):
        items = [item for item in items if item.available_quantity > 0]

    context = {
        'items': items,
        'categories': categories,
        'total_items_count': Item.objects.filter(is_active=True).count(),
    }
    return render(request, 'catalog.html', context)


@login_required
def reserve_item_view(request, item_id):
    """Student booking form. Validates temporal constraints, capacity, and bounds."""
    if request.user.role in (CustomUser.Role.MANAGER, CustomUser.Role.ADMIN):
        return redirect('manager_dashboard' if request.user.role == CustomUser.Role.MANAGER else 'admin_system_dashboard')

    item = get_object_or_404(Item, pk=item_id, is_active=True)
    now = timezone.now()
    max_lead_time = now + timedelta(days=60)
    available_qty = _get_available_quantity(item)

    context = {
        'item': item,
        'now': now,
        'max_lead_time': max_lead_time,
        'available_quantity': available_qty,
    }

    if request.method == 'POST':
        start_raw = request.POST.get('start_time', '').strip()
        end_raw = request.POST.get('end_time', '').strip()
        qty_raw = request.POST.get('quantity', '1').strip()
        purpose = request.POST.get('purpose', Reservation.Purpose.COURSEWORK).strip()
        notes = request.POST.get('notes', '').strip()

        if purpose not in Reservation.Purpose.values:
            purpose = Reservation.Purpose.COURSEWORK

        try:
            quantity = int(qty_raw)
            if quantity < 1:
                quantity = 1
        except (ValueError, TypeError):
            quantity = 1

        if not start_raw or not end_raw:
            messages.error(request, "Both pickup and return times are required.")
            return render(request, 'reserve_item.html', context)

        try:
            start_time = timezone.make_aware(
                datetime.strptime(start_raw, '%Y-%m-%dT%H:%M'),
                timezone.get_current_timezone(),
            )
            end_time = timezone.make_aware(
                datetime.strptime(end_raw, '%Y-%m-%dT%H:%M'),
                timezone.get_current_timezone(),
            )
        except (ValueError, TypeError):
            messages.error(request, "Invalid timestamp format provided.")
            return render(request, 'reserve_item.html', context)

        # Duplicate submission guard
        duplicate = Reservation.objects.filter(
            user=request.user,
            item=item,
            status__in=[Reservation.Status.PENDING, Reservation.Status.APPROVED],
            start_time=start_time,
            end_time=end_time,
        ).exists()
        if duplicate:
            messages.warning(request, "You already have an identical pending or active booking for this item and time.")
            return redirect('student_dashboard')

        try:
            with transaction.atomic():
                t0 = timezone.now()
                locked_item = Item.objects.select_for_update().get(pk=item.pk)

                # Check instantaneous unallocated stock
                available_now = _get_available_quantity(locked_item)
                if quantity > available_now:
                    raise ValidationError(
                        f"Inventory conflict: Only {available_now} items available during this time period."
                    )

                reservation = Reservation(
                    user=request.user,
                    item=locked_item,
                    quantity=quantity,
                    start_time=start_time,
                    end_time=end_time,
                    status=Reservation.Status.PENDING,
                    purpose=purpose,
                    notes=notes,
                )
                reservation.save()

                messages.success(
                    request,
                    f"🎉 Booking Request Submitted! {quantity} unit(s) reserved. Awaiting collection check-in.",
                )
                elapsed_ms = (timezone.now() - t0).total_seconds() * 1000
                log_system_event(
                    SystemLog.EventType.TX_COMMIT,
                    f"Reservation #{reservation.id} confirmed for '{request.user.username}': {quantity}x {locked_item.name}.",
                    severity=SystemLog.Severity.INFO,
                    module='core.views.reserve_item',
                    execution_time_ms=elapsed_ms,
                    metadata=f"start={start_time.isoformat()}, end={end_time.isoformat()}, item_id={locked_item.id}"
                )
                remaining = _get_available_quantity(locked_item)
                log_system_event(
                    SystemLog.EventType.CAPACITY_UPDATE,
                    f"Live inventory updated for '{locked_item.name}': {remaining} unit(s) remaining (Total: {locked_item.total_quantity}).",
                    severity=SystemLog.Severity.INFO,
                    module='core.capacity_engine',
                    metadata=f"remaining={remaining}"
                )

                messages.success(
                    request,
                    f"🎉 Booking Confirmed! {quantity} unit(s) of {locked_item.name} reserved. Digital pass active.",
                )
                return redirect('student_dashboard')
        except ValidationError as exc:
            log_system_event(
                SystemLog.EventType.INTERVAL_CONFLICT,
                f"Collision blocked for '{request.user.username}' on '{item.name}': {exc}",
                severity=SystemLog.Severity.WARNING,
                module='core.interval_algebra',
                metadata=f"user={request.user.username}, item_id={item.id}"
            )
            if hasattr(exc, 'message_dict'):
                for errors in exc.message_dict.values():
                    for err in errors:
                        messages.error(request, err)
            else:
                for err in exc.messages:
                    messages.error(request, err)
        except OperationalError:
            messages.error(
                request,
                "The system is busy processing another request. Please try again.",
            )

    return render(request, 'reserve_item.html', context)


@login_required
def student_dashboard_view(request):
    """Student command center with active loans, countdowns, and QR pass."""
    if request.user.role in (CustomUser.Role.MANAGER, CustomUser.Role.ADMIN):
        return redirect('manager_dashboard' if request.user.role == CustomUser.Role.MANAGER else 'admin_system_dashboard')

    now = timezone.now()
    _mark_overdue_reservations()

    user = request.user

    active_reservations = (
        Reservation.objects.filter(
            user=user,
            status__in=[Reservation.Status.ACTIVE, Reservation.Status.OVERDUE],
        )
        .select_related('item', 'item__category')
        .order_by('end_time')
    )
    upcoming_reservations = (
        Reservation.objects.filter(
            user=user,
            status__in=[Reservation.Status.PENDING, Reservation.Status.APPROVED],
        )
        .select_related('item', 'item__category')
        .order_by('start_time')
    )

    pass_reservation = (
        Reservation.objects.filter(
            user=user,
            status__in=[Reservation.Status.APPROVED, Reservation.Status.ACTIVE],
        )
        .select_related('item', 'item__category')
        .order_by('start_time')
        .first()
    )

    if pass_reservation:
        next_deadline = pass_reservation.end_time
    elif active_reservations.exists():
        next_deadline = active_reservations.first().end_time
    else:
        next_deadline = None

    active_data = []
    for res in active_reservations:
        span = (res.end_time - res.start_time).total_seconds()
        elapsed = (now - res.start_time).total_seconds()
        progress = max(0, min(100, int((elapsed / span) * 100))) if span > 0 else 100
        remaining_hours = max(0, int((res.end_time - now).total_seconds() / 3600))

        active_data.append({
            'reservation': res,
            'progress': progress,
            'remaining_hours': remaining_hours,
            'is_overdue': res.status == Reservation.Status.OVERDUE,
        })

    qr_code_base64 = None
    if pass_reservation:
        token = pass_reservation.generate_signed_pass_token()
        qr_code_base64 = generate_qr_base64(token)

    past_reservations = (
        Reservation.objects.filter(
            user=user,
            status__in=[
                Reservation.Status.RETURNED,
                Reservation.Status.REJECTED,
                Reservation.Status.CANCELLED,
            ],
        )
        .select_related('item', 'item__category')
        .order_by('-updated_at')[:20]
    )

    returned_count = Reservation.objects.filter(
        user=user, status=Reservation.Status.RETURNED,
    ).count()
    overdue_penalties = Reservation.objects.filter(
        user=user, status=Reservation.Status.OVERDUE,
    ).count()
    trust_score = max(0, 100 - (overdue_penalties * 5))

    context = {
        'active_data': active_data,
        'upcoming_reservations': upcoming_reservations,
        'past_reservations': past_reservations,
        'active_count': active_reservations.count(),
        'overdue_count': active_reservations.filter(
            status=Reservation.Status.OVERDUE,
        ).count(),
        'next_deadline': next_deadline,
        'pass_reservation': pass_reservation,
        'qr_code_base64': qr_code_base64,
        'trust_score': trust_score,
        'lifetime_loans': returned_count,
    }
    return render(request, 'student_dashboard.html', context)


@login_required
def cancel_reservation_view(request, pk):
    """Allow students to cancel their own PENDING or APPROVED reservations."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    reason = request.POST.get('reason', '').strip()

    reservation = get_object_or_404(
        Reservation,
        pk=pk,
        user=request.user,
        status__in=[Reservation.Status.PENDING, Reservation.Status.APPROVED],
    )
    reservation.status = Reservation.Status.CANCELLED
    if reason:
        reservation.cancellation_reason = reason
    reservation.save()

    log_system_event(
        SystemLog.EventType.TX_COMMIT,
        f"Reservation #{pk} cancelled by user '{request.user.username}'. Capacity returned to pool.",
        severity=SystemLog.Severity.INFO,
        module='core.views.cancel_reservation'
    )
    messages.info(request, f"Reservation #{pk} has been cancelled.")
    return redirect('student_dashboard')


# ---------------------------------------------------------------------------
# Manager Console Views (Circulation & Desk Operations)
# ---------------------------------------------------------------------------

@login_required
@role_required(CustomUser.Role.MANAGER, CustomUser.Role.ADMIN)
def manager_dashboard_view(request):
    """Lab Manager Operations Hub.
    Dedicated desk console for inventory circulation, approvals, checkouts, and returns.
    """
    _mark_overdue_reservations()

    # Load items with live stock metrics
    items = list(Item.objects.all().select_related('category'))
    for item in items:
        item.available_quantity = _get_available_quantity(item)
        item.active_loans = Reservation.objects.filter(
            item=item, status__in=[Reservation.Status.ACTIVE, Reservation.Status.OVERDUE]
        ).count()
        item.pending_requests = Reservation.objects.filter(
            item=item, status=Reservation.Status.PENDING
        ).count()

    categories = list(Category.objects.all().order_by('id'))
    category_param = request.GET.get('category', '').strip()
    active_category = None
    if category_param.isdigit():
        active_category = Category.objects.filter(pk=int(category_param)).first()
    if not active_category and categories and category_param != 'all':
        active_category = categories[0]

    reservations_qs = Reservation.objects.select_related('user', 'item', 'item__category').order_by('-created_at')
    if active_category:
        reservations_qs = reservations_qs.filter(item__category=active_category)

    checklist_items = [
        item.strip()
        for item in (active_category.inspection_checklist.split('\n') if active_category and active_category.inspection_checklist else [])
        if item.strip()
    ]

    pending_reservations = Reservation.objects.filter(
        status=Reservation.Status.PENDING
    ).select_related('item', 'item__category', 'user').order_by('created_at')

    approved_pickups = Reservation.objects.filter(
        status=Reservation.Status.APPROVED
    ).select_related('item', 'item__category', 'user').order_by('start_time')

    active_loans = Reservation.objects.filter(
        status=Reservation.Status.ACTIVE
    ).select_related('item', 'item__category', 'user').order_by('end_time')

    overdue_loans = Reservation.objects.filter(
        status=Reservation.Status.OVERDUE
    ).select_related('item', 'item__category', 'user').order_by('end_time')

    recent_circulation_logs = CirculationLog.objects.select_related(
        'reservation', 'reservation__item', 'reservation__user', 'actor'
    ).order_by('-timestamp')[:8]

    context = {
        'items': items,
        'categories': categories,
        'active_category': active_category,
        'selected_category_id': str(active_category.id) if active_category else 'all',
        'checklist_items': checklist_items,
        'reservations': reservations_qs,
        'pending_reservations': pending_reservations,
        'approved_pickups': approved_pickups,
        'active_loans': active_loans,
        'overdue_loans': overdue_loans,
        'recent_logs': recent_circulation_logs,
        'pending_count': pending_reservations.count(),
        'approved_count': approved_pickups.count(),
        'active_count': active_loans.count(),
        'overdue_count': overdue_loans.count(),
    }
    return render(request, 'manager_dashboard.html', context)


@login_required
@role_required(CustomUser.Role.ADMIN, CustomUser.Role.MANAGER)
def admin_dashboard_view(request):
    """Router for management dashboard preserving backwards compatibility with test fixtures."""
    if request.user.role == CustomUser.Role.ADMIN and request.GET.get('category') is None:
        return admin_system_dashboard_view(request)
    return manager_dashboard_view(request)


@login_required
@role_required(CustomUser.Role.MANAGER, CustomUser.Role.ADMIN)
def approve_reservation_view(request, pk):
    """Approve a pending reservation (PENDING → APPROVED)."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        with transaction.atomic():
            reservation = get_object_or_404(
                Reservation.objects.select_for_update().select_related('item', 'user'),
                pk=pk,
                status=Reservation.Status.PENDING,
            )
            if reservation.end_time <= timezone.now():
                messages.error(
                    request,
                    f"Cannot approve reservation #{pk}: the requested booking period has already expired.",
                )
                return redirect('manager_dashboard')

            Item.objects.select_for_update().get(pk=reservation.item_id)
            reservation.status = Reservation.Status.APPROVED
            reservation.save()

            actor_name = request.user.get_full_name() or request.user.username
            CirculationLog.objects.create(
                reservation=reservation,
                actor=request.user,
                action_type=CirculationLog.Action.APPROVED,
                note=f"Reservation #{pk} approved for pickup by {actor_name}",
            )
            log_system_event(
                SystemLog.EventType.TX_COMMIT,
                f"Reservation #{pk} approved by manager '{request.user.username}'.",
                severity=SystemLog.Severity.INFO,
                module='core.circulation'
            )
    except ValidationError as exc:
        messages.error(request, f"Cannot approve reservation #{pk}: {exc}")
        return redirect('manager_dashboard')
    except OperationalError:
        messages.error(request, "Database busy — please retry.")
        return redirect('manager_dashboard')

    messages.success(request, f"✅ Reservation #{pk} approved. QR collection pass is ready.")
    return redirect('manager_dashboard')


@login_required
@role_required(CustomUser.Role.MANAGER, CustomUser.Role.ADMIN)
def checkout_reservation_view(request, pk):
    """Issue physical equipment to the student (APPROVED → ACTIVE)."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        with transaction.atomic():
            reservation = get_object_or_404(
                Reservation.objects.select_for_update().select_related('item', 'user'),
                pk=pk,
                status=Reservation.Status.APPROVED,
            )
            if reservation.end_time <= timezone.now():
                messages.error(
                    request,
                    f"Cannot issue gear for reservation #{pk}: scheduled window expired.",
                )
                return redirect('manager_dashboard')

            reservation.status = Reservation.Status.ACTIVE
            reservation.save()

            borrower_name = reservation.user.get_full_name() or reservation.user.username
            borrower_id = reservation.user.student_id or 'N/A'
            CirculationLog.objects.create(
                reservation=reservation,
                actor=request.user,
                action_type=CirculationLog.Action.CHECKED_OUT,
                note=f"Equipment issued to {borrower_name} (#{borrower_id})",
            )
            log_system_event(
                SystemLog.EventType.TX_COMMIT,
                f"Checkout completed: Reservation #{pk} ({reservation.item.name}) handed to {borrower_name}.",
                severity=SystemLog.Severity.INFO,
                module='core.circulation'
            )
    except OperationalError:
        messages.error(request, "Database busy — please retry checkout.")
        return redirect('manager_dashboard')

    messages.success(request, "🚀 Equipment checked out! Circulation timer started.")
    return redirect('manager_dashboard')


@login_required
@role_required(CustomUser.Role.MANAGER, CustomUser.Role.ADMIN)
def reject_reservation_view(request, pk):
    """Reject a pending reservation with reason."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    reason = request.POST.get('reason', '').strip()

    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects.select_for_update().select_related('item'),
            pk=pk,
            status=Reservation.Status.PENDING,
        )
        reservation.status = Reservation.Status.REJECTED
        if reason:
            reservation.cancellation_reason = reason
        reservation.save()

        note = f"Reservation #{pk} rejected by {request.user.get_full_name() or request.user.username}"
        if reason:
            note += f" (Reason: {reason})"

        CirculationLog.objects.create(
            reservation=reservation,
            actor=request.user,
            action_type=CirculationLog.Action.REJECTED,
            note=note,
        )
        log_system_event(
            SystemLog.EventType.TX_COMMIT,
            f"Reservation #{pk} rejected by manager '{request.user.username}'. Reason: {reason or 'None'}",
            severity=SystemLog.Severity.INFO,
            module='core.circulation'
        )

    messages.info(request, f"Reservation #{pk} has been rejected.")
    return redirect('manager_dashboard')


@login_required
@role_required(CustomUser.Role.MANAGER, CustomUser.Role.ADMIN)
def return_item_view(request, pk):
    """Process physical return and inspect equipment."""
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects.select_for_update().select_related('item', 'user'),
            pk=pk,
            status__in=[Reservation.Status.ACTIVE, Reservation.Status.OVERDUE],
        )

        was_overdue = (
            reservation.status == Reservation.Status.OVERDUE
            or timezone.now() > reservation.end_time
        )
        reservation.status = Reservation.Status.RETURNED
        reservation.save()

        actor_name = request.user.get_full_name() or request.user.username
        CirculationLog.objects.create(
            reservation=reservation,
            actor=request.user,
            action_type=CirculationLog.Action.RETURNED,
            note=f"Equipment inspected and returned by {actor_name}",
        )

        if was_overdue:
            overdue_seconds = (timezone.now() - reservation.end_time).total_seconds()
            overdue_hours = max(1, int(overdue_seconds / 3600))
            penalty = PenaltyRecord.objects.create(
                user=reservation.user,
                reservation=reservation,
                amount=OVERDUE_PENALTY_RATE * overdue_hours,
                reason=f"Late return: {overdue_hours} hour(s) past scheduled deadline",
            )
            log_system_event(
                SystemLog.EventType.CAPACITY_UPDATE,
                f"Late return on Res #{pk}. Penalty fine of ${penalty.amount} assessed to student '{reservation.user.username}'.",
                severity=SystemLog.Severity.WARNING,
                module='core.penalties'
            )

        log_system_event(
            SystemLog.EventType.TX_COMMIT,
            f"Return processed for Res #{pk}. Equipment '{reservation.item.name}' returned to available stock.",
            severity=SystemLog.Severity.INFO,
            module='core.circulation'
        )

    if was_overdue:
        messages.warning(request, "📦 Equipment returned late — fine has been logged.")
    else:
        messages.success(request, "📦 Equipment returned safely. Inventory restored.")
    return redirect('manager_dashboard')


@login_required
@role_required(CustomUser.Role.MANAGER, CustomUser.Role.ADMIN)
def circulation_log_view(request):
    """Manager Circulation Audit Ledger with CSV export."""
    logs = CirculationLog.objects.select_related(
        'reservation',
        'reservation__item',
        'reservation__item__category',
        'reservation__user',
        'actor',
    ).order_by('-timestamp')

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        timestamp_slug = timezone.now().strftime("%Y%m%d_%H%M%S")
        response['Content-Disposition'] = (
            f'attachment; filename="circulation_audit_log_{timestamp_slug}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow([
            'Log ID', 'Timestamp', 'Action Type', 'Item Name', 'Category',
            'Student', 'Student ID', 'Lab Officer', 'Notes',
        ])
        for log in logs:
            actor = log.actor
            actor_name = (
                (actor.get_full_name() or actor.username) if actor else 'System'
            )
            writer.writerow([
                _sanitize_csv_cell(log.id),
                _sanitize_csv_cell(log.timestamp.strftime('%Y-%m-%d %H:%M:%S')),
                _sanitize_csv_cell(log.get_action_type_display()),
                _sanitize_csv_cell(log.reservation.item.name),
                _sanitize_csv_cell(log.reservation.item.category.name),
                _sanitize_csv_cell(
                    log.reservation.user.get_full_name()
                    or log.reservation.user.username
                ),
                _sanitize_csv_cell(log.reservation.user.student_id or 'N/A'),
                _sanitize_csv_cell(actor_name),
                _sanitize_csv_cell(log.note),
            ])
        return response

    paginator = Paginator(logs, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'logs': page_obj,
        'page_obj': page_obj,
        'total_logs_count': logs.count(),
        'approved_count': logs.filter(action_type=CirculationLog.Action.APPROVED).count(),
        'checkout_count': logs.filter(action_type=CirculationLog.Action.CHECKED_OUT).count(),
        'returned_count': logs.filter(action_type=CirculationLog.Action.RETURNED).count(),
        'rejected_count': logs.filter(action_type=CirculationLog.Action.REJECTED).count(),
    }
    return render(request, 'circulation_log.html', context)


# ---------------------------------------------------------------------------
# Admin System & Backend Architecture Views
# ---------------------------------------------------------------------------

@login_required
@role_required(CustomUser.Role.ADMIN)
def admin_system_dashboard_view(request):
    """Admin System Information Dashboard.
    Exposes live backend engine state, SQLite WAL metrics, 3NF schema telemetry,
    and interval conflict prevention logs.
    """
    db_path = settings.DATABASES['default']['NAME']
    db_size_bytes = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    # Query SQLite engine PRAGMAs
    wal_mode = 'UNKNOWN'
    sync_mode = 'UNKNOWN'
    busy_timeout = 0
    page_count = 0
    page_size = 0
    integrity_status = 'UNKNOWN'

    if connection.vendor == 'sqlite':
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA journal_mode;")
            wal_mode = str(cursor.fetchone()[0]).upper()
            cursor.execute("PRAGMA synchronous;")
            sync_mode = str(cursor.fetchone()[0])
            cursor.execute("PRAGMA busy_timeout;")
            busy_timeout = cursor.fetchone()[0]
            cursor.execute("PRAGMA page_count;")
            page_count = cursor.fetchone()[0]
            cursor.execute("PRAGMA page_size;")
            page_size = cursor.fetchone()[0]
            cursor.execute("PRAGMA integrity_check;")
            integrity_status = str(cursor.fetchone()[0])

    # Table Metrics
    table_stats = [
        {
            'name': 'core_customuser',
            'label': 'Users (RBAC Partition)',
            'count': CustomUser.objects.count(),
            'breakdown': f"Students: {CustomUser.objects.filter(role=CustomUser.Role.STUDENT).count()}, Managers: {CustomUser.objects.filter(role=CustomUser.Role.MANAGER).count()}, Admins: {CustomUser.objects.filter(role=CustomUser.Role.ADMIN).count()}",
            'status': 'Normalized (3NF)'
        },
        {
            'name': 'core_category',
            'label': 'Categories (Workflow Hubs)',
            'count': Category.objects.count(),
            'breakdown': '3 Dedicated Workflows (Studio, Lab, Spatial Computing)',
            'status': 'Normalized (3NF)'
        },
        {
            'name': 'core_item',
            'label': 'Equipment (Physical Inventory)',
            'count': Item.objects.count(),
            'breakdown': f"Total Units: {Item.objects.aggregate(total=Sum('total_quantity'))['total'] or 0} units across 3 assets",
            'status': 'Normalized (3NF) - Zero Derived Counters'
        },
        {
            'name': 'core_reservation',
            'label': 'Reservations (Temporal Intervals)',
            'count': Reservation.objects.count(),
            'breakdown': f"Active: {Reservation.objects.filter(status=Reservation.Status.ACTIVE).count()}, Approved: {Reservation.objects.filter(status=Reservation.Status.APPROVED).count()}, Pending: {Reservation.objects.filter(status=Reservation.Status.PENDING).count()}",
            'status': 'Composite B-Tree Indexed'
        },
        {
            'name': 'core_circulationlog',
            'label': 'Circulation Logs (Manager Ledger)',
            'count': CirculationLog.objects.count(),
            'breakdown': 'Physical custody transitions (PROTECT durability)',
            'status': 'Append-Only'
        },
        {
            'name': 'core_systemlog',
            'label': 'System Logs (Backend Telemetry)',
            'count': SystemLog.objects.count(),
            'breakdown': 'Transactions, Concurrency, and Conflict events',
            'status': 'Immutable Telemetry'
        },
        {
            'name': 'core_penaltyrecord',
            'label': 'Penalty Records (Late Fines)',
            'count': PenaltyRecord.objects.count(),
            'breakdown': f"Overdue fines accrued @ ${OVERDUE_PENALTY_RATE}/hr",
            'status': 'Audit Linked'
        },
    ]

    # Live equipment availability telemetry
    live_items = []
    for it in Item.objects.all().select_related('category'):
        avail = _get_available_quantity(it)
        live_items.append({
            'name': it.name,
            'category': it.category.name,
            'total_quantity': it.total_quantity,
            'available_quantity': avail,
            'allocated_quantity': it.total_quantity - avail,
            'is_available': avail > 0,
        })

    # Recent Backend System Logs
    recent_system_logs = SystemLog.objects.all().order_by('-timestamp')[:20]

    # Conflict prevention counters
    total_conflicts_blocked = SystemLog.objects.filter(
        event_type=SystemLog.EventType.INTERVAL_CONFLICT
    ).count()
    total_commits = SystemLog.objects.filter(
        event_type=SystemLog.EventType.TX_COMMIT
    ).count()

    context = {
        'wal_mode': wal_mode,
        'sync_mode': sync_mode,
        'busy_timeout': busy_timeout,
        'page_count': page_count,
        'page_size': page_size,
        'integrity_status': integrity_status,
        'db_size_kb': round(db_size_bytes / 1024, 2),
        'db_path': str(db_path),
        'python_version': platform.python_version(),
        'django_version': settings.DJANGO_VERSION if hasattr(settings, 'DJANGO_VERSION') else '5.x',
        'os_info': f"{platform.system()} {platform.release()} ({platform.machine()})",
        'pid': os.getpid(),
        'server_time': timezone.now(),
        'table_stats': table_stats,
        'live_items': live_items,
        'recent_system_logs': recent_system_logs,
        'total_conflicts_blocked': total_conflicts_blocked,
        'total_commits': total_commits,
    }
    return render(request, 'admin_system_dashboard.html', context)


@login_required
@role_required(CustomUser.Role.ADMIN)
def admin_system_logs_view(request):
    """Admin-only Backend System Operations Log with CSV export."""
    logs_qs = SystemLog.objects.all().order_by('-timestamp')

    event_type = request.GET.get('event_type', '').strip()
    if event_type:
        logs_qs = logs_qs.filter(event_type=event_type)

    severity = request.GET.get('severity', '').strip()
    if severity:
        logs_qs = logs_qs.filter(severity=severity)

    query = request.GET.get('q', '').strip()
    if query:
        logs_qs = logs_qs.filter(Q(message__icontains=query) | Q(metadata__icontains=query))

    if request.GET.get('export') == 'csv':
        response = HttpResponse(content_type='text/csv')
        timestamp_slug = timezone.now().strftime("%Y%m%d_%H%M%S")
        response['Content-Disposition'] = (
            f'attachment; filename="backend_system_logs_{timestamp_slug}.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(['ID', 'Timestamp', 'Event Type', 'Severity', 'Module', 'Execution Time (ms)', 'Message', 'Metadata'])
        for item in logs_qs:
            writer.writerow([
                _sanitize_csv_cell(item.id),
                _sanitize_csv_cell(item.timestamp.strftime('%Y-%m-%d %H:%M:%S')),
                _sanitize_csv_cell(item.event_type),
                _sanitize_csv_cell(item.severity),
                _sanitize_csv_cell(item.module),
                _sanitize_csv_cell(item.execution_time_ms or 0),
                _sanitize_csv_cell(item.message),
                _sanitize_csv_cell(item.metadata),
            ])
        return response

    paginator = Paginator(logs_qs, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    context = {
        'logs': page_obj,
        'page_obj': page_obj,
        'total_count': logs_qs.count(),
        'event_types': SystemLog.EventType.choices,
        'severities': SystemLog.Severity.choices,
        'selected_event': event_type,
        'selected_severity': severity,
    }
    return render(request, 'admin_system_logs.html', context)
