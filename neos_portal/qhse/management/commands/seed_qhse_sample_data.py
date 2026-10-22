"""Fills QHSE with made-up incidents, inspections and corrective actions, for trying the pages out locally.
Run:  python manage.py seed_qhse_sample_data            (only when QHSE is empty)
      python manage.py seed_qhse_sample_data --reset    (wipes QHSE records and its linked actions first)"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from action_tracker.models import Action
from departments.models import Asset, Classification, Department
from maintenance.models import MaintenanceLevel, MaintenanceRecord
from personnel.models import Personnel
from qhse.models import (AuditPart, AuditPoint, AuditTemplate, Incident, Inspection, InspectionAnswer, Investigation,
                         SiteVisit, SiteVisitItem)

T = timezone.localdate


def ago(days):
    return T() - datetime.timedelta(days=days)


INCIDENTS = [
    # days ago, type, severity, title, location, client, status, assigned, description, cause
    (3, "near_miss", "Medium", "Slickline tool string dropped from catwalk (no one below)", "Well Ru-1", "Client A", "Investigating", "Ahmed Khalil",
     "A lubricator section slipped while being rigged up and fell about 2 m onto the deck. The area below was barricaded.", "Sling not rated for the combined weight."),
    (9, "unsafe", "Low", "Hand rail missing on pump skid platform", "Base workshop", "", "Open", "Sara Nasser", "Guard rail removed for repair and not refitted.", ""),
    (14, "environmental", "High", "Hydraulic oil leak on wellsite, about 20 L", "Well G-2", "Client B", "Investigating", "Omar Haddad",
     "Hose failure on the power pack. Spill contained with the drip tray and absorbent pads; soil removed.", "Hose past its inspection date."),
    (21, "injury", "Medium", "Minor hand cut while changing wire clamp (first aid)", "Well G-2", "Client B", "Closed", "Ahmed Khalil",
     "Operator cut a finger on a sharp edge. First aid on site, returned to work.", "Gloves not cut-resistant for this task."),
    (30, "incident", "High", "Vehicle reversed into pressure control equipment trailer", "Yard", "", "Closed", "Sara Nasser",
     "Low-speed contact, damage to trailer guard only. No injuries.", "No banksman, blind spot."),
    (41, "quality", "Low", "Wrong gauge certificate issued with unit", "Document control", "Client A", "Closed", "Omar Haddad", "Certificate for another serial number attached.", "Manual file mix-up."),
    (55, "near_miss", "Low", "Gas detector not bump tested before shift", "Well Ru-1", "Client A", "Closed", "Ahmed Khalil", "Found at shift start, tested and recorded.", "Checklist step missed."),
    (70, "near_miss", "Medium", "Dropped object from derrick board, area barricaded", "Well G-2", "Client B", "Closed", "Omar Haddad", "Tool lanyard missing.", "Lanyards not issued."),
    (98, "incident", "Critical", "Over-pressure on test line during pressure test", "Test bay", "", "Closed", "Sara Nasser",
     "Pressure exceeded the planned test value by 10 percent before the relief valve opened. Area clear, no injury.", "Gauge range too wide for the test."),
    (130, "unsafe", "Medium", "Frayed lifting sling found in use", "Base workshop", "", "Closed", "Ahmed Khalil", "Removed from service.", "Inspection tags overdue."),
]

INSPECTIONS = [
    # title, kind, area, due offset (days from today), done offset or None, result, inspector, repeat months, findings
    ("Monthly yard and workshop inspection", "inspection", "Base workshop", -12, None, "", "Sara Nasser", 1, ""),
    ("Fire extinguisher check, all units", "inspection", "All units", 4, None, "", "Omar Haddad", 3, ""),
    ("Lifting equipment and sling inspection", "inspection", "Base workshop", -3, None, "", "Ahmed Khalil", 6, ""),
    ("Rig-up audit, slickline job", "audit", "Well G-2", 18, None, "", "Sara Nasser", None, ""),
    ("Emergency drill: well control, wellsite", "drill", "Well Ru-1", 40, None, "", "Omar Haddad", 6, ""),
    ("ISO 9001 internal audit", "audit", "Head office", 60, None, "", "Sara Nasser", 12, ""),
    ("Monthly yard and workshop inspection", "inspection", "Base workshop", -42, -40, "Needs improvement", "Sara Nasser", 1,
     "Housekeeping poor near the chemical store. Two missing signs."),
    ("Pressure control equipment inspection", "inspection", "Test bay", -75, -73, "Pass", "Ahmed Khalil", 6, "All items in order. Certificates valid."),
    ("Rig-up audit, wireline job", "audit", "Well G-2", -90, -88, "Fail", "Sara Nasser", None, "Lifting plan missing and gas detector out of date."),
    ("Weekly toolbox talk: dropped objects", "toolbox", "Well Ru-1", -8, -8, "Pass", "Omar Haddad", None, "14 attended. Lanyards demonstrated."),
]

ACTIONS = [
    # kind, index, text, assignee, priority, due offset, status
    ("incident", 0, "Re-rate and tag all lifting slings used for lubricator rig-up", "Sara Nasser", "High", -2, "In Progress"),
    ("incident", 0, "Toolbox talk on lifting plans for all slickline crews", "Omar Haddad", "Medium", 10, "Pending"),
    ("incident", 2, "Inspect and replace power pack hoses past date", "Ahmed Khalil", "High", -5, "Pending"),
    ("incident", 2, "Add hose inspection to monthly yard checklist", "Sara Nasser", "Medium", 12, "Pending"),
    ("incident", 3, "Issue cut-resistant gloves to all wire handling crews", "Ahmed Khalil", "Medium", -20, "Completed"),
    ("incident", 4, "Appoint banksmen and mark reversing routes in the yard", "Sara Nasser", "High", -50, "Completed"),
    ("inspection", 6, "Clear chemical store area and fit missing signs", "Omar Haddad", "Medium", -30, "Completed"),
    ("inspection", 8, "Write a lifting plan template and gas detector register", "Sara Nasser", "High", -60, "Completed"),
    ("inspection", 8, "Re-audit the wireline rig-up after corrections", "Ahmed Khalil", "Medium", 8, "Pending"),
]


SAMPLE_ASSETS = [("PCE-101", "Lubricator assembly 10k"), ("PCE-102", "BOP frame"), ("PWR-201", "Hydraulic power pack"), ("TRK-301", "MAN truck")]
# incident index -> asset, inspection index -> asset
INCIDENT_ASSET = {0: "PCE-101", 2: "PWR-201", 4: "TRK-301", 8: "PCE-102"}
INSPECTION_ASSET = {2: "PCE-101", 7: "PCE-102", 8: "PCE-101"}


def make_assets_and_maintenance():
    """A few pieces of equipment and their maintenance jobs, so the links between the tabs have something to show."""
    wireline, _ = Department.objects.get_or_create(name="Wireline & Slickline", defaults={"location": "Site A - North - Tuba"})
    testing, _ = Department.objects.get_or_create(name="Well Testing", defaults={"location": "Site A - North - Tuba"})
    cls_w, _ = Classification.objects.get_or_create(department=wireline, name="Sample equipment", defaults={"is_unit": True})
    cls_t, _ = Classification.objects.get_or_create(department=testing, name="Sample equipment", defaults={"is_unit": True})
    for name, desc in SAMPLE_ASSETS:
        # the truck belongs to Well Testing; the rest to Wireline & Slickline
        Asset.objects.get_or_create(name=name, defaults={"classification": cls_t if name == "TRK-301" else cls_w, "description": desc})
    level, _ = MaintenanceLevel.objects.get_or_create(level_name="Level 1", defaults={"interval_count": 3, "interval_unit": "month"})
    jobs = [
        MaintenanceRecord.objects.create(asset_serial="PCE-101", schedule="levelled", maintenance_level=level, due_date=T() - datetime.timedelta(days=6)),
        MaintenanceRecord.objects.create(asset_serial="PCE-101", activity="pressure_test", schedule="repeating", every_count=6, every_unit="month",
                                         due_date=T() + datetime.timedelta(days=40), last_done=ago(140), last_result="Pass"),
        MaintenanceRecord.objects.create(asset_serial="PWR-201", schedule="levelled", maintenance_level=level, due_date=T() + datetime.timedelta(days=5)),
        MaintenanceRecord.objects.create(asset_serial="PCE-102", activity="pressure_test", schedule="repeating", every_count=6, every_unit="month",
                                         due_date=T() + datetime.timedelta(days=75), last_done=ago(100), last_result="Fail"),
    ]
    from sqb.models import SQBTrackerEntry

    for serial, hours, cause, days, note in (("PCE-101", 4.5, "equipment", 6, "Lubricator seal leak during rig-up"),
                                             ("PWR-201", 2, "personnel", 11, "Crew change delayed on location"),
                                             ("TRK-301", 6, "equipment", 4, "Hydraulic pump failure on the truck"),
                                             ("PCE-102", 1.5, "documents", 18, "Waiting for permit approval")):
        SQBTrackerEntry.objects.create(date=ago(days), unit=Asset.objects.get(name=serial), npt_hours=hours, category=cause,
                                       client="Client A" if serial != "TRK-301" else "Client B", description=note)
    jobs[3].raise_follow_up("Failed test")                       # what a Fail does automatically
    jobs[2].raise_follow_up("Failure reported")
    return jobs


TEMPLATES = {
    "Rig-up audit (slickline)": ("audit", 80, [
        ("Lifting and rigging", [
            ("Lifting plan available and signed", "Current plan on site, signed by the supervisor.", "ok", True),
            ("Slings and shackles tagged and in date", "Colour code of the current period.", "ok", True),
            ("Lubricator weight within crane rating", "", "ok", False),
            ("Tag line used for suspended loads", "", "ok", False)]),
        ("Pressure control", [
            ("Pressure test of the lubricator", "Test to the rated working pressure for 10 minutes.", "number:psi:3000:3300", True),
            ("Stuffing box and grease head checked", "", "ok", False),
            ("Emergency shut-in drill known by the crew", "", "ok", False)]),
        ("Housekeeping and HSE", [
            ("Work area barricaded", "", "ok", False),
            ("Gas detector bump tested today", "Record kept on site.", "ok", True),
            ("Waste and spill kit in place", "", "ok", False)]),
    ]),
    "Monthly yard inspection": ("inspection", 85, [
        ("Housekeeping", [("Walkways clear", "", "ok", False), ("Chemical store tidy and signed", "", "ok", False)]),
        ("Fire safety", [("Extinguishers charged and in date", "", "ok", True), ("Escape routes unblocked", "", "ok", True)]),
        ("Lifting equipment", [("Cranes and hoists inspected in date", "", "ok", True), ("Slings tagged", "", "ok", False)]),
    ]),
}


def make_templates():
    out = {}
    for name, (kind, mark, parts) in TEMPLATES.items():
        t = AuditTemplate.objects.create(name=name, kind=kind, pass_mark=mark, description="Sample template for testing.")
        for i, (ptitle, points) in enumerate(parts, start=1):
            part = AuditPart.objects.create(template=t, title=ptitle, order=i)
            for j, (text, guidance, atype, critical) in enumerate(points, start=1):
                kwargs = {}
                if atype.startswith("number"):
                    _, unit, lo, hi = atype.split(":")
                    kwargs = {"answer_type": "number", "unit": unit, "min_value": lo, "max_value": hi}
                AuditPoint.objects.create(part=part, text=text, guidance=guidance, critical=critical, order=j, **kwargs)
        out[name] = t
    return out


SAMPLE_PEOPLE = [
    ("S1", "Ahmed Khalil", "Wireline & Slickline", "Supervisor"), ("S2", "Sara Nasser", "Wireline & Slickline", "HSE Officer"),
    ("S3", "Sam Editor", "Wireline & Slickline", "Coordinator"), ("S4", "Omar Haddad", "Well Testing", "Supervisor"),
    ("S5", "Lina Viewer", "Well Testing", "Operator"), ("S6", "Zed Nodept", "", "Operator"),
]


def make_people_and_accounts():
    """Sample people in both departments, and sample accounts: a Level 2 in Wireline, a Level 1 in Well Testing, and a
    Level 2 whose account is not linked to any department (to see the 'nothing to show' message)."""
    from django.contrib.auth import get_user_model

    from accounts.models import Profile, Role

    User = get_user_model()
    people = {}
    for emp, name, dept, position in SAMPLE_PEOPLE:
        people[name], _ = Personnel.objects.get_or_create(employee_id=emp, defaults={"name": name, "department": dept, "position": position})
    accounts = [("sample_editor", Role.EDITOR, "Sam Editor"), ("sample_viewer", Role.VIEWER, "Lina Viewer"), ("sample_nodept", Role.EDITOR, None)]
    for username, role, person in accounts:
        u, created = User.objects.get_or_create(username=username)
        if created:
            u.set_unusable_password()
            u.save()
        Profile.objects.update_or_create(user=u, defaults={"role": role, "employee": people.get(person)})


def make_follow_up(jobs, incidents, user, wl=None, wt=None):
    """Investigations opened from a failed test, an overdue job and an overdue action; and site visit reports."""
    inv1 = Investigation.objects.create(
        title="Failed test: PCE-102", trigger="failed_test", maintenance_record=jobs[3], asset_serial="PCE-102", status="Investigating",
        owner="Ahmed Khalil", due_date=T() + datetime.timedelta(days=4), opened_date=ago(5),
        what_happened="Pressure test of the BOP frame failed: pressure dropped 12 percent in 10 minutes.",
        findings="Seal on the lower flange found worn. Spare seal kit not in stock.", root_cause="Seals not replaced at the last service.")
    inv1.actions.add(*jobs[3].actions.all())
    Investigation.objects.create(
        title="Overdue maintenance: PCE-101", trigger="overdue_job", maintenance_record=jobs[0], asset_serial="PCE-101", status="Open",
        owner="Sara Nasser", due_date=T() + datetime.timedelta(days=7), opened_date=ago(1),
        what_happened="Level 1 service was due on " + (T() - datetime.timedelta(days=6)).strftime("%d %b") + " and has not been done.")
    from sqb.models import SQBTrackerEntry

    big = SQBTrackerEntry.objects.filter(unit__name="TRK-301").first()
    if big:
        Investigation.objects.create(department=wt, title="NPT: TRK-301, 6 h (Base equipment)", trigger="npt", npt_entry=big, asset_serial="TRK-301",
                                     status="Investigating", owner="Omar Haddad", opened_date=ago(2),
                                     what_happened="6 h of non-productive time: hydraulic pump failure on the truck.",
                                     findings="Pump past its service interval.", root_cause="Service interval not tracked for the truck.")
    late = Action.objects.filter(status="Pending", due_date__lt=T()).exclude(maintenance_records__isnull=False).first()
    if late:
        Investigation.objects.create(department=wt, title=f"Overdue action: {late.action_item}"[:200], trigger="overdue_action", action=late, status="Closed",
                                     owner=late.assignee, opened_date=ago(12), what_happened="Action not started.",
                                     findings="Assignee was on leave; no cover.", root_cause="No delegate for approvals.")
    from approvals import services as approvals

    drafts = [
        ("Well G-2", "Client B", "Pre-job safety visit", "Rig-up area was clear. Crew briefed. Two items need follow-up.", 3, [
            ("Wellhead", "good", "", "Barricades and signage in place before rig-up."),
            ("Camp", "finding", "Medium", "Fire extinguisher in the generator room out of date."),
            ("Workshop", "finding", "High", "No lifting plan displayed at the crane.")]),
        ("Well Ru-1", "Client A", "Client HSE walk-through", "Walk-through with the client HSE advisor, no major issues.", 10, [
            ("Wellsite", "observation", "", "Spill kit stocked but not at the pump skid."),
            ("Wellsite", "good", "", "Gas detectors bump tested and logged.")]),
        ("Base yard", "", "Monthly yard visit", "Housekeeping improved since last visit.", 24, [
            ("Chemical store", "good", "", "Store tidy, all containers labelled.")]),
    ]
    editor = None
    from django.contrib.auth import get_user_model
    from accounts.models import Profile, Role

    User = get_user_model()
    editor = User.objects.get(username="sample_editor")
    for n, (site, client, purpose, summary, days, items) in enumerate(drafts):
        v = SiteVisit.objects.create(department=(wt if client == "Client A" else wl), date=ago(days), site=site, client=client, purpose=purpose, summary=summary, visited_by="Sara Nasser",
                                     attendees="Site supervisor, client representative", next_steps="Re-visit in two weeks.", created_by=editor)
        for i, (area, kind, sev, text) in enumerate(items, start=1):
            SiteVisitItem.objects.create(visit=v, area=area, kind=kind, severity=sev, text=text, order=i)
        if n == 1:
            approvals.submit(v, "site_visit", editor)             # waits for a Level 3
        elif n == 2:
            approvals.submit(v, "site_visit", user)               # a Level 3 / admin: approved at once


class Command(BaseCommand):
    help = "Create sample QHSE data for local testing."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing QHSE records first.")

    def handle(self, *args, **opts):
        if opts["reset"]:
            linked = set(Action.objects.filter(qhse_incidents__isnull=False)) | set(Action.objects.filter(qhse_inspections__isnull=False))
            Incident.objects.all().delete()
            Inspection.objects.all().delete()
            Investigation.objects.all().delete()
            SiteVisit.objects.all().delete()
            AuditTemplate.objects.filter(description="Sample template for testing.").delete()
            linked |= set(Action.objects.filter(maintenance_records__isnull=False))
            MaintenanceRecord.objects.filter(asset_serial__in=[a for a, _ in SAMPLE_ASSETS]).delete()
            from sqb.models import SQBTrackerEntry
            SQBTrackerEntry.objects.filter(unit__name__in=[a for a, _ in SAMPLE_ASSETS]).delete()
            Asset.objects.filter(name__in=[a for a, _ in SAMPLE_ASSETS]).delete()
            Department.objects.filter(name="Sample department").delete()
            Personnel.objects.filter(employee_id__in=[p[0] for p in SAMPLE_PEOPLE]).delete()
            for a in linked:
                a.delete()
        elif Incident.objects.exists() or Inspection.objects.exists():
            self.stdout.write("QHSE already has data. Use --reset to start again.")
            return
        make_people_and_accounts()
        jobs = make_assets_and_maintenance()
        wl, wt = Department.objects.get(name="Wireline & Slickline"), Department.objects.get(name="Well Testing")
        templates = make_templates()
        templates["Rig-up audit (slickline)"].department = wl
        templates["Rig-up audit (slickline)"].save()
        incidents = []
        for days, kind, sev, title, loc, client, status, who, desc, cause in INCIDENTS:
            incidents.append(Incident.objects.create(
                date=ago(days), time=datetime.time(8 + days % 9, 15), incident_type=kind, severity=sev, title=title, location=loc,
                client=client, status=status, assigned_to=who, description=desc, root_cause=cause,
                people_involved="Crew on shift", immediate_action="Area made safe, supervisor informed.",
                asset_serial=INCIDENT_ASSET.get(len(incidents), ""),
                department=None if len(incidents) in INCIDENT_ASSET else (wl if len(incidents) % 2 == 0 else wt)))
            if status == "Closed":
                incidents[-1].closed_date = ago(max(days - 6, 1))
                incidents[-1].save()
        inspections = []
        for title, kind, area, due, done, result, who, rep, findings in INSPECTIONS:
            inspections.append(Inspection.objects.create(
                title=title, kind=kind, area=area, due_date=T() + datetime.timedelta(days=due), inspector=who,
                repeat_months=rep, done_date=(T() + datetime.timedelta(days=done)) if done is not None else None,
                result=result, findings=findings, asset_serial=INSPECTION_ASSET.get(len(inspections), ""),
                department=None if len(inspections) in INSPECTION_ASSET else (wl if len(inspections) % 2 == 0 else wt)))
        for kind, idx, text, who, prio, due, status in ACTIONS:
            src = incidents[idx] if kind == "incident" else inspections[idx]
            action = Action.objects.create(
                department=src.department, action_item=f"[{src.reference}] {text}", assignee=who, priority=prio, action_type="procedure",
                due_date=T() + datetime.timedelta(days=due), raised_date=ago(30),
                comments=f"Corrective action from QHSE {src.reference}: {src.title}")
            if status != "Pending":
                action.status = status
                action.save()
            src.actions.add(action)
        # audits worked from templates: one scheduled, one overdue, and one already done with its answers
        inspections[3].template = templates["Rig-up audit (slickline)"]
        inspections[3].scope = "external"
        inspections[3].auditor_company = "Bureau Veritas (sample)"
        inspections[3].save()
        inspections[0].template = templates["Monthly yard inspection"]
        inspections[0].save()
        done = inspections[8]
        done.template = templates["Rig-up audit (slickline)"]
        done.save()
        from qhse.views_audit import _snapshot, _verdict

        _snapshot(done)
        answers = list(done.answers.all())
        for k, a in enumerate(answers):
            a.outcome = "non_compliant" if k in (0, 4) else "compliant"
            if a.answer_type == "number":
                a.value = 2750
                a.outcome = a.judge_value()
            if a.outcome == "non_compliant":
                a.notes = "Found not as required during the audit."
            a.save()
        score, _result, _critical = _verdict(answers, done.template)
        done.score = score
        done.save()
        from django.contrib.auth import get_user_model

        admin = get_user_model().objects.filter(is_superuser=True).first()
        make_follow_up(jobs, incidents, admin, wl, wt)
        self.stdout.write(self.style.SUCCESS(
            f"Created {len(incidents)} incidents, {len(inspections)} inspections and {len(ACTIONS)} corrective actions."))
