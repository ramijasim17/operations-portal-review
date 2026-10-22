"""Maintenance schedules: levels, repeats, one-off, on-failure and pressure tests."""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from departments.models import Asset, Classification, Department
from maintenance.forms import MaintenanceRecordForm
from maintenance.models import (
    MaintenanceLevel, MaintenanceLog, MaintenanceRecord, add_interval, add_months, describe_interval,
)

from wire_management.models import Location

from .dept_testing import link_department
from .models import Profile, Role

D = datetime.date


class IntervalMathTests(TestCase):
    def test_calendar_months_clamp_to_month_end(self):
        self.assertEqual(add_months(D(2027, 1, 31), 1), D(2027, 2, 28))
        self.assertEqual(add_months(D(2028, 1, 31), 1), D(2028, 2, 29))   # leap year
        self.assertEqual(add_months(D(2026, 11, 15), 3), D(2027, 2, 15))

    def test_intervals_from_the_examples(self):
        start = D(2026, 1, 10)
        self.assertEqual(add_interval(start, 1, 1, "month"), D(2026, 2, 10))       # monthly
        self.assertEqual(add_interval(start, 1, 3, "month"), D(2026, 4, 10))       # every 3 months
        self.assertEqual(add_interval(start, 1, 6, "month"), D(2026, 7, 10))       # 6 months
        self.assertEqual(add_interval(start, 1, 1, "year"), D(2027, 1, 10))        # annual
        self.assertEqual(add_interval(start, 2, 1, "week"), start + datetime.timedelta(days=4))   # twice a week
        self.assertEqual(add_interval(start, 2, 1, "month"), start + datetime.timedelta(days=15))  # twice a month

    def test_wording(self):
        self.assertEqual(describe_interval(1, 1, "month"), "Every month")
        self.assertEqual(describe_interval(1, 6, "month"), "Every 6 months")
        self.assertEqual(describe_interval(2, 1, "week"), "2 times per week")
        self.assertEqual(describe_interval(3, 2, "month"), "3 times every 2 months")


class ScheduleTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(name="D", location="L")
        cls = Classification.objects.create(department=dept, name="PCE")
        Asset.objects.create(classification=cls, name="A-1", description="one")
        self.level2 = MaintenanceLevel.objects.create(level_name="Level 2", interval_count=3, interval_unit="month")
        self.today = timezone.localdate()

    def record(self, **kw):
        defaults = {"asset_serial": "A-1"}
        return MaintenanceRecord.objects.create(**(defaults | kw))

    def test_level_uses_the_levels_own_period(self):
        r = self.record(schedule="levelled", maintenance_level=self.level2)
        self.assertEqual(r.schedule_text, "Level 2 · Every 3 months")
        self.assertEqual(r.due_date, add_months(self.today, 3))       # first due one period out
        r.complete(D(2026, 1, 31))
        self.assertEqual(r.due_date, D(2026, 4, 30))
        self.assertEqual((r.last_done, r.status == "done"), (D(2026, 1, 31), False))

    def test_changing_the_level_period_applies_at_next_completion(self):
        r = self.record(schedule="levelled", maintenance_level=self.level2)
        self.level2.interval_count = 6
        self.level2.save()
        r.refresh_from_db()
        r.complete(D(2026, 1, 15))
        self.assertEqual(r.due_date, D(2026, 7, 15))

    def test_repeating_twice_per_week(self):
        r = self.record(schedule="repeating", times_per=2, every_count=1, every_unit="week")
        r.complete(D(2026, 3, 2))
        self.assertEqual(r.due_date, D(2026, 3, 6))
        self.assertEqual(r.schedule_text, "2 times per week")

    def test_one_off_finishes_after_being_done(self):
        r = self.record(schedule="one_off", due_date=D(2026, 5, 1))
        self.assertEqual(r.schedule_text, "Once, on 01 May 2026")
        r.complete(D(2026, 5, 1))
        r.refresh_from_db()
        self.assertEqual((r.is_active, r.due_date, r.status), (False, None, "done"))

    def test_on_failure_stands_by_until_it_fails(self):
        r = self.record(schedule="on_failure")
        self.assertEqual((r.due_date, r.status), (None, "standby"))
        r.report_failure(self.today, notes="pump seized")
        self.assertEqual((r.due_date, r.status), (self.today, "soon"))       # due today, not yet late
        r.report_failure(self.today - datetime.timedelta(days=2))
        self.assertEqual(r.status, "expired")
        r.complete(self.today)
        r.refresh_from_db()
        self.assertEqual((r.due_date, r.status), (None, "standby"))           # back to standby
        self.assertEqual(r.logs.count(), 3)      # two failures + one completion
        self.assertEqual(set(r.logs.values_list("event", flat=True)), {"done", "failure"})

    def test_pressure_test_at_a_location_twice_a_month(self):
        r = self.record(activity="pressure_test", schedule="repeating", times_per=2, every_count=1,
                        every_unit="month", place="location", location_name="Main Field")
        self.assertEqual(r.place_text, "At Main Field")
        r.complete(D(2026, 6, 1), result="Fail")
        self.assertEqual((r.last_result, r.due_date), ("Fail", D(2026, 6, 16)))

    def test_yard_place_text(self):
        self.assertEqual(self.record(schedule="on_failure").place_text, "Yard / workshop")


