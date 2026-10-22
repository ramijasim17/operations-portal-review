"""Departments: which department's records a person sees in Action Tracker, QHSE, Maintenance and Personnel.

  * Level 1 and Level 2 are locked to their own department -- the one written on their Personnel record. If the account
    is not linked to a department, they see nothing (and a message says why).
  * Level 3 and Admin start on all departments and may pick one with the Department box; the choice is remembered for
    the session and applies to all four tabs.
"""
from dataclasses import dataclass

from django.db.models import Q
from django.http import Http404

from departments.models import Asset, Department

ALL, NONE, DEPT, BLOCKED = "all", "none", "dept", "blocked"
RESTRICTED_ROLES = ("viewer", "editor")           # Level 1 and Level 2
SESSION_KEY = "dept_scope"


@dataclass
class Scope:
    mode: str                           # all | none (records with no department) | dept | blocked
    department: object = None
    locked: bool = False                # a Level 1 / 2 person: cannot choose

    @property
    def key(self):
        return str(self.department.pk) if self.mode == DEPT else self.mode

    @property
    def label(self):
        if self.mode == DEPT:
            return self.department.name
        return {NONE: "No department", BLOCKED: "No department linked"}.get(self.mode, "All departments")

    @property
    def name(self):
        """The department name as written on Personnel records (empty when it does not apply)."""
        return self.department.name if self.mode == DEPT else ""


def is_restricted(user):
    """Level 1 / 2 are locked to their department -- once the DEPARTMENT_LOCK switch is on (see settings)."""
    from django.conf import settings

    if not getattr(settings, "DEPARTMENT_LOCK", True):
        return False
    profile = getattr(user, "profile", None)
    return bool(profile) and profile.role in RESTRICTED_ROLES


def user_department(user):
    """The Department that matches the department written on the person's Personnel record, if any."""
    profile = getattr(user, "profile", None)
    person = getattr(profile, "employee", None)
    name = (getattr(person, "department", "") or "").strip()
    return Department.objects.filter(name__iexact=name).first() if name else None


def get_scope(request):
    cached = getattr(request, "_dept_scope", None)
    if cached is not None:
        return cached
    user = request.user
    if is_restricted(user):
        dept = user_department(user)
        scope = Scope(DEPT, dept, locked=True) if dept else Scope(BLOCKED, locked=True)
    else:
        raw = request.GET.get("dept")
        if raw is None:
            raw = request.session.get(SESSION_KEY)
        if raw is None:
            raw = ALL                       # Level 3 / Admin start on every department
        scope = Scope(ALL)
        if raw == NONE:
            scope = Scope(NONE)
        elif raw.isdigit():
            dept = Department.objects.filter(pk=int(raw)).first()
            scope = Scope(DEPT, dept) if dept else Scope(ALL)
        if request.session.get(SESSION_KEY) != scope.key:
            request.session[SESSION_KEY] = scope.key
    request._dept_scope = scope
    return scope


def initial_department(request):
    """What a new record's Department box starts on: the department being looked at, else the person's own."""
    scope = get_scope(request)
    return scope.department if scope.mode == DEPT else user_department(request.user)


def filter_qs(qs, scope, field="department", include_shared=False):
    """Limit a queryset to the department. include_shared=True also keeps records with no department (for example audit
    templates that every department can use)."""
    if scope.mode == BLOCKED:
        return qs.none()
    if scope.mode == DEPT:
        cond = Q(**{field: scope.department})
        return qs.filter(cond | Q(**{f"{field}__isnull": True})) if include_shared else qs.filter(cond)
    if scope.mode == NONE:
        return qs.filter(**{f"{field}__isnull": True})
    return qs


def matches(scope, department_id, shared=False):
    """The same rule for things worked out in Python (maintenance jobs by their equipment, for example)."""
    if scope.mode == BLOCKED:
        return False
    if scope.mode == DEPT:
        return department_id == scope.department.pk or (shared and department_id is None)
    if scope.mode == NONE:
        return department_id is None
    return True


def visible_or_404(request, department_id, shared=False):
    """Object-level check for a single record: a Level 1 / 2 person opening a link to another department's record gets
    'not found', exactly as if it did not exist. Level 3 and Admin may open anything."""
    if not is_restricted(request.user):
        return
    if not matches(get_scope(request), department_id, shared):
        raise Http404()


def asset_department_map(serials):
    return dict(Asset.objects.filter(name__in=set(serials)).values_list("name", "classification__department_id"))


def personnel_filter(qs, scope):
    """Personnel keep their department as text, so match it to the Department's name (capital letters do not matter)."""
    if scope.mode == BLOCKED:
        return qs.none()
    if scope.mode == DEPT:
        return qs.filter(department__iexact=scope.department.name)
    if scope.mode == NONE:                      # people whose written department is empty or matches no Department
        cond = Q(pk__in=[])
        for name in Department.objects.values_list("name", flat=True):
            cond |= Q(department__iexact=name)
        return qs.exclude(cond)
    return qs


def asset_serials_for(scope):
    """Asset No / Serial No values that belong to the department being looked at (None = no limit)."""
    if scope.mode == DEPT:
        return set(Asset.objects.filter(classification__department=scope.department).values_list("name", flat=True))
    if scope.mode == BLOCKED:
        return set()
    return None


def filter_by_serial(qs, scope, field="asset_serial"):
    """Things that point at equipment by its serial (maintenance jobs) belong to the equipment's department."""
    if scope.mode == BLOCKED:
        return qs.none()
    if scope.mode == DEPT:
        return qs.filter(**{f"{field}__in": Asset.objects.filter(classification__department=scope.department).values_list("name", flat=True)})
    if scope.mode == NONE:
        return qs.exclude(**{f"{field}__in": Asset.objects.values_list("name", flat=True)})
    return qs


def serial_visible_or_404(request, serial):
    """Object-level check for something that belongs to a piece of equipment."""
    if not is_restricted(request.user):
        return
    allowed = asset_serials_for(get_scope(request))
    if allowed is not None and serial not in allowed:
        raise Http404()


def filter_by_unit(qs, scope, field="unit"):
    """Things recorded against a unit (NPT) belong to the unit's department."""
    if scope.mode == BLOCKED:
        return qs.none()
    if scope.mode == DEPT:
        return qs.filter(**{f"{field}__classification__department": scope.department})
    if scope.mode == NONE:
        return qs.filter(**{f"{field}__isnull": True})
    return qs
