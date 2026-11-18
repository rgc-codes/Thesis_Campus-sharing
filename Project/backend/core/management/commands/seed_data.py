import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from backend.core.models import (
    CustomUser, Category, Item, Reservation, PenaltyRecord, CirculationLog, SystemLog, log_system_event
)


class Command(BaseCommand):
    help = 'Seed the database with exact 3NF schema, 4 authenticated users, and 3 equipment assets.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Initializing normalized Campus Sharing database..."))

        # 1. Reset Circulation & Inventory Data for Deterministic 3NF State
        PenaltyRecord.objects.all().delete()
        CirculationLog.objects.all().delete()
        Reservation.objects.all().delete()
        Item.objects.all().delete()
        Category.objects.all().delete()
        CustomUser.objects.exclude(username__in=['aether', 'Max', 'Jannat', 'Sazzad']).delete()

        # 2. Create Required Users
        # Admin: aether
        admin_user, _ = CustomUser.objects.get_or_create(
            username='aether',
            defaults={
                'first_name': 'Aether',
                'last_name': 'Architect',
                'email': 'aether@campus.edu',
                'role': CustomUser.Role.ADMIN,
                'student_id': 'ADM-001',
                'is_staff': True,
                'is_superuser': True,
            }
        )
        admin_user.role = CustomUser.Role.ADMIN
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.student_id = 'ADM-001'
        admin_user.set_password('pass123')
        admin_user.save()

        # Manager: Max
        manager_user, _ = CustomUser.objects.get_or_create(
            username='Max',
            defaults={
                'first_name': 'Max',
                'last_name': 'Desk Manager',
                'email': 'max@campus.edu',
                'role': CustomUser.Role.MANAGER,
                'student_id': 'MGR-001',
                'is_staff': False,
                'is_superuser': False,
            }
        )
        manager_user.role = CustomUser.Role.MANAGER
        manager_user.is_staff = False
        manager_user.is_superuser = False
        manager_user.student_id = 'MGR-001'
        manager_user.set_password('pass123')
        manager_user.save()

        # Student 1: Jannat
        student_jannat, _ = CustomUser.objects.get_or_create(
            username='Jannat',
            defaults={
                'first_name': 'Jannat',
                'last_name': 'Hossain',
                'email': 'jannat@student.campus.edu',
                'role': CustomUser.Role.STUDENT,
                'student_id': 'STU-001',
            }
        )
        student_jannat.role = CustomUser.Role.STUDENT
        student_jannat.student_id = 'STU-001'
        student_jannat.set_password('pass123')
        student_jannat.save()

        # Student 2: Sazzad
        student_sazzad, _ = CustomUser.objects.get_or_create(
            username='Sazzad',
            defaults={
                'first_name': 'Sazzad',
                'last_name': 'Rahman',
                'email': 'sazzad@student.campus.edu',
                'role': CustomUser.Role.STUDENT,
                'student_id': 'STU-002',
            }
        )
        student_sazzad.role = CustomUser.Role.STUDENT
        student_sazzad.student_id = 'STU-002'
        student_sazzad.set_password('pass123')
        student_sazzad.save()

        self.stdout.write(self.style.SUCCESS("Users created: Jannat (Student), Sazzad (Student), Max (Manager), aether (Admin)."))

        # 3. Create Categories
        cat_camera = Category.objects.create(
            name='Media & Cinema Equipment',
            description='High-resolution mirrorless cameras, prime cinema optics, and broadcast field gear.',
            workflow_type=Category.WorkflowType.STUDIO,
            station_location='Desk A · Media Studio Lab',
            inspection_checklist='1. Optical sensor & glass clarity\n2. Dual card slot read/write cycle\n3. XLR audio input response\n4. Battery cycle test'
        )

        cat_drone = Category.objects.create(
            name='Autonomous Aerial Systems',
            description='Field UAVs, multispectral sensor arrays, and aerial robotics for mapping.',
            workflow_type=Category.WorkflowType.LAB,
            station_location='Desk B · Robotics Hangar',
            inspection_checklist='1. Rotor blade & motor integrity\n2. Omnidirectional vision calibration\n3. Remote telemetry ping\n4. Battery thermal report'
        )

        cat_spatial = Category.objects.create(
            name='Spatial Computing & XR',
            description='Wearable micro-OLED spatial computers, VR headsets, and sensory feedback hardware.',
            workflow_type=Category.WorkflowType.COMPUTING,
            station_location='Desk C · Spatial VR Sandbox',
            inspection_checklist='1. Dual micro-OLED optical seal audit\n2. Eye-tracking & hand-tracking calibration\n3. External battery cable stress test\n4. Thermal exhaust inspection'
        )

        # 4. Create the EXACT 3 Equipment Assets with 3NF Total Quantities
        camera_item = Item.objects.create(
            category=cat_camera,
            name='Sony FX3 Cinema Camera',
            description='Full-frame cinema line camera with 4K 120p, 15+ stops dynamic range, dual CFexpress slots, and XLR top handle audio.',
            total_quantity=2,  # Availability = 2
            image_url='https://images.unsplash.com/photo-1516035069371-29a1b244cc32?auto=format&fit=crop&w=800&q=80',
            is_active=True,
        )

        drone_item = Item.objects.create(
            category=cat_drone,
            name='DJI Mavic 3 Pro Cine Drone',
            description='Triple-camera system drone with Hasselblad 4/3 CMOS sensor, Apple ProRes encoding, and 43-min flight time.',
            total_quantity=3,  # Availability = 3
            image_url='https://images.unsplash.com/photo-1508614589041-895b88991e3e?auto=format&fit=crop&w=800&q=80',
            is_active=True,
        )

        vision_pro_item = Item.objects.create(
            category=cat_spatial,
            name='Apple Vision Pro',
            description='Spatial computing headset with dual 23-million pixel micro-OLED displays, M2+R1 dual chips, and real-time visionOS tracking.',
            total_quantity=1,  # Availability = 1
            image_url='https://images.unsplash.com/photo-1593508512255-86ab42a8e620?auto=format&fit=crop&w=800&q=80',
            is_active=True,
        )

        self.stdout.write(self.style.SUCCESS(
            "Created 3 assets:\n"
            "  1. Sony FX3 Cinema Camera (Total: 2, Initial Available: 2)\n"
            "  2. DJI Mavic 3 Pro Cine Drone (Total: 3, Initial Available: 3)\n"
            "  3. Apple Vision Pro (Total: 1, Initial Available: 1)"
        ))

        # 5. Log System Initialization
        log_system_event(
            SystemLog.EventType.SCHEMA_AUDIT,
            "Clean 3NF database state initialized with 4 verified users and 3 isolated equipment assets.",
            severity=SystemLog.Severity.INFO,
            metadata={
                'users': ['Jannat', 'Sazzad', 'Max', 'aether'],
                'items': {'Camera': 2, 'Drone': 3, 'Apple Vision Pro': 1}
            }
        )
        self.stdout.write(self.style.SUCCESS("Database seeding completed successfully."))
