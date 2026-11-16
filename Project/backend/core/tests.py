# core/tests.py
"""
Comprehensive test suite for the Campus Item-Sharing and Reservation System.
Covers: User roles, reservation workflow, conflict prevention, RBAC enforcement.
"""

from datetime import timedelta

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import CustomUser, Category, Item, Reservation, PenaltyRecord, CirculationLog


class ModelSetupMixin:
    """Shared helper to create common test fixtures."""

    def setUp(self):
        super().setUp()
        # Create users with different roles
        self.admin_user = CustomUser.objects.create_user(
            username='admin_user',
            password='testpass123',
            role=CustomUser.Role.ADMIN,
            student_id='ADM-001',
            is_staff=True,
        )
        self.manager_user = CustomUser.objects.create_user(
            username='manager_user',
            password='testpass123',
            role=CustomUser.Role.MANAGER,
            student_id='MGR-001',
        )
        self.student_user = CustomUser.objects.create_user(
            username='student_user',
            password='testpass123',
            role=CustomUser.Role.STUDENT,
            student_id='STU-001',
        )
        self.student_user2 = CustomUser.objects.create_user(
            username='student_user2',
            password='testpass123',
            role=CustomUser.Role.STUDENT,
            student_id='STU-002',
        )

        # Create a category and item
        self.category = Category.objects.create(
            name='Camera Equipment',
            description='DSLR cameras and accessories',
        )
        self.item = Item.objects.create(
            category=self.category,
            name='Canon EOS R5',
            description='Full-frame mirrorless camera',
            total_quantity=3,
            is_active=True,
        )
        self.single_item = Item.objects.create(
            category=self.category,
            name='Hasselblad X2D',
            description='Medium format camera (unique)',
            total_quantity=1,
            is_active=True,
        )


# ==========================================================================
# TEST 1: User Roles & Permissions
# ==========================================================================
class UserRoleTests(ModelSetupMixin, TestCase):
    """Validates that user roles are correctly assigned and enforced."""

    def test_user_role_defaults_to_student(self):
        """New users should default to the STUDENT role."""
        user = CustomUser.objects.create_user(
            username='newbie', password='testpass123'
        )
        self.assertEqual(user.role, CustomUser.Role.STUDENT)

    def test_admin_role_assignment(self):
        self.assertEqual(self.admin_user.role, CustomUser.Role.ADMIN)
        self.assertTrue(self.admin_user.is_staff)

    def test_manager_role_assignment(self):
        self.assertEqual(self.manager_user.role, CustomUser.Role.MANAGER)

    def test_student_role_assignment(self):
        self.assertEqual(self.student_user.role, CustomUser.Role.STUDENT)

    def test_user_str_representation(self):
        self.assertIn('Admin', str(self.admin_user))
        self.assertIn('Student', str(self.student_user))

    def test_student_id_uniqueness(self):
        """Two users cannot share the same student_id."""
        with self.assertRaises(Exception):
            CustomUser.objects.create_user(
                username='duplicate_sid',
                password='testpass123',
                student_id='STU-001',  # already taken
            )


# ==========================================================================
# TEST 2: Successful Reservation
# ==========================================================================
class SuccessfulReservationTests(ModelSetupMixin, TestCase):
    """Tests clean booking of an available item."""

    def test_successful_reservation_creation(self):
        """A valid reservation should save without errors."""
        now = timezone.now()
        res = Reservation(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.PENDING,
        )
        res.save()
        self.assertEqual(res.pk is not None, True)
        self.assertEqual(res.status, Reservation.Status.PENDING)

    def test_reservation_str_representation(self):
        now = timezone.now()
        res = Reservation.objects.create(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
        )
        self.assertIn('student_user', str(res))
        self.assertIn('Canon EOS R5', str(res))

    def test_multiple_non_overlapping_reservations(self):
        """Non-overlapping reservations on the same item should succeed."""
        now = timezone.now()
        Reservation.objects.create(
            user=self.student_user,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.APPROVED,
        )
        # Second reservation AFTER the first ends
        res2 = Reservation(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=4),
            end_time=now + timedelta(hours=6),
            status=Reservation.Status.APPROVED,
        )
        res2.save()  # Should succeed
        self.assertIsNotNone(res2.pk)

    def test_reservation_within_available_capacity(self):
        """Multiple overlapping reservations within total_quantity should succeed."""
        now = timezone.now()
        # item has total_quantity=3, so 3 overlapping reservations of qty=1 each should work
        for i in range(3):
            user = CustomUser.objects.create_user(
                username=f'cap_user_{i}',
                password='testpass123',
                student_id=f'CAP-{i:03d}',
            )
            Reservation.objects.create(
                user=user,
                item=self.item,
                quantity=1,
                start_time=now + timedelta(hours=1),
                end_time=now + timedelta(hours=3),
                status=Reservation.Status.APPROVED,
            )
        self.assertEqual(
            Reservation.objects.filter(item=self.item).count(), 3
        )