class FormTests(TestCase):
    def setUp(self):
        dept = Department.objects.create(name="D", location="L")
        Asset.objects.create(classification=Classification.objects.create(department=dept, name="C"), name="A-1")
        self.level = MaintenanceLevel.objects.create(level_name="Level 1", interval_count=1, interval_unit="month")
        self.empty_level = MaintenanceLevel.objects.create(level_name="Level X")

    def form(self, **data):
        base = {"asset_serial": "A-1", "activity": "maintenance", "times_per": 1, "every_unit": "month", "place": "yard"}
        return MaintenanceRecordForm(base | data)

    def test_level_schedule_needs_a_level(self):
        self.assertIn("maintenance_level", self.form(schedule="levelled").errors)

    def test_level_without_a_period_needs_one_entered(self):
        self.assertIn("every_count", self.form(schedule="levelled", maintenance_level=self.empty_level.pk).errors)
        self.assertTrue(self.form(schedule="levelled", maintenance_level=self.empty_level.pk, every_count=2).is_valid())

    def test_level_with_a_period_is_enough(self):
        self.assertTrue(self.form(schedule="levelled", maintenance_level=self.level.pk).is_valid())

    def test_repeating_needs_a_period_and_one_off_needs_a_date(self):
        self.assertIn("every_count", self.form(schedule="repeating").errors)
        self.assertIn("due_date", self.form(schedule="one_off").errors)
        self.assertTrue(self.form(schedule="on_failure").is_valid())

    def test_irrelevant_fields_are_cleared(self):
        Location.objects.create(name="Main Field")
        form = self.form(schedule="on_failure", maintenance_level=self.level.pk, every_count=5,
                         due_date="2030-01-01", location_name="Main Field")
        self.assertTrue(form.is_valid(), form.errors)
        record = form.save()
        self.assertEqual((record.maintenance_level, record.every_count, record.due_date, record.location_name),
                         (None, None, None, ""))

    def test_new_repeating_record_gets_a_first_due_date(self):
        record = self.form(schedule="repeating", every_count=2, every_unit="week").save()
        self.assertEqual(record.due_date, timezone.localdate() + datetime.timedelta(days=14))


