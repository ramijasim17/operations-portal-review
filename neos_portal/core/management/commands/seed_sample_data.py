"""Fills an EMPTY database with made-up sample data, so the portal can be shown to testers without
any real names, serial numbers, clients or files.   python manage.py seed_sample_data --admin-password-file FILE"""
import datetime
import io
import secrets

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import Profile
from notifications.middleware import _actor

COURSES = ["H2S", "First Aid", "Fire Fighting", "Lifting Supervisor", "Working at Height"]
POSITIONS = ["Slickline Supervisor", "Wireline Operator", "WL/SL Operator", "Trainee Operator", "Crew Chief", "QHSE Assistant"]


def png(text):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (480, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 470, 290), outline="black", width=3)
    draw.text((30, 130), text, fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return ContentFile(out.getvalue(), name="sample.png")


class Command(BaseCommand):
    help = "Fill an empty database with sample (made-up) data for testers."

    def add_arguments(self, parser):
        parser.add_argument("--admin-password-file", required=True, help="Where to write the generated admin login.")

    def handle(self, *args, **options):
        from departments.models import Asset, AssetCert, CertType, Classification, ClassificationCertRequirement, Department
        from personnel.models import Personnel, PersonnelCert, TrainingCourse

        User = get_user_model()
        if Personnel.objects.exists() or Asset.objects.exists():
            raise CommandError("This database already has data; run the seed on an empty one.")
        today = timezone.localdate()
        zone = timezone.get_current_timezone()
        at = lambda d, h, m=0: timezone.make_aware(datetime.datetime.combine(d, datetime.time(h, m)), zone)

        # ── the admin the organiser logs in with ─────────────────────────────────────────────────
        password = secrets.token_urlsafe(9)
        admin = User.objects.create_user(username="testadmin", email="testadmin@example.com", password=password)
        Profile.objects.update_or_create(user=admin, defaults={"role": "admin"})
        demo = User.objects.create_user(username="demo_level2", email="demo@example.com")      # no password: only the author of the pending samples
        demo.set_unusable_password()
        demo.save()
        Profile.objects.update_or_create(user=demo, defaults={"role": "editor"})
        demo = User.objects.get(pk=demo.pk)

        # ── shared lists ──────────────────────────────────────────────────────────────────────────
        from wire_management.models import Client, FieldWell, Location
        for name in ("Client A", "Client B", "Client C"):
            Client.objects.create(name=name)
        for name in ("North Field", "South Field"):
            FieldWell.objects.create(name=name)
        for name in ("Workshop", "Rig 12", "Rig 7", "Well pad 3"):
            Location.objects.create(name=name)

        # ── departments, classifications, assets, certificates ───────────────────────────────────
        wl = Department.objects.create(name="Wireline & Slickline", location="Main yard")
        wt = Department.objects.create(name="Well Testing", location="Test base")
        certs = {n: CertType.objects.create(name=n) for n in ("Pressure test", "Thorough examination", "NDT inspection")}
        plan = [
            (wl, "Units", True, "UNIT", 4, ["Pressure test"]),
            (wl, "Lubricators", False, "LUB", 12, ["Pressure test", "NDT inspection"]),
            (wl, "Toolstrings", False, "TS", 15, ["Thorough examination"]),
            (wl, "Slings & lifting", False, "SL", 20, ["Thorough examination"]),
            (wt, "Gauges & panels", False, "GP", 10, ["Pressure test"]),
            (wt, "Separators", False, "SEP", 4, ["Pressure test", "NDT inspection"]),
        ]
        spread = [-25, 12, 200, 340, -3, 28, 150, None, 90, 400]       # expired / soon / valid / no certificate yet
        classes, assets = {}, {}
        for dept, cname, is_unit, prefix, count, required in plan:
            c = Classification.objects.create(department=dept, name=cname, is_unit=is_unit)
            classes[cname] = c
            for ct in required:
                ClassificationCertRequirement.objects.create(classification=c, cert_type=certs[ct])
            for i in range(1, count + 1):
                asset = Asset.objects.create(classification=c, name=f"{prefix}-{i:02d}", description=f"Sample {cname.lower()} {i}")
                assets[asset.name] = asset
                for j, ct in enumerate(required):
                    offset = spread[(i + j) % len(spread)]
                    if offset is not None:
                        AssetCert.objects.create(asset=asset, cert_type=certs[ct], cert_number=f"C-{prefix}{i:02d}{j}",
                                                 issue_date=today + datetime.timedelta(days=offset - 365), expiry_date=today + datetime.timedelta(days=offset))

        # ── personnel: IDs T001-T025 are the ones testers sign up with ───────────────────────────
        courses = [TrainingCourse.objects.create(course_name=n) for n in COURSES]
        people = []
        for n in range(1, 26):
            p = Personnel.objects.create(employee_id=f"T{n:03d}", name=f"Tester {n:02d}", position=POSITIONS[n % len(POSITIONS)],
                                         department="WL/SL" if n % 3 else "Well Testing", status="Active")
            required = courses[: 2 + n % 4]
            p.required_training_courses.set(required)
            for k, course in enumerate(required):
                offset = spread[(n + k) % len(spread)]
                if offset is not None:
                    PersonnelCert.objects.create(personnel=p, course=course, expiry_date=today + datetime.timedelta(days=offset))
            people.append(p)

        # ── manifests, jobs, supervisors ──────────────────────────────────────────────────────────
        from job_manager.models import JobManifest
        m1 = JobManifest.objects.create(job_name="Wireline job A-12", client="Client A", date=today - datetime.timedelta(days=20), location="Rig 12",
                                        status="Active", department=wl, equipment_serials="UNIT-01,UNIT-02,LUB-01,LUB-02,TS-01,TS-02")
        m1.dispatch_equipment()
        m2 = JobManifest.objects.create(job_name="Slickline job B-3", client="Client B", date=today + datetime.timedelta(days=5), location="Rig 7",
                                        status="Draft", department=wl, equipment_serials="SL-01,SL-02,TS-05")
        token = _actor.set(demo)                       # made by a Level 2: these wait for approval
        try:
            JobManifest.objects.create(job_name="New job C-1 (waiting for approval)", client="Client C", date=today + datetime.timedelta(days=12),
                                       location="Well pad 3", status="Draft", department=wl)
            Asset.objects.create(classification=classes["Slings & lifting"], name="SL-21", description="New sling (waiting for approval)")
            Personnel.objects.create(employee_id="T099", name="Tester 99", position="Trainee Operator", department="WL/SL", status="Active")
        finally:
            _actor.reset(token)

        from job_reports.models import JobReport, JobSupervisor
        from sgs_report.models import Battery, Gauge, SGSReport, SGSSequenceEntry, SGSShift, SGSStation
        jr1 = JobReport.objects.create(manifest=m1, name="Well A-12 static gradient survey", needs_survey_report=True, survey_report_type="SGS")
        jr2 = JobReport.objects.create(manifest=m1, name="Well A-14 wireline", callout_start=today - datetime.timedelta(days=12), callout_end=today - datetime.timedelta(days=9))
        jr3 = JobReport.objects.create(manifest=m2, name="Well B-3 slickline")
        for jr, who, shift, start, end in ((jr1, people[1], "day", 19, 15), (jr1, people[4], "night", 19, 15), (jr2, people[2], "both", 12, 9)):
            JobSupervisor.objects.create(job_report=jr, person=who, shift=shift, start_date=today - datetime.timedelta(days=start),
                                         end_date=today - datetime.timedelta(days=end))

        # ── one SGS report with stations and events ───────────────────────────────────────────────
        batt1, batt2 = Battery.objects.create(serial_number="B-S1"), Battery.objects.create(serial_number="B-S2", used_before_mah=1500)
        g1 = Gauge.objects.create(serial_number="D9001", battery=batt1)
        g1.calibration_certificate.save("cal-d9001.png", png("Sample calibration certificate D9001"))
        g2 = Gauge.objects.create(serial_number="D9002", battery=batt2)
        g2.calibration_certificate.save("cal-d9002.png", png("Sample calibration certificate D9002"))
        day = today - datetime.timedelta(days=18)
        report = SGSReport.objects.create(
            well_name="A-12", client="Client A", field="North Field", report_type="SGS", test_type="Static Gradient Survey",
            company="Client A", rig="Rig 12", country_city="Sample City", run_start=at(day, 9), run_end=at(day, 15),
            bottom_gauge=g1, top_gauge=g2, bottom_gauge_connected_at=at(day, 8, 30), bottom_gauge_disconnected_at=at(day, 15, 20),
            top_gauge_connected_at=at(day, 8, 40), top_gauge_disconnected_at=at(day, 15, 10), company_representative="Client rep", neos_representative="Tester 02")
        report.record_battery_usage()
        jr1.linked_sgs_report = report
        jr1.save()
        for i, (depth, h, m, p, t) in enumerate([(2465, 10, 0, 2905.3, 174.2), (2000, 10, 40, 2860.4, 170.0), (1500, 11, 30, 2790.9, 160.2),
                                                 (1000, 12, 20, 1375.2, 120.8), (500, 13, 10, 810.4, 101.2), (0, 14, 0, 770.1, 88.0)], 1):
            SGSStation.objects.create(report=report, step_no=i, md_mgl=depth, start_datetime=at(day, h, m), end_datetime=at(day, h, m + 9),
                                      pressure_psia=p, temp_value=t)
        shift = SGSShift.objects.create(report=report, label=f"Day shift {day:%d-%m-%Y}", shift_date=day, order=1)
        for i, (a, b, text) in enumerate([((8, 30), (9, 0), "Arrive at location, safety meeting, rig-up"), ((9, 0), (10, 0), "RIH gauges to 2465 m"),
                                         ((10, 0), (14, 10), "Static gradient stops as per program"), ((14, 10), (15, 0), "POOH"), ((15, 0), (15, 20), "Rig-down")], 1):
            SGSSequenceEntry.objects.create(shift=shift, sr=str(i), start_datetime=at(day, *a), end_datetime=at(day, *b), description=text, order=i)
        from approvals.models import Approval
        from django.contrib.contenttypes.models import ContentType
        Approval.objects.create(content_type=ContentType.objects.get_for_model(report), object_id=report.pk, kind="sgs_result",
                                status="approved", auto=True, label="A-12", decided_by=admin, decided_at=timezone.now())

        # ── actions, maintenance, NPT ─────────────────────────────────────────────────────────────
        from action_tracker.models import Action
        names = [p.name for p in people]
        for i, (text, kind, client, status, due, raised) in enumerate([
            ("Replace the O-rings on lubricator LUB-03", "equipment", "Client A", "Pending", 5, 6),
            ("Send the calibration certificates to the client", "documentation", "Client A", "In Progress", -2, 14),
            ("Refresher H2S course for the night crew", "personnel", "ABC internal", "Pending", 20, 3),
            ("Update the lifting plan for Rig 7", "procedure", "Client B", "Completed", -5, 30),
            ("Quote for the extra pressure test", "commercial", "Client C", "Pending", 9, 2),
            ("Investigate the pressure drift on gauge D9002", "equipment", "Client A", "In Progress", 3, 8),
        ], 1):
            a = Action.objects.create(action_item=text, assignee=names[i], priority=["High", "Medium", "Low"][i % 3], due_date=today + datetime.timedelta(days=due),
                                      client=client, action_type=kind, raised_date=today - datetime.timedelta(days=raised), job_report=jr1 if i < 3 else None)
            if status == "Completed":
                Action.objects.filter(pk=a.pk).update(status="Completed", closed_date=today - datetime.timedelta(days=raised - 6))
            elif status == "In Progress":
                Action.objects.filter(pk=a.pk).update(status="In Progress")

        from maintenance.models import MaintenanceLevel, MaintenanceRecord
        levels = [MaintenanceLevel.objects.create(level_name=n, interval_count=c, interval_unit="month") for n, c in (("Level 1", 1), ("Level 2", 3), ("Level 3", 6))]
        for i, serial in enumerate(["UNIT-01", "UNIT-02", "LUB-01", "LUB-02", "LUB-03", "GP-01"]):
            MaintenanceRecord.objects.create(asset_serial=serial, schedule="levelled", maintenance_level=levels[i % 3],
                                             due_date=today + datetime.timedelta(days=[-6, 4, 25, 60, -1, 14][i]))
        MaintenanceRecord.objects.create(asset_serial="LUB-04", activity="pressure_test", schedule="repeating", times_per=2, every_count=1,
                                         every_unit="month", place="yard", due_date=today + datetime.timedelta(days=7))

        from sqb.models import ClientNPTSetting, NPTKPI, SQBTrackerEntry
        units = [assets["UNIT-01"], assets["UNIT-02"], assets["UNIT-03"]]
        setting = ClientNPTSetting.objects.create(client="Client A", basis="monthly_24")
        setting.units.set(units)
        NPTKPI.objects.create(name="Client A NPT %", metric="percent", target=3, client="Client A")
        for i, (back, hours, cat) in enumerate([(60, 5, "equipment"), (45, 2.5, "personnel"), (33, 8, "equipment"), (20, 3, "documents"), (12, 6, "equipment"), (6, 1.5, "personnel")]):
            SQBTrackerEntry.objects.create(date=today - datetime.timedelta(days=back), unit=units[i % 3], job_report=jr1, client="Client A",
                                           category=cat, job_ticket=jr1.name, npt_hours=hours, description="Sample NPT entry")
        from notifications.models import Notification
        Notification.objects.all().delete()                # the seeding itself is not news
        with open(options["admin_password_file"], "w") as f:
            f.write("Sign in to the tester site as an admin with:\n"
                    f"  username: testadmin\n  password: {password}\n\n"
                    "Testers create their own accounts on the sign-up page with an Employee ID from T001 to T025 and the name 'Tester 01' ... 'Tester 25' (the same number).\n"
                    "New accounts start at Level 1; raise them under Access Control.\n")
        self.stdout.write(self.style.SUCCESS("Sample data created."))
