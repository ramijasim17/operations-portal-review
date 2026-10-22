"""Give every existing action a department where one can be worked out, so Level 1 / 2 people keep seeing theirs:
first from the equipment of the maintenance job it came from, then from the QHSE record it came from, then from the
department written on the Personnel record of the person it is assigned to. The rest stay without a department (only
Level 3 and Admin see those until someone sets one)."""
from django.db import migrations


def backfill(apps, schema_editor):
    Action = apps.get_model("action_tracker", "Action")
    Asset = apps.get_model("departments", "Asset")
    Department = apps.get_model("departments", "Department")
    Personnel = apps.get_model("personnel", "Personnel")
    Record = apps.get_model("maintenance", "MaintenanceRecord")
    Incident = apps.get_model("qhse", "Incident")
    Inspection = apps.get_model("qhse", "Inspection")
    Investigation = apps.get_model("qhse", "Investigation")
    SiteVisitItem = apps.get_model("qhse", "SiteVisitItem")

    todo = {a.pk: a for a in Action.objects.filter(department__isnull=True)}
    if not todo:
        return
    found = {}
    asset_dept = dict(Asset.objects.values_list("name", "classification__department_id"))
    for rec in Record.objects.prefetch_related("actions"):
        dept = asset_dept.get(rec.asset_serial)
        for a in rec.actions.all():
            if dept and a.pk in todo:
                found.setdefault(a.pk, dept)
    for model in (Incident, Inspection, Investigation):
        for obj in model.objects.exclude(department__isnull=True).prefetch_related("actions"):
            for a in obj.actions.all():
                if a.pk in todo:
                    found.setdefault(a.pk, obj.department_id)
    for item in SiteVisitItem.objects.exclude(action__isnull=True).select_related("visit"):
        if item.action_id in todo and item.visit.department_id:
            found.setdefault(item.action_id, item.visit.department_id)
    names = {n.lower(): pk for n, pk in Department.objects.values_list("name", "pk")}
    people = {n: d.strip().lower() for n, d in Personnel.objects.exclude(department="").values_list("name", "department")}
    for pk, action in todo.items():
        dept = found.get(pk)
        if dept is None:
            written = people.get((action.assignee or "").strip())
            dept = names.get(written) if written else None
        if dept:
            Action.objects.filter(pk=pk).update(department_id=dept)


class Migration(migrations.Migration):
    dependencies = [
        ("action_tracker", "0006_action_department"),
        ("maintenance", "0003_maintenancerecord_actions"),
        ("qhse", "0004_audittemplate_department_incident_department_and_more"),
        ("personnel", "0001_initial"),
        ("departments", "0001_initial"),
    ]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