class ViewTests(TestCase):
    def setUp(self):
        self.editor = User.objects.create_user(username="ed", password="x")
        Profile.objects.update_or_create(user=self.editor, defaults={"role": Role.EDITOR})
        self.viewer = User.objects.create_user(username="vw", password="x")
        Profile.objects.update_or_create(user=self.viewer, defaults={"role": Role.VIEWER})
        dept = Department.objects.create(name="D", location="L")
        Asset.objects.create(classification=Classification.objects.create(department=dept, name="C"), name="A-1")
        link_department(self.editor, dept)               # Level 1 / 2 see only their own department's equipment
        link_department(self.viewer, dept)
        self.level = MaintenanceLevel.objects.create(level_name="Level 1", interval_count=1, interval_unit="month")
        self.record = MaintenanceRecord.objects.create(
            asset_serial="A-1", schedule="levelled", maintenance_level=self.level,
            due_date=timezone.localdate() - datetime.timedelta(days=3))
        self.client.force_login(self.editor)

    def test_mark_done_moves_the_due_date_and_logs_who(self):
        approver = User.objects.create_user(username="lvl3", password="x")
        Profile.objects.update_or_create(user=approver, defaults={"role": Role.APPROVER})
        self.client.force_login(approver)            # a Level 3's report needs no approval, so the schedule moves at once
        self.client.post(reverse("maintenance:complete", args=[self.record.pk]), {"done_date": "2026-05-10", "notes": "greased"})
        self.record.refresh_from_db()
        self.assertEqual(self.record.due_date, D(2026, 6, 10))
        log = MaintenanceLog.objects.get()
        self.assertEqual((log.by, log.notes), (approver, "greased"))

    def test_a_level_2_report_waits_for_approval_before_the_schedule_moves(self):
        before = self.record.due_date
        self.client.post(reverse("maintenance:complete", args=[self.record.pk]), {"done_date": "2026-05-10", "notes": "greased"})
        self.record.refresh_from_db()
        self.assertEqual(self.record.due_date, before)                      # unchanged until a Level 3 approves
        log = MaintenanceLog.objects.get()
        self.assertEqual((log.by, log.notes), (self.editor, "greased"))

    def test_pressure_test_requires_a_result(self):
        test = MaintenanceRecord.objects.create(asset_serial="A-1", activity="pressure_test", schedule="on_failure")
        response = self.client.post(reverse("maintenance:complete", args=[test.pk]), {"done_date": "2026-05-10"})
        self.assertEqual(response.status_code, 200)              # form re-shown with an error
        self.assertEqual(MaintenanceLog.objects.count(), 0)

    def test_mark_done_form_offers_an_optional_result_for_any_job(self):
        page = self.client.get(reverse("maintenance:complete", args=[self.record.pk])).content.decode()
        self.assertIn('name="result"', page)
        self.client.post(reverse("maintenance:complete", args=[self.record.pk]),
                         {"done_date": "2026-05-10", "result": "Pass"})
        self.assertEqual(MaintenanceLog.objects.get().result, "Pass")

    def test_history_reports_download_as_one_pdf(self):
        import io
        from django.core.files.base import ContentFile
        from pypdf import PdfWriter
        for day in (1, 2):
            buf = io.BytesIO()
            w = PdfWriter(); w.add_blank_page(100, 100); w.write(buf)
            log = MaintenanceLog.objects.create(record=self.record, done_date=D(2026, 5, day))
            log.file.save(f"r{day}.pdf", ContentFile(buf.getvalue()))
        url = reverse("maintenance:history_pdf", args=[self.record.pk])
        response = self.client.get(url)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertEqual(len(__import__("pypdf").PdfReader(io.BytesIO(response.content)).pages), 2)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_history_pdf_without_reports_goes_back(self):
        response = self.client.get(reverse("maintenance:history_pdf", args=[self.record.pk]))
        self.assertRedirects(response, reverse("maintenance:history", args=[self.record.pk]))

    def test_viewer_can_read_history_but_not_mark_done(self):
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(reverse("maintenance:history", args=[self.record.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("maintenance:complete", args=[self.record.pk]),
                                          {"done_date": "2026-05-10"}).status_code, 403)

    def test_every_tab_renders_and_filters(self):
        MaintenanceRecord.objects.create(asset_serial="A-1", activity="pressure_test", schedule="repeating",
                                         times_per=2, every_count=1, every_unit="month")
        for tab in ("all", "levelled", "repeating", "other", "pressure", "done"):
            self.assertEqual(self.client.get(reverse("maintenance:list"), {"tab": tab}).status_code, 200)
        pressure = self.client.get(reverse("maintenance:list"), {"tab": "pressure"}).content.decode()
        self.assertIn("2 times per month", pressure)
        self.assertNotIn("Level 1 ·", pressure)

    def test_dashboard_lists_a_due_levelled_record(self):
        """Regression: the dashboard used a field that doesn't exist on levels."""
        self.assertContains(self.client.get(reverse("core:dashboard")), "A-1 · Level 1")

    def test_finished_one_offs_are_not_counted_as_overdue_on_the_dashboard(self):
        MaintenanceRecord.objects.create(asset_serial="A-1", schedule="one_off", is_active=False,
                                         due_date=timezone.localdate() - datetime.timedelta(days=30))
        html = self.client.get(reverse("core:dashboard")).content.decode()
        self.assertEqual(html.count("Maintenance</span>"), 1)   # only the live record


class LocationListTests(TestCase):
    def test_location_dropdown_uses_the_shared_list(self):
        Location.objects.create(name="Main Field")
        form = MaintenanceRecordForm()
        self.assertEqual([c[0] for c in form.fields["location_name"].choices], ["", "Main Field"])

    def test_only_listed_locations_are_accepted_for_on_site_tests(self):
        dept = Department.objects.create(name="D", location="L")
        Asset.objects.create(classification=Classification.objects.create(department=dept, name="C"), name="A-1")
        Location.objects.create(name="Main Field")
        data = {"asset_serial": "A-1", "activity": "pressure_test", "schedule": "repeating", "times_per": 2,
                "every_count": 1, "every_unit": "month", "place": "location"}
        self.assertFalse(MaintenanceRecordForm(data | {"location_name": "Nowhere"}).is_valid())
        form = MaintenanceRecordForm(data | {"location_name": "Main Field"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().place_text, "At Main Field")