# ==========================================================================
# TEST 3: Conflict Prevention (Blocked Overlapping Bookings)
# ==========================================================================
class ConflictPreventionTests(ModelSetupMixin, TestCase):
    """Tests that overlapping bookings are correctly blocked."""

    def test_exact_overlap_blocked(self):
        """Two reservations for the exact same time on a single-unit item should fail."""
        now = timezone.now()
        start = now + timedelta(hours=1)
        end = now + timedelta(hours=3)

        Reservation.objects.create(
            user=self.student_user,
            item=self.single_item,
            quantity=1,
            start_time=start,
            end_time=end,
            status=Reservation.Status.APPROVED,
        )

        res2 = Reservation(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=start,
            end_time=end,
            status=Reservation.Status.APPROVED,
        )
        with self.assertRaises(ValidationError):
            res2.save()

    def test_partial_overlap_start_blocked(self):
        """A reservation that starts before the existing one ends should fail (single unit)."""
        now = timezone.now()
        Reservation.objects.create(
            user=self.student_user,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=4),
            status=Reservation.Status.APPROVED,
        )

        res2 = Reservation(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=3),   # overlaps last hour
            end_time=now + timedelta(hours=6),
            status=Reservation.Status.APPROVED,
        )
        with self.assertRaises(ValidationError):
            res2.save()

    def test_partial_overlap_end_blocked(self):
        """A reservation that ends after the existing one starts should fail (single unit)."""
        now = timezone.now()
        Reservation.objects.create(
            user=self.student_user,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=3),
            end_time=now + timedelta(hours=6),
            status=Reservation.Status.APPROVED,
        )

        res2 = Reservation(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=4),   # overlaps into existing
            status=Reservation.Status.APPROVED,
        )
        with self.assertRaises(ValidationError):
            res2.save()

    def test_enclosing_overlap_blocked(self):
        """A reservation fully enclosing an existing one should fail (single unit)."""
        now = timezone.now()
        Reservation.objects.create(
            user=self.student_user,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=2),
            end_time=now + timedelta(hours=4),
            status=Reservation.Status.APPROVED,
        )

        res2 = Reservation(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=5),
            status=Reservation.Status.APPROVED,
        )
        with self.assertRaises(ValidationError):
            res2.save()

    def test_capacity_exceeded_blocked(self):
        """Exceeding total_quantity during overlapping window should fail."""
        now = timezone.now()
        start = now + timedelta(hours=1)
        end = now + timedelta(hours=3)

        # Fill all 3 units of self.item
        for i in range(3):
            user = CustomUser.objects.create_user(
                username=f'fill_user_{i}',
                password='testpass123',
                student_id=f'FILL-{i:03d}',
            )
            Reservation.objects.create(
                user=user,
                item=self.item,
                quantity=1,
                start_time=start,
                end_time=end,
                status=Reservation.Status.APPROVED,
            )

        # 4th reservation should fail
        res4 = Reservation(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=start,
            end_time=end,
            status=Reservation.Status.APPROVED,
        )
        with self.assertRaises(ValidationError):
            res4.save()

    def test_pending_reservations_do_not_block(self):
        """PENDING reservations should NOT consume capacity (only APPROVED/ACTIVE do)."""
        now = timezone.now()
        start = now + timedelta(hours=1)
        end = now + timedelta(hours=3)

        Reservation.objects.create(
            user=self.student_user,
            item=self.single_item,
            quantity=1,
            start_time=start,
            end_time=end,
            status=Reservation.Status.PENDING,  # Does NOT consume capacity
        )

        res2 = Reservation(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=start,
            end_time=end,
            status=Reservation.Status.APPROVED,
        )
        res2.save()  # Should succeed
        self.assertIsNotNone(res2.pk)

    def test_returned_reservations_do_not_block(self):
        """RETURNED reservations should NOT consume capacity."""
        now = timezone.now()
        start = now + timedelta(hours=1)
        end = now + timedelta(hours=3)

        Reservation.objects.create(
            user=self.student_user,
            item=self.single_item,
            quantity=1,
            start_time=start,
            end_time=end,
            status=Reservation.Status.RETURNED,
        )

        res2 = Reservation(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=start,
            end_time=end,
            status=Reservation.Status.APPROVED,
        )
        res2.save()
        self.assertIsNotNone(res2.pk)

    def test_start_time_after_end_time_rejected(self):
        """Inverted date range (start >= end) must be rejected."""
        now = timezone.now()
        res = Reservation(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=5),
            end_time=now + timedelta(hours=2),  # end before start
            status=Reservation.Status.PENDING,
        )
        with self.assertRaises(ValidationError):
            res.save()

    def test_same_start_and_end_time_rejected(self):
        """Zero-duration reservation (start == end) must be rejected."""
        now = timezone.now()
        same_time = now + timedelta(hours=2)
        res = Reservation(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=same_time,
            end_time=same_time,
            status=Reservation.Status.PENDING,
        )
        with self.assertRaises(ValidationError):
            res.save()

    def test_past_start_time_rejected_for_new_reservation(self):
        """A new reservation with start_time in the past must be rejected."""
        now = timezone.now()
        res = Reservation(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now - timedelta(hours=1),  # Past!
            end_time=now + timedelta(hours=2),
            status=Reservation.Status.PENDING,
        )
        with self.assertRaises(ValidationError):
            res.save()

    def test_quantity_exceeds_total_inventory_rejected(self):
        """Requesting more than total_quantity in a single reservation must fail."""
        now = timezone.now()
        res = Reservation(
            user=self.student_user,
            item=self.item,
            quantity=self.item.total_quantity + 1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.PENDING,
        )
        with self.assertRaises(ValidationError):
            res.save()

    def test_adjacent_reservations_no_conflict(self):
        """Reservations that touch at a boundary (end == start) should NOT conflict."""
        now = timezone.now()
        Reservation.objects.create(
            user=self.student_user,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.APPROVED,
        )
        # Starts exactly when the first ends
        res2 = Reservation(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=now + timedelta(hours=3),   # == prev end_time
            end_time=now + timedelta(hours=5),
            status=Reservation.Status.APPROVED,
        )
        res2.save()  # Should succeed — no overlap
        self.assertIsNotNone(res2.pk)


