from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from .models import CustomUser, Reservation, Item, Category


def app_context(request):
    """Global template context variables with live, role-partitioned actionable notifications."""
    demo_mode = getattr(settings, 'DEMO_MODE', True)
    notifications = []

    if request.user.is_authenticated:
        now = timezone.now()

        if request.user.role == CustomUser.Role.STUDENT:
            # 1. Approved reservations awaiting collection
            approved = Reservation.objects.filter(
                user=request.user,
                status=Reservation.Status.APPROVED,
            ).select_related('item')[:3]
            for res in approved:
                notifications.append({
                    'id': f'appr-{res.id}',
                    'title': f'{res.item.name} Approved',
                    'body': 'Your digital QR pass is ready. Collect at designated station.',
                    'tag': 'success',
                    'time': res.updated_at,
                    'link': reverse('student_dashboard'),
                    'badge': 'Pass Ready',
                })

            # 2. Urgent: Overdue loans
            overdues = Reservation.objects.filter(
                user=request.user,
                status=Reservation.Status.OVERDUE,
            ).select_related('item')[:3]
            for res in overdues:
                notifications.append({
                    'id': f'over-{res.id}',
                    'title': f'OVERDUE: {res.item.name}',
                    'body': 'Loan period expired. Return immediately to halt penalty accumulation ($5.00/hr).',
                    'tag': 'error',
                    'time': res.end_time,
                    'link': reverse('student_dashboard'),
                    'badge': 'Fine Incurred',
                })

            # 3. Upcoming deadline (< 24 hours remaining)
            active_soon = Reservation.objects.filter(
                user=request.user,
                status=Reservation.Status.ACTIVE,
                end_time__gt=now,
                end_time__lte=now + timedelta(hours=24),
            ).select_related('item')[:2]
            for res in active_soon:
                hrs_left = max(1, int((res.end_time - now).total_seconds() / 3600))
                notifications.append({
                    'id': f'due-{res.id}',
                    'title': f'Return in {hrs_left}h: {res.item.name}',
                    'body': f'Scheduled cutoff deadline: {res.end_time.strftime("%b %d, %H:%M")}.',
                    'tag': 'warning',
                    'time': res.end_time,
                    'link': reverse('student_dashboard'),
                    'badge': 'Due Soon',
                })

            # 4. Denied requests
            rejected = Reservation.objects.filter(
                user=request.user,
                status=Reservation.Status.REJECTED,
            ).select_related('item').order_by('-updated_at')[:2]
            for res in rejected:
                reason = f" Reason: {res.cancellation_reason}" if res.cancellation_reason else ""
                notifications.append({
                    'id': f'rej-{res.id}',
                    'title': f'{res.item.name} Request Denied',
                    'body': f'Manager was unable to approve request.{reason}',
                    'tag': 'info',
                    'time': res.updated_at,
                    'link': reverse('student_dashboard'),
                    'badge': 'Denied',
                })

        elif request.user.role in (CustomUser.Role.MANAGER, CustomUser.Role.ADMIN):
            # 1. Pending requests awaiting manager review per category
            pending_requests = Reservation.objects.filter(
                status=Reservation.Status.PENDING,
            ).select_related('item', 'item__category', 'user').order_by('-created_at')[:4]
            for res in pending_requests:
                notifications.append({
                    'id': f'mgr-pend-{res.id}',
                    'title': f'New {res.item.category.name} Request',
                    'body': f'{res.user.get_full_name() or res.user.username} requested {res.quantity}x {res.item.name}.',
                    'tag': 'warning',
                    'time': res.created_at,
                    'link': f"{reverse('admin_dashboard')}?category={res.item.category_id}&status=pending",
                    'badge': res.item.category.name[:12],
                })

            # 2. Overdue loans requiring retrieval
            overdue_loans = Reservation.objects.filter(
                status=Reservation.Status.OVERDUE,
            ).select_related('item', 'item__category', 'user').order_by('end_time')[:3]
            for res in overdue_loans:
                notifications.append({
                    'id': f'mgr-over-{res.id}',
                    'title': f'Overdue Alert: {res.item.name}',
                    'body': f'Borrower: {res.user.get_full_name() or res.user.username}. Due since {res.end_time.strftime("%b %d")}.',
                    'tag': 'error',
                    'time': res.end_time,
                    'link': f"{reverse('admin_dashboard')}?category={res.item.category_id}&status=overdue",
                    'badge': 'Action Req',
                })

    return {
        'DEMO_MODE': demo_mode,
        'unread_notifications_count': len(notifications),
        'user_notifications': notifications,
    }
