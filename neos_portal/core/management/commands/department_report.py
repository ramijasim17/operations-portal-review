"""Shows what is not ready for DEPARTMENT_LOCK: people whose written department matches no Department, accounts not linked
to a person, Level 1 / 2 accounts that would see nothing, and records that have no department.

    python manage.py department_report
"""
from django.core.management.base import BaseCommand

from accounts.models import Profile
from action_tracker.models import Action
from departments.models import Department
from maintenance.models import MaintenanceRecord
from personnel.models import Personnel


class Command(BaseCommand):
    help = "List what must be fixed before turning DEPARTMENT_LOCK on."

    def handle(self, *args, **options):
        names = {d.name.lower(): d for d in Department.objects.all()}
        out = self.stdout.write
        out(f"Departments: {', '.join(d.name for d in Department.objects.all()) or 'none'}")

        bad = [p for p in Personnel.objects.filter(status="Active") if (p.department or "").strip().lower() not in names]
        out(f"\nActive people whose department matches no Department ({len(bad)}):")
        for p in bad:
            out(f"  {p.name} (ID {p.employee_id}): written as '{p.department or '(empty)'}'")

        out("\nLevel 1 / 2 accounts that would see nothing:")
        count = 0
        for prof in Profile.objects.filter(role__in=("viewer", "editor"), user__is_active=True).select_related("user", "employee"):
            dept = (prof.employee.department if prof.employee else "").strip().lower()
            if dept not in names:
                count += 1
                why = "not linked to a person" if not prof.employee else f"person's department is '{prof.employee.department or '(empty)'}'"
                out(f"  {prof.user.username} ({prof.get_role_display()}): {why}")
        if not count:
            out("  none")

        no_dept = Action.objects.filter(department__isnull=True).count()
        out(f"\nActions with no department: {no_dept} (only Level 3 / Admin see these until a department is set)")
        from departments.models import Asset

        assets = set(Asset.objects.values_list("name", flat=True))
        unknown = [r for r in MaintenanceRecord.objects.all() if r.asset_serial not in assets]
        out(f"Maintenance jobs whose equipment is not in Departments: {len(unknown)}")
