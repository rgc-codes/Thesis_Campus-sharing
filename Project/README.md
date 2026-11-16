# Campus Item Circulation Engine

A deterministic, role-isolated resource reservation engine built with Django 5.x, Allen's Interval Algebra for conflict prevention, and immutable circulation auditing.

`Python 3.11+` &middot; `Django 5.x` &middot; `Two-Tier MVT` &middot; `SQLite 3.x (WAL)`

---

## Architecture at a Glance

```
Browser (Tailwind UI)
  │  HTTP GET / POST
  ▼
URL Router (backend/config/urls.py + core/urls.py)
  │  Route match
  ▼
RBAC Guard (@role_required: STUDENT | MANAGER | ADMIN)
  │  Authorized session
  ▼
View Controllers (backend/core/views.py)
  │  Atomic transaction + Pessimistic lock (select_for_update)
  ▼
ORM Domain Layer (backend/core/models.py)
  │  Allen's Interval Algebra: (s1 < e2) ∧ (e1 > s2)
  ▼
Storage Engine (SQLite WAL Mode, 3NF Schema, Composite B-Tree Indexes)
```

---

## 60-Second Quickstart

```bash
# 1. Clone repository and activate environment
cd "Campus sharing/Project"
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Install dependencies & run migrations
pip install django qrcode pillow
python3 manage.py migrate

# 3. Seed demo hardware inventory and pre-configured accounts
python3 manage.py seed_data

# 4. Boot the server
python3 manage.py runserver
```

Open **`http://127.0.0.1:8000`** in your browser.

---

## Pre-Configured Demo Credentials

The seed command creates three pre-configured accounts. Quick-fill buttons on `/login/` populate these credentials in one click.

| Role | Username | Password | Accessible Endpoints | Primary Capability |
| :--- | :--- | :--- | :--- | :--- |
| **Student** | `student1` | `pass123` | `/`, `/dashboard/`, `/reserve/<id>/` | Browse catalog, submit booking requests, view active custody countdowns, render dynamic Base64 QR pickup/return pass. |
| **Lab Manager** | `manager1` | `pass123` | `/management/dashboard/`, `/management/circulation-log/` | Review pending queues, approve/reject bookings, issue physical equipment (`Issue Gear`), process returns, export audit logs to CSV. |
| **Super Admin** | `admin` | `admin123` | `/admin/`, and all endpoints above | Direct ORM administration, user role provisioning, hardware inventory commissioning, penalty adjustments. |

*Note: A secondary student account (`kai` / `student123`) is also provisioned to test multi-user booking collisions.*

---

## Core Engineering Decisions

### 1. Conflict Prevention via Allen's Interval Algebra
Overlapping temporal intervals $[s_1, e_1)$ and $[s_2, e_2)$ collide if and only if:

$$\text{Overlap}(I_1, I_2) \iff (s_1 < e_2) \land (e_1 > s_2)$$

Rather than relying on application-level loops or error-prone range checks (`start <= other_start <= end`), the collision check is evaluated directly in the database query planner via a composite B-tree index `[item, start_time, end_time]`:

```python
# backend/core/models.py
overlapping_reservations = Reservation.objects.filter(
    item=self.item,
    start_time__lt=self.end_time,
    end_time__gt=self.start_time,
    status__in=[self.Status.APPROVED, self.Status.ACTIVE],
)
if self.pk:
    overlapping_reservations = overlapping_reservations.exclude(pk=self.pk)

consumed_quantity = overlapping_reservations.aggregate(
    total=Sum('quantity')
)['total'] or 0

if (consumed_quantity + self.quantity) > self.item.total_quantity:
    available = max(0, self.item.total_quantity - consumed_quantity)
    raise ValidationError(f"Inventory conflict: Only {available} unit(s) available for this window.")
```

- **5-Minute Clock Drift Buffer**: `self.start_time < (timezone.now() - timedelta(minutes=5))` accommodates client/server clock skew on booking creation.
- **`self._state.adding` Guard**: Validation applies only on insertion, ensuring existing records can be inspected or transitioned without false rejections once the start time passes.
- **14-Day Bound**: Bookings are capped at 14 consecutive days to prevent indefinite resource locks.

### 2. Concurrency Control via Pessimistic Row Locking
To eliminate race conditions when multiple users book the final available unit simultaneously, booking and dispatch workflows use atomic transactions with row locks:

```python
# backend/core/views.py
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
    reservation.save()  # Triggers self.full_clean()
```

`select_for_update()` forces incoming concurrent transactions for the same item to serialize. The first thread commits; the second thread re-evaluates the updated allocation pool, detects zero remaining capacity, and raises a `ValidationError`.

### 3. Immutable Circulation Audit Trails
State transitions are permanently logged to an append-only ledger (`CirculationLog`). Referential integrity is preserved via `SET_NULL`:

```python
# backend/core/models.py
class CirculationLog(models.Model):
    reservation = models.ForeignKey(Reservation, on_delete=models.CASCADE, related_name='circulation_logs')
    actor = models.ForeignKey(CustomUser, on_delete=models.SET_NULL, null=True, blank=True)
    action_type = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    note = models.TextField(blank=True)
```

