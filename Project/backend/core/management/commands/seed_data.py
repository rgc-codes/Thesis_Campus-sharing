import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from backend.core.models import CustomUser, Category, Item, Reservation, PenaltyRecord, CirculationLog


class Command(BaseCommand):
    help = 'Seed the database with sample items, users, and reservations for thesis demo.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Seeding Campus Sharing database..."))

        # 1. Create Users
        admin_user, _ = CustomUser.objects.get_or_create(
            username='admin',
            defaults={
                'first_name': 'Sarah',
                'last_name': 'Vance',
                'email': 'admin@campus.edu',
                'role': CustomUser.Role.ADMIN,
                'student_id': 'ADM2026-01',
                'is_staff': True,
                'is_superuser': True,
            }
        )
        admin_user.role = CustomUser.Role.ADMIN
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.set_password('admin123')
        admin_user.save()

        manager_user, _ = CustomUser.objects.get_or_create(
            username='manager1',
            defaults={
                'first_name': 'Marcus',
                'last_name': 'Brody',
                'email': 'manager1@campus.edu',
                'role': CustomUser.Role.MANAGER,
                'student_id': 'MGR2026-01',
                'is_staff': True,
            }
        )
        manager_user.role = CustomUser.Role.MANAGER
        manager_user.is_staff = True
        manager_user.student_id = 'MGR2026-01'
        manager_user.set_password('pass123')
        manager_user.save()

        student1, _ = CustomUser.objects.get_or_create(
            username='student1',
            defaults={
                'first_name': 'Elena',
                'last_name': 'Rostova',
                'email': 'student1@campus.edu',
                'role': CustomUser.Role.STUDENT,
                'student_id': 'STU2026-88',
            }
        )
        student1.role = CustomUser.Role.STUDENT
        student1.student_id = 'STU2026-88'
        student1.set_password('pass123')
        student1.save()

        student2, _ = CustomUser.objects.get_or_create(
            username='kai',
            defaults={
                'first_name': 'Kai',
                'last_name': 'Chen',
                'email': 'kai@student.campus.edu',
                'role': CustomUser.Role.STUDENT,
                'student_id': 'STU2026-90',
            }
        )
        student2.role = CustomUser.Role.STUDENT
        student2.student_id = 'STU2026-90'
        student2.set_password('student123')
        student2.save()

        self.stdout.write(self.style.SUCCESS("Users created (admin, manager1, student1, kai)."))

        # 2. Create Categories
        cat_media, _ = Category.objects.get_or_create(
            name='Media & Cameras',
            defaults={'description': 'Professional DSLR, mirrorless cameras, and optics'}
        )
        cat_audio, _ = Category.objects.get_or_create(
            name='Audio & Microphones',
            defaults={'description': 'Field recorders, shotgun mics, and wireless lavalier sets'}
        )
        cat_lab, _ = Category.objects.get_or_create(
            name='Laboratory & Sensors',
            defaults={'description': 'Oscilloscopes, digital multimeters, and environmental probes'}
        )
        cat_vr, _ = Category.objects.get_or_create(
            name='Spatial & Computing',
            defaults={'description': 'VR headsets, LiDAR scanners, and high-performance mobile workstations'}
        )

        self.stdout.write(self.style.SUCCESS("Categories created."))

        # 3. Create Items
        items_data = [
            (cat_media, 'Sony Alpha FX3 Cinema Line', 'Full-frame cinema camera with 4K 120p capability, XLR handle unit, and dual CFexpress slots.', 2, 'https://images.unsplash.com/photo-1516035069371-29a1b244cc32?auto=format&fit=crop&w=800&q=80'),
            (cat_media, 'Canon EOS R5 Mirrorless Kit', '8K raw recording, 45MP full-frame CMOS sensor with RF 24-70mm f/2.8L lens included.', 3, 'https://images.unsplash.com/photo-1502920917128-1aa500764cbd?auto=format&fit=crop&w=800&q=80'),
            (cat_audio, 'Sennheiser MKH 416 Shotgun Mic', 'Moisture-resistant compact shotgun microphone ideal for film, broadcast, and field audio.', 4, 'https://images.unsplash.com/photo-1590602847861-f357a9332bbc?auto=format&fit=crop&w=800&q=80'),
            (cat_audio, 'Røde Wireless PRO Kit', 'Dual-channel compact wireless microphone system with 32-bit float on-board recording.', 5, 'https://images.unsplash.com/photo-1520523839898-507125ef5381?auto=format&fit=crop&w=800&q=80'),
            (cat_lab, 'Rigol DS1054Z Digital Oscilloscope', '4-channel 50MHz digital oscilloscope with UltraVision deep-memory technology.', 4, 'https://images.unsplash.com/photo-1581092160607-ee22621dd758?auto=format&fit=crop&w=800&q=80'),
            (cat_lab, 'FLIR E8-XT Infrared Thermal Camera', 'Handheld thermal camera with MSX technology, 320x240 thermal resolution.', 2, 'https://images.unsplash.com/photo-1581092335397-9583fe92d232?auto=format&fit=crop&w=800&q=80'),
            (cat_vr, 'Meta Quest Pro Mixed-Reality Headset', 'High-resolution mixed reality headset with eye/face tracking and color passthrough.', 3, 'https://images.unsplash.com/photo-1622979135225-d2ba269bc1df?auto=format&fit=crop&w=800&q=80'),
            (cat_vr, 'Leica BLK360 Precision LiDAR Scanner', 'Compact imaging laser scanner for rapid capturing of 3D point clouds and high-res HDR panoramas.', 1, 'https://images.unsplash.com/photo-1508614589041-895b88991e3e?auto=format&fit=crop&w=800&q=80'),
        ]

        created_items = []
        for cat, name, desc, qty, img in items_data:
            item, _ = Item.objects.get_or_create(
                name=name,
                category=cat,
                defaults={'description': desc, 'total_quantity': qty, 'is_active': True, 'image_url': img}
            )
            item.description = desc
            item.total_quantity = qty
            item.image_url = img
            item.is_active = True
            item.save()
            created_items.append(item)

        self.stdout.write(self.style.SUCCESS(f"{len(created_items)} Items created."))

        # 4. Clean previous reservations & logs
        Reservation.objects.all().delete()
        PenaltyRecord.objects.all().delete()
        CirculationLog.objects.all().delete()

        now = timezone.now()

        # 5. Create Approved reservation for Elena (student1) for the Sony Alpha FX3
        # Ready for pickup pass on student dashboard, and ready for manager checkout!
        res_approved_fx3 = Reservation(
            user=student1,
            item=created_items[0],  # Sony Alpha FX3 Cinema Line
            quantity=1,
            start_time=now + datetime.timedelta(hours=2),
            end_time=now + datetime.timedelta(hours=50),
            status=Reservation.Status.APPROVED,
        )
        super(Reservation, res_approved_fx3).save()

        # Audit entry for the approval
        CirculationLog.objects.create(
            reservation=res_approved_fx3,
            actor=manager_user,
            action_type=CirculationLog.Action.APPROVED,
            note="Reservation approved for studio assignment by Marcus Brody"
        )

        # 6. Create Pending reservation for Kai (Canon EOS R5)
        res_pending = Reservation(
            user=student2,
            item=created_items[1],  # Canon R5
            quantity=1,
            start_time=now + datetime.timedelta(hours=4),
            end_time=now + datetime.timedelta(hours=28),
            status=Reservation.Status.PENDING,
        )
        super(Reservation, res_pending).save()

        self.stdout.write(self.style.SUCCESS("Sample reservations and audit logs created!"))
        self.stdout.write(self.style.SUCCESS("\nDemo Credentials:"))
        self.stdout.write("  Student: username=student1 password=pass123")
        self.stdout.write("  Manager: username=manager1 password=pass123")
        self.stdout.write("  Admin:   username=admin    password=admin123\n")
