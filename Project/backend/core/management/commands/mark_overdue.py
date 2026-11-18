from django.core.management.base import BaseCommand
from django.utils import timezone
from backend.core.models import Reservation


class Command(BaseCommand):
    help = 'Mark ACTIVE reservations past their deadline as OVERDUE.'

    def handle(self, *args, **options):
        count = Reservation.objects.filter(
            status=Reservation.Status.ACTIVE,
            end_time__lt=timezone.now(),
        ).update(status=Reservation.Status.OVERDUE)
        self.stdout.write(self.style.SUCCESS(f"Marked {count} reservation(s) as OVERDUE."))