# ==========================================================================
# TEST 4: Unauthorized Admin Access (RBAC)
# ==========================================================================
class UnauthorizedAccessTests(ModelSetupMixin, TestCase):
    """Confirms students get 403 when accessing manager/admin views."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_student_cannot_access_admin_dashboard(self):
        """A STUDENT user must be blocked from the admin dashboard."""
        self.client.login(username='student_user', password='testpass123')
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 403)

    def test_manager_can_access_admin_dashboard(self):
        """A MANAGER user should be able to access the admin dashboard."""
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_admin_can_access_admin_dashboard(self):
        """An ADMIN user should be able to access the admin dashboard."""
        self.client.login(username='admin_user', password='testpass123')
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_redirected_from_dashboard(self):
        """An unauthenticated user should be redirected to login."""
        response = self.client.get(reverse('admin_dashboard'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

    def test_student_cannot_approve_reservation(self):
        """A STUDENT must not be able to approve a reservation."""
        now = timezone.now()
        res = Reservation.objects.create(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.PENDING,
        )
        self.client.login(username='student_user', password='testpass123')
        response = self.client.post(reverse('approve_reservation', args=[res.pk]))
        self.assertEqual(response.status_code, 403)
        # Verify the reservation status is unchanged
        res.refresh_from_db()
        self.assertEqual(res.status, Reservation.Status.PENDING)

    def test_student_cannot_reject_reservation(self):
        """A STUDENT must not be able to reject a reservation."""
        now = timezone.now()
        res = Reservation.objects.create(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.PENDING,
        )
        self.client.login(username='student_user', password='testpass123')
        response = self.client.post(reverse('reject_reservation', args=[res.pk]))
        self.assertEqual(response.status_code, 403)

    def test_student_can_access_catalog(self):
        """Students SHOULD be able to access the catalog."""
        self.client.login(username='student_user', password='testpass123')
        response = self.client.get(reverse('catalog'))
        self.assertEqual(response.status_code, 200)

    def test_student_can_access_student_dashboard(self):
        """Students SHOULD be able to access their own dashboard."""
        self.client.login(username='student_user', password='testpass123')
        response = self.client.get(reverse('student_dashboard'))
        self.assertEqual(response.status_code, 200)


# ==========================================================================
# TEST 5: Reservation Workflow via Views (Integration)
# ==========================================================================
class ReservationViewIntegrationTests(ModelSetupMixin, TestCase):
    """Tests the reserve_item view end-to-end via HTTP."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        self.client.login(username='student_user', password='testpass123')

    def test_reserve_item_get_renders_form(self):
        """GET on the reserve page should render the form template."""
        response = self.client.get(
            reverse('reserve_item', args=[self.item.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.item.name)

    def test_reserve_item_post_success(self):
        """A valid POST should create a PENDING reservation and redirect."""
        now = timezone.now()
        start = (now + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M')
        end = (now + timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M')

        response = self.client.post(
            reverse('reserve_item', args=[self.item.pk]),
            {'start_time': start, 'end_time': end},
        )
        self.assertEqual(response.status_code, 302)  # Redirect on success
        self.assertEqual(Reservation.objects.count(), 1)
        self.assertEqual(
            Reservation.objects.first().status,
            Reservation.Status.PENDING,
        )

    def test_reserve_item_post_conflict_shows_error(self):
        """A conflicting POST should re-render the form with error messages."""
        now = timezone.now()
        start = now + timedelta(hours=1)
        end = now + timedelta(hours=3)

        # Pre-fill the single item's only slot
        Reservation.objects.create(
            user=self.student_user2,
            item=self.single_item,
            quantity=1,
            start_time=start,
            end_time=end,
            status=Reservation.Status.APPROVED,
        )

        response = self.client.post(
            reverse('reserve_item', args=[self.single_item.pk]),
            {
                'start_time': start.strftime('%Y-%m-%dT%H:%M'),
                'end_time': end.strftime('%Y-%m-%dT%H:%M'),
            },
        )
        # Should NOT redirect — should re-render with errors
        self.assertEqual(response.status_code, 200)
        # Only the original reservation should exist
        self.assertEqual(Reservation.objects.count(), 1)

    def test_reserve_item_missing_times(self):
        """POST without times should show error."""
        response = self.client.post(
            reverse('reserve_item', args=[self.item.pk]),
            {'start_time': '', 'end_time': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Reservation.objects.count(), 0)


# ==========================================================================
# TEST 6: Penalty Record
# ==========================================================================
class PenaltyRecordTests(ModelSetupMixin, TestCase):
    """Tests penalty record creation and validation."""

    def test_penalty_creation(self):
        now = timezone.now()
        res = Reservation.objects.create(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.OVERDUE,
        )
        penalty = PenaltyRecord.objects.create(
            user=self.student_user,
            reservation=res,
            amount=25.00,
            reason='Late return by 2 hours',
        )
        self.assertFalse(penalty.is_paid)
        self.assertIsNone(penalty.resolved_at)

    def test_penalty_auto_resolves_on_payment(self):
        now = timezone.now()
        res = Reservation.objects.create(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.OVERDUE,
        )
        penalty = PenaltyRecord.objects.create(
            user=self.student_user,
            reservation=res,
            amount=25.00,
            reason='Late return',
        )
        penalty.is_paid = True
        penalty.save()
        self.assertIsNotNone(penalty.resolved_at)

    def test_negative_penalty_rejected(self):
        now = timezone.now()
        res = Reservation.objects.create(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
        )
        penalty = PenaltyRecord(
            user=self.student_user,
            reservation=res,
            amount=-10.00,
            reason='Invalid',
        )
        with self.assertRaises(ValidationError):
            penalty.save()


# ==========================================================================
# TEST 7: Category & Item Model Tests
# ==========================================================================
class CategoryItemTests(ModelSetupMixin, TestCase):

    def test_category_str(self):
        self.assertEqual(str(self.category), 'Camera Equipment')

    def test_item_str(self):
        self.assertEqual(str(self.item), 'Canon EOS R5')

    def test_category_unique_name(self):
        with self.assertRaises(Exception):
            Category.objects.create(name='Camera Equipment')  # duplicate

    def test_item_protect_on_category_delete(self):
        """Deleting a category with items should raise ProtectedError."""
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.category.delete()


# ==========================================================================
# TEST 8: Circulation Log & Manager Workflow Tests
# ==========================================================================
class CirculationWorkflowTests(ModelSetupMixin, TestCase):
    """Tests checkout, return, approval logging, and circulation audit ledger."""

    def setUp(self):
        super().setUp()
        self.client = Client()
        now = timezone.now()
        self.reservation = Reservation.objects.create(
            user=self.student_user,
            item=self.item,
            quantity=1,
            start_time=now + timedelta(hours=1),
            end_time=now + timedelta(hours=3),
            status=Reservation.Status.PENDING,
        )

    def test_manager_approve_logs_circulation(self):
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.post(
            reverse('approve_reservation', args=[self.reservation.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.APPROVED)
        self.assertEqual(
            CirculationLog.objects.filter(
                reservation=self.reservation,
                action_type=CirculationLog.Action.APPROVED,
            ).count(),
            1,
        )

    def test_manager_checkout_transitions_to_active_and_logs(self):
        self.reservation.status = Reservation.Status.APPROVED
        self.reservation.save()
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.post(
            reverse('checkout_reservation', args=[self.reservation.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.ACTIVE)
        self.assertEqual(
            CirculationLog.objects.filter(
                reservation=self.reservation,
                action_type=CirculationLog.Action.CHECKED_OUT,
            ).count(),
            1,
        )

    def test_manager_return_transitions_to_returned_and_logs(self):
        self.reservation.status = Reservation.Status.ACTIVE
        self.reservation.save()
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.post(
            reverse('return_item', args=[self.reservation.pk])
        )
        self.assertEqual(response.status_code, 302)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, Reservation.Status.RETURNED)
        self.assertEqual(
            CirculationLog.objects.filter(
                reservation=self.reservation,
                action_type=CirculationLog.Action.RETURNED,
            ).count(),
            1,
        )

    def test_student_cannot_checkout_reservation(self):
        self.reservation.status = Reservation.Status.APPROVED
        self.reservation.save()
        self.client.login(username='student_user', password='testpass123')
        response = self.client.post(
            reverse('checkout_reservation', args=[self.reservation.pk])
        )
        self.assertEqual(response.status_code, 403)

    def test_circulation_log_view_and_csv_export(self):
        CirculationLog.objects.create(
            reservation=self.reservation,
            actor=self.manager_user,
            action_type=CirculationLog.Action.APPROVED,
            note='Test approval log',
        )
        self.client.login(username='manager_user', password='testpass123')
        response = self.client.get(reverse('circulation_log'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Audit Ledger')
        self.assertContains(response, 'Test approval log')

        # CSV Export
        csv_response = self.client.get(reverse('circulation_log') + '?export=csv')
        self.assertEqual(csv_response.status_code, 200)
        self.assertEqual(csv_response['Content-Type'], 'text/csv')
        self.assertIn('attachment; filename="circulation_audit_log_', csv_response['Content-Disposition'])
        content = csv_response.content.decode('utf-8')
        self.assertIn('Log ID,Timestamp,Action Type,Item Name,Category,Student,Student ID,Lab Officer,Notes', content)
        self.assertIn('Test approval log', content)