If a user or staff account is later purged, historical audit records remain intact with `actor=NULL` (recorded as `System Engine`), preserving chain-of-custody integrity for institutional audits.

---

## Route Registry & Permissions

| URL Pattern | Method | Access Level | Description |
| :--- | :---: | :--- | :--- |
| `/` | `GET` | Public | Item catalog with real-time unallocated inventory counts. |
| `/login/` | `GET`, `POST` | Anonymous | Authentication view with click-to-fill demo accounts. |
| `/logout/` | `POST` | Authenticated | Session invalidation and redirect to login. |
| `/reserve/<item_id>/` | `GET`, `POST` | Authenticated | Modal booking form. Enforces 14-day limits and interval math. |
| `/dashboard/` | `GET` | `STUDENT` | Active loan monitor, deadline countdowns, Base64 QR pass. |
| `/management/dashboard/` | `GET` | `MANAGER`, `ADMIN` | Desk console with filter tabs (`all`, `pending`, `active`, `overdue`). |
| `/management/reservation/<pk>/approve/` | `POST` | `MANAGER`, `ADMIN` | Transition reservation: `PENDING` &rarr; `APPROVED`. |
| `/management/reservation/<pk>/checkout/` | `POST` | `MANAGER`, `ADMIN` | Transition reservation: `APPROVED` &rarr; `ACTIVE` (Issues gear). |
| `/management/reservation/<pk>/return/` | `POST` | `MANAGER`, `ADMIN` | Transition reservation: `ACTIVE`/`OVERDUE` &rarr; `RETURNED`. |
| `/management/reservation/<pk>/reject/` | `POST` | `MANAGER`, `ADMIN` | Rejects booking request and restores inventory capacity. |
| `/management/circulation-log/` | `GET` | `MANAGER`, `ADMIN` | Audit trail with metric counters and CSV export (`?export=csv`). |
| `/admin/` | `GET`, `POST` | `ADMIN` (`is_staff`) | Django superuser administration portal. |

---

## Directory Layout

```
.
├── backend/
│   ├── config/               # Application configuration
│   │   ├── settings.py       # Two-tier paths, security headers, auth settings
│   │   ├── urls.py           # Root URL dispatcher
│   │   ├── wsgi.py           # WSGI entrypoint
│   │   └── asgi.py           # ASGI entrypoint
│   └── core/                 # Domain logic & services
│       ├── models.py         # CustomUser, Item, Reservation, CirculationLog
│       ├── views.py          # Atomic view controllers & RBAC decorators
│       ├── urls.py           # Application routes
│       ├── qr_utils.py       # Zero-dependency Base64 QR generator
│       ├── tests.py          # 46 automated unit tests
│       └── management/       # seed_data command
│
├── frontend/
│   ├── templates/            # HTML5 + Django Template Language
│   │   ├── base.html         # Frame: Navbar, notification center, toast stack
│   │   ├── catalog.html      # Hardware grid with real-time stock
│   │   ├── reserve_item.html # Timeframe selector modal
│   │   ├── student_dashboard.html # Custody progress & QR passes
│   │   ├── admin_dashboard.html   # Desk queues & dispatch buttons
│   │   ├── circulation_log.html   # Audit ledger table & CSV export
│   │   └── login.html        # Authentication card with quick credentials
│   └── static/               # CSS, JS, and image assets
│
├── docs/
│   └── SYSTEM_DESIGN_DOSSIER.md # Formal specifications (Thesis Ch. 3 & 4)
├── manage.py                 # CLI driver configured for backend/ tier
└── db.sqlite3                # Transactional database
```

---

## Test Suite & Verification

The test suite validates data models, interval conflict detection, pessimistic locking, RBAC route gating, and audit trail immutability:

```bash
python3 manage.py test backend.core
```

```
Found 46 test(s).
Creating test database for alias 'default'...
System check identified no issues (0 silenced).
..............................................
----------------------------------------------------------------------
Ran 46 tests in 124.817s

OK
Destroying test database for alias 'default'...
```

### Coverage Breakdown
- **User Roles & RBAC (6 tests)**: Role defaults, student ID uniqueness, manager console gating, student loan isolation.
- **Item & Category Integrity (5 tests)**: Deletion protection (`ON DELETE PROTECT`), default quantities, soft-delete filtering.
- **Allen's Interval Algebra (14 tests)**: Abutting intervals (permitted), partial overlaps (blocked), complete enclosures (blocked), multi-unit capacity pools, past-time buffers, 14-day bounds.
- **Circulation Lifecycle (8 tests)**: `PENDING` &rarr; `APPROVED` &rarr; `ACTIVE` &rarr; `RETURNED` state transitions and actor logging.
- **Pessimistic Concurrency (7 tests)**: Rollback on error, `select_for_update` row lock isolation, IDOR scoping, CSRF verification.
- **QR Utilities & CSV Export (6 tests)**: Pure-Python Base64 QR encoding, finder pattern matrix generation, RFC 4180 CSV export compliance.
