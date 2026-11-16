import csv
from datetime import datetime
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .models import Category, CirculationLog, CustomUser, Item, Reservation
from .qr_utils import generate_qr_base64


def role_required(*allowed_roles):
    """Restricts view execution to authenticated users holding one of the specified roles or staff privileges."""
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(settings.LOGIN_URL)
            if request.user.role not in allowed_roles and not request.user.is_staff:
                return HttpResponseForbidden("Permission denied.")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _get_available_quantity(item, exclude_pk=None):
    """Calculates instantaneous unallocated stock for catalog display."""
    now = timezone.now()
    active_qs = Reservation.objects.filter(
        item=item,
        start_time__lt=now,
        end_time__gt=now,
        status__in=[Reservation.Status.APPROVED, Reservation.Status.ACTIVE],
    )
    if exclude_pk:
        active_qs = active_qs.exclude(pk=exclude_pk)
    allocated = active_qs.aggregate(total=Sum('quantity'))['total'] or 0
    return max(0, item.total_quantity - allocated)


def catalog_view(request):
    categories = Category.objects.all()
    items_qs = Item.objects.filter(is_active=True).select_related('category')

    query = request.GET.get('q', '').strip()
    if query:
        items_qs = items_qs.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )

    category_id = request.GET.get('category', '').strip()
    if category_id:
        items_qs = items_qs.filter(category_id=category_id)

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
    item = get_object_or_404(Item, pk=item_id, is_active=True)

    if request.method == 'POST':
        start_raw = request.POST.get('start_time', '').strip()
        end_raw = request.POST.get('end_time', '').strip()

        if not start_raw or not end_raw:
            messages.error(request, "Both pickup and return times are required.")
            return render(request, 'reserve_item.html', {'item': item})

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
            return render(request, 'reserve_item.html', {'item': item})

        # Atomic transaction with row lock on inventory to eliminate concurrent booking race conditions
        with transaction.atomic():
            locked_item = Item.objects.select_for_update().get(pk=item.pk)
            reservation = Reservation(
                user=request.user,
                item=locked_item,
                quantity=1,
                start_time=start_time,
                end_time=end_time,
                status=Reservation.Status.PENDING,
            )
            try:
                reservation.save()
                messages.success(request, "🎉 Booking Request Submitted! Awaiting lab manager approval.")
                return redirect('student_dashboard')
            except ValidationError as exc:
                if hasattr(exc, 'message_dict'):
                    for errors in exc.message_dict.values():
                        for err in errors:
                            messages.error(request, err)
                else:
                    for err in exc.messages:
                        messages.error(request, err)

    return render(request, 'reserve_item.html', {'item': item})


@login_required
def student_dashboard_view(request):
    now = timezone.now()

    # Scoped strictly to request.user to prevent IDOR leaks
    active_reservations = (
        Reservation.objects.filter(user=request.user, status__in=['ACTIVE', 'OVERDUE'])
        .select_related('item', 'item__category')
        .order_by('end_time')
    )
    upcoming_reservations = (
        Reservation.objects.filter(user=request.user, status__in=['PENDING', 'APPROVED'])
        .select_related('item', 'item__category')
        .order_by('start_time')
    )

    pass_reservation = (
        Reservation.objects.filter(
            user=request.user,
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
            'is_overdue': res.status == 'OVERDUE',
        })

    qr_code_base64 = None
    if pass_reservation:
        token = (
            f"RES-{pass_reservation.id}|"
            f"STU-{request.user.student_id or request.user.username}|"
            f"ITEM-{pass_reservation.item.id}|"
            f"STATUS-{pass_reservation.status}"
        )
        qr_code_base64 = generate_qr_base64(token)

    context = {
        'active_data': active_data,
        'upcoming_reservations': upcoming_reservations,
        'active_count': active_reservations.count(),
        'overdue_count': active_reservations.filter(status='OVERDUE').count(),
        'next_deadline': next_deadline,
        'pass_reservation': pass_reservation,
        'qr_code_base64': qr_code_base64,
        'trust_score': 98,
        'lifetime_loans': 14,
    }
    return render(request, 'student_dashboard.html', context)


@login_required
@role_required(CustomUser.Role.ADMIN, CustomUser.Role.MANAGER)
def admin_dashboard_view(request):
    current_filter = request.GET.get('filter', 'all')
    reservations = Reservation.objects.select_related('user', 'item', 'item__category').order_by('-created_at')

    if current_filter == 'pending':
        reservations = reservations.filter(status=Reservation.Status.PENDING)
    elif current_filter == 'active':
        reservations = reservations.filter(status=Reservation.Status.ACTIVE)
    elif current_filter == 'overdue':
        reservations = reservations.filter(status=Reservation.Status.OVERDUE)

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timezone.timedelta(days=1)

    context = {
        'reservations': reservations,
        'current_filter': current_filter,
        'total_count': Reservation.objects.count(),
        'pending_count': Reservation.objects.filter(status=Reservation.Status.PENDING).count(),
        'active_count': Reservation.objects.filter(status=Reservation.Status.ACTIVE).count(),
        'overdue_count': Reservation.objects.filter(status=Reservation.Status.OVERDUE).count(),
        'today_pickups': Reservation.objects.filter(start_time__gte=today_start, start_time__lt=today_end).count(),
    }
    return render(request, 'admin_dashboard.html', context)


