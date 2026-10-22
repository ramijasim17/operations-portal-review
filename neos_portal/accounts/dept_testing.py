"""Helpers for tests: Level 1 / 2 people only see their own department, so test users and test records need one."""
from action_tracker.models import Action
from departments.models import Department
from personnel.models import Personnel


def test_department(name="Test dept"):
    return Department.objects.get_or_create(name=name, defaults={"location": "Test"})[0]


def link_department(user, dept=None):
    """Put the user in a department by giving them a Personnel record that names it."""
    dept = dept or test_department()
    person, _ = Personnel.objects.get_or_create(employee_id=f"T-{user.username}", defaults={"name": f"Tester {user.username}", "status": "Inactive"})
    person.department = dept.name
    person.save()
    from accounts.models import Profile

    Profile.objects.filter(user=user).update(employee=person)          # not .save(): the cached profile may hold an old role
    user._state.fields_cache.pop("profile", None)                      # so user.profile is read again, with the new link
    return dept


def make_action(**kwargs):
    kwargs.setdefault("department", test_department())
    return Action.objects.create(**kwargs)