@login_required
@role_required(CustomUser.Role.ADMIN, CustomUser.Role.MANAGER)
def approve_reservation_view(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects.select_for_update().select_related('item', 'user'),
            pk=pk,
            status=Reservation.Status.PENDING,
        )
        # Lock item row to re-verify capacity before approving
        Item.objects.select_for_update().get(pk=reservation.item_id)
        reservation.status = Reservation.Status.APPROVED
        reservation.save()

        CirculationLog.objects.create(
            reservation=reservation,
            actor=request.user,
            action_type=CirculationLog.Action.APPROVED,
            note=f"Reservation #{pk} approved for collection by {request.user.get_full_name() or request.user.username}",
        )

    messages.success(request, f"✅ Reservation #{pk} approved. Digital QR pass is now active.")
    return redirect('admin_dashboard')


@login_required
@role_required(CustomUser.Role.ADMIN, CustomUser.Role.MANAGER)
def checkout_reservation_view(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects.select_for_update().select_related('item', 'user'),
            pk=pk,
            status=Reservation.Status.APPROVED,
        )
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

    messages.success(request, "🚀 Equipment checked out! Circulation timer started.")
    return redirect('admin_dashboard')


@login_required
@role_required(CustomUser.Role.ADMIN, CustomUser.Role.MANAGER)
def reject_reservation_view(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects.select_for_update(),
            pk=pk,
            status=Reservation.Status.PENDING,
        )
        reservation.status = Reservation.Status.RETURNED
        reservation.save()

        CirculationLog.objects.create(
            reservation=reservation,
            actor=request.user,
            action_type=CirculationLog.Action.REJECTED,
            note=f"Reservation #{pk} rejected by {request.user.get_full_name() or request.user.username}",
        )

    messages.info(request, f"Reservation #{pk} has been rejected.")
    return redirect('admin_dashboard')


@login_required
@role_required(CustomUser.Role.ADMIN, CustomUser.Role.MANAGER)
def return_item_view(request, pk):
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    with transaction.atomic():
        reservation = get_object_or_404(
            Reservation.objects.select_for_update().select_related('item', 'user'),
            pk=pk,
            status__in=[Reservation.Status.ACTIVE, Reservation.Status.OVERDUE],
        )
        reservation.status = Reservation.Status.RETURNED
        reservation.save()

        CirculationLog.objects.create(
            reservation=reservation,
            actor=request.user,
            action_type=CirculationLog.Action.RETURNED,
            note=f"Equipment inspected and returned by {request.user.get_full_name() or request.user.username}",
        )

    messages.success(request, "📦 Equipment returned safely. Inventory restored.")
    return redirect('admin_dashboard')


@login_required
@role_required(CustomUser.Role.ADMIN, CustomUser.Role.MANAGER)
def circulation_log_view(request):
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
        response['Content-Disposition'] = f'attachment; filename="circulation_audit_log_{timestamp_slug}.csv"'
        writer = csv.writer(response)
        writer.writerow([
            'Log ID', 'Timestamp', 'Action Type', 'Item Name', 'Category',
            'Student', 'Student ID', 'Lab Officer', 'Notes',
        ])
        for log in logs:
            writer.writerow([
                log.id,
                log.timestamp.strftime('%Y-%m-%d %H:%M:%S'),
                log.get_action_type_display(),
                log.reservation.item.name,
                log.reservation.item.category.name,
                log.reservation.user.get_full_name() or log.reservation.user.username,
                log.reservation.user.student_id or 'N/A',
                log.actor.get_full_name() or log.actor.username if log.actor else 'System',
                log.note,
            ])
        return response

    context = {
        'logs': logs,
        'total_logs_count': logs.count(),
        'approved_count': logs.filter(action_type=CirculationLog.Action.APPROVED).count(),
        'checkout_count': logs.filter(action_type=CirculationLog.Action.CHECKED_OUT).count(),
        'returned_count': logs.filter(action_type=CirculationLog.Action.RETURNED).count(),
        'rejected_count': logs.filter(action_type=CirculationLog.Action.REJECTED).count(),
    }
    return render(request, 'circulation_log.html', context)


# Route aliases for clean PEP 8 naming
approve_reservation = approve_reservation_view
checkout_reservation = checkout_reservation_view
reject_reservation = reject_reservation_view
mark_returned = return_item_view
return_item = return_item_view
admin_dashboard = admin_dashboard_view
