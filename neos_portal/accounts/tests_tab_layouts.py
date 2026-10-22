"""The tidied SGS report tabs and the Personnel certificate cards."""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from sgs_report.models import SGSReport, SGSSequenceEntry, SGSShift, SGSStation

from personnel.models import Personnel, PersonnelCert, TrainingCourse

from .models import Role
from .tests_actions_approvals import login

ZONE = timezone.get_current_timezone()


def local(h, m=0):
    return timezone.make_aware(datetime.datetime(2026, 4, 18, h, m), ZONE)


def days(n):
    return timezone.localdate() + datetime.timedelta(days=n)


class SGSTabTests(TestCase):
    def setUp(self):
        login(self.client, Role.ADMIN, "a")
        self.report = SGSReport.objects.create(well_name="R-376", field="North Field", client="Client A", rig="Rig 5",
                                               run_start=local(10), run_end=local(15))
        SGSStation.objects.create(report=self.report, step_no=1, md_mgl=2465, start_datetime=local(11), end_datetime=local(11, 10),
                                  pressure_psia=2900.0, temp_value=170.0)
        SGSStation.objects.create(report=self.report, step_no=2, md_mgl=2457, start_datetime=local(12), end_datetime=local(12, 10))
        shift = SGSShift.objects.create(report=self.report, label="Day", shift_date=datetime.date(2026, 4, 18), order=1)
        SGSSequenceEntry.objects.create(shift=shift, sr="1", start_datetime=local(10), end_datetime=local(10, 30), description="Rig up", order=1)
        self.url = reverse("sgs_report:detail", args=[self.report.pk])

    def test_progress_strip_shows_the_four_steps(self):
        page = self.client.get(self.url)
        self.assertEqual(page.context["progress"]["stations"], 2)
        self.assertEqual(page.context["progress"]["stations_done"], 1)
        self.assertEqual(page.context["progress"]["events"], 1)
        for text in ("1 &middot; Common information", "2 &middot; Gauge logs", "3 &middot; SGS result", "4 &middot; Final report", "1 of 2 stations complete"):
            self.assertContains(page, text)

    def test_common_tab_lists_the_report_fields(self):
        page = self.client.get(self.url + "?tab=common")
        for text in ("Test details", "Run &amp; gauges", "Well data", "Operation", "Rig 5", "North Field"):
            self.assertContains(page, text)

    def test_result_tab_keeps_the_blue_gradient_rows_under_their_own_columns(self):
        page = self.client.get(self.url + "?tab=result")
        self.assertContains(page, "table-compact")
        self.assertContains(page, "needs entry")                    # station 2 has no readings yet
        self.assertContains(page, 'class="grad"')                   # the blue row between two stations
        for column in ("Avg MD (to next)", "Pressure grad", "Temp grad"):
            self.assertContains(page, column)
        self.assertContains(page, "Add a station")
        self.assertContains(page, "Import from Excel")

    def test_final_tab_keeps_the_original_picture_boxes(self):
        page = self.client.get(self.url + "?tab=final")
        self.assertContains(page, "Step 2")
        self.assertContains(page, "aspect-ratio:4/3")               # the dashed 'No image' box with its file picker
        self.assertNotContains(page, "image-grid")

    def test_final_tab_has_events_shift_form_and_image_slots(self):
        page = self.client.get(self.url + "?tab=final")
        for text in ("Report files", "Step 1", "Rig up", "Add a shift", "Step 2"):
            self.assertContains(page, text)

    def test_every_tab_still_renders_with_the_tab_bar(self):
        for tab in ("common", "gauge_logs", "result", "final"):
            page = self.client.get(self.url + f"?tab={tab}")
            self.assertEqual(page.status_code, 200, tab)
            for label in ("Common Information", "Gauge Logs", "Build SGS Result", "Build Final Report"):
                self.assertContains(page, label)


class AssetVersionTests(TestCase):
    def test_css_and_js_links_carry_a_version_so_a_stale_copy_is_never_kept(self):
        login(self.client, Role.VIEWER, "v")
        page = self.client.get(reverse("core:dashboard"))
        self.assertRegex(page.content.decode(), r"portal\.css\?v=\d+")
        self.assertRegex(page.content.decode(), r"portal\.js\?v=\d+")


class BlueRowTests(TestCase):
    def test_the_gradient_rows_are_blue_with_their_own_styling_not_only_from_the_stylesheet(self):
        login(self.client, Role.ADMIN, "a")
        report = SGSReport.objects.create(well_name="R", run_start=local(10), run_end=local(15))
        for i, (md, hh) in enumerate(((2465, 11), (2457, 12)), 1):
            SGSStation.objects.create(report=report, step_no=i, md_mgl=md, start_datetime=local(hh), end_datetime=local(hh, 10),
                                      pressure_psia=2900.0, temp_value=170.0)
        page = self.client.get(reverse("sgs_report:detail", args=[report.pk]) + "?tab=result")
        self.assertContains(page, "var(--result-bg)")


class PersonnelTabTests(TestCase):
    """Required courses stays the simple table; Certificates is the one-card-per-employee layout, tidied."""

    def setUp(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        login(self.client, Role.ADMIN, "a")
        self.h2s = TrainingCourse.objects.create(course_name="H2S")
        self.aid = TrainingCourse.objects.create(course_name="First Aid")
        self.alice = Personnel.objects.create(employee_id="1", name="Alice A", position="Supervisor")
        self.bob = Personnel.objects.create(employee_id="2", name="Bob B")
        self.gone = Personnel.objects.create(employee_id="4", name="Gone G", status="Inactive")
        for p in (self.alice, self.bob):
            p.required_training_courses.set([self.h2s, self.aid])
        PersonnelCert.objects.create(personnel=self.alice, course=self.h2s, expiry_date=days(-5))
        PersonnelCert.objects.create(personnel=self.alice, course=self.aid)                    # no expiry date
        self.url = reverse("personnel:list") + "?tab="
        self.Capture = CaptureQueriesContext
        self.connection = connection

    def test_the_matrix_is_the_default_with_one_coloured_cell_per_course(self):
        page = self.client.get(self.url + "certificates")
        self.assertContains(page, "cert-matrix")
        self.assertNotContains(page, "Gone G")
        kinds = {p.name: [c["kind"] for c in p.cells] for p in page.context["active_people"]}
        # courses are listed A-Z: First Aid, H2S
        self.assertEqual(kinds["Alice A"], ["valid", "expired"])         # First Aid has no expiry date, H2S expired
        self.assertEqual(kinds["Bob B"], ["missing", "missing"])
        self.assertEqual(page.context["summary"], {"missing": 2, "expired": 1, "soon": 0, "valid": 1})
        self.assertContains(page, "+ add")                                # a missing cell offers to add it
        self.assertContains(page, "5d ago")
        self.assertContains(page, "no expiry")

    def test_an_unrequired_course_with_no_certificate_is_a_quiet_dot(self):
        extra = TrainingCourse.objects.create(course_name="Zebra course")
        page = self.client.get(self.url + "certificates")
        cells = {p.name: [c["kind"] for c in p.cells] for p in page.context["active_people"]}
        self.assertEqual(cells["Alice A"][-1], "na")
        PersonnelCert.objects.create(personnel=self.alice, course=extra, expiry_date=days(300))      # held though not required
        page = self.client.get(self.url + "certificates")
        self.assertEqual({p.name: p.cells[-1]["kind"] for p in page.context["active_people"]}["Alice A"], "valid")

    def test_filters_and_sorting(self):
        names = lambda **q: [p.name for p in self.client.get(reverse("personnel:list"), {"tab": "certificates", **q}).context["active_people"]]
        self.assertEqual(names(), ["Alice A", "Bob B"])                   # the one with an expired certificate first
        self.assertEqual(names(sort="name"), ["Alice A", "Bob B"])
        self.assertEqual(names(show="expired"), ["Alice A"])
        self.assertEqual(names(show="missing"), ["Bob B"])
        self.assertEqual(names(show="soon"), [])
        self.assertEqual(names(show="attention"), ["Alice A", "Bob B"])
        self.assertEqual(names(q="bob"), ["Bob B"])
        self.assertEqual(names(q="2"), ["Bob B"])

    def test_the_cards_view_is_still_there(self):
        page = self.client.get(self.url + "certificates&view=cards")
        self.assertContains(page, "cert-card")
        self.assertNotContains(page, "cert-matrix")
        self.assertContains(page, "Add certificate")

    def test_viewers_see_the_matrix_without_edit_links(self):
        self.client.logout()
        login(self.client, Role.VIEWER, "v")
        page = self.client.get(self.url + "certificates")
        self.assertContains(page, "cert-matrix")
        self.assertNotContains(page, "+ add")
        self.assertNotContains(page, "cert_edit")

    def test_certificates_tab_does_not_query_per_person(self):
        self.client.get(self.url + "certificates")          # warm-up: the first visit also records "last seen"
        with self.Capture(self.connection) as few:
            self.client.get(self.url + "certificates")
        for i in range(10):
            p = Personnel.objects.create(employee_id=f"x{i}", name=f"Extra {i}")
            p.required_training_courses.set([self.h2s, self.aid])
            PersonnelCert.objects.create(personnel=p, course=self.h2s, expiry_date=days(100))
        with self.Capture(self.connection) as many:
            self.client.get(self.url + "certificates")
        self.assertEqual(len(few), len(many))

    def test_required_courses_is_the_simple_table_and_still_saves(self):
        page = self.client.get(self.url + "courses")
        self.assertContains(page, "matrix-table")
        self.assertContains(page, "Save required courses")
        with self.Capture(self.connection) as few:
            self.client.get(self.url + "courses")
        for i in range(10):
            Personnel.objects.create(employee_id=f"y{i}", name=f"More {i}").required_training_courses.set([self.h2s])
        with self.Capture(self.connection) as many:
            self.client.get(self.url + "courses")
        self.assertEqual(len(few), len(many))
        post = {f"required_{self.alice.id}_{self.h2s.id}": "on"}
        self.client.post(reverse("personnel:required_courses_save"), post)
        self.assertEqual({c.course_name for c in self.alice.required_training_courses.all()}, {"H2S"})

    def test_tab_bar(self):
        for tab in ("employees", "courses", "certificates"):
            page = self.client.get(self.url + tab)
            for label in ("Employees", "Required courses", "Certificates"):
                self.assertContains(page, f">{label}</a>")


class CollapsibleShiftTests(TestCase):
    def setUp(self):
        login(self.client, Role.ADMIN, "a")
        self.report = SGSReport.objects.create(well_name="R", report_type="PBU", run_start=local(8), run_end=local(20))
        for n in range(1, 4):
            shift = SGSShift.objects.create(report=self.report, label=f"Shift {n}", shift_date=datetime.date(2026, 4, 18), order=n)
            for i in range(1, n + 2):
                SGSSequenceEntry.objects.create(shift=shift, sr=str(i), start_datetime=local(8 + i), end_datetime=local(9 + i),
                                                description=f"Event number {n} row {i}", order=i)
        self.url = reverse("sgs_report:detail", args=[self.report.pk]) + "?tab=final"

    def test_each_shift_is_a_collapsible_card_with_a_summary(self):
        page = self.client.get(self.url).content.decode()
        self.assertEqual(page.count('<details class="shift-card"'), 3)
        self.assertIn("2 events", page)
        self.assertIn("4 events", page)
        self.assertIn("09:00 &rarr; 11:00", page)            # shift 1: 2 events 09:00-10:00, 10:00-11:00 -> first start .. last end

    def test_only_the_latest_shift_starts_open_when_there_are_several(self):
        page = self.client.get(self.url).content.decode()
        cards = [c for c in page.split('<details class="shift-card"')[1:]]
        self.assertEqual([("open" in c.split(">")[0]) for c in cards], [False, False, True])
        self.assertIn("Open all shifts", page)
        self.assertIn("Close all shifts", page)

    def test_a_single_shift_is_open_and_has_no_open_all_buttons(self):
        SGSShift.objects.filter(order__gt=1).delete()
        page = self.client.get(self.url).content.decode()
        self.assertEqual(page.count('<details class="shift-card"'), 1)
        self.assertIn("open", page.split('<details class="shift-card"')[1].split(">")[0])
        self.assertNotIn("Open all shifts", page)

    def test_the_events_and_forms_are_still_inside_each_shift(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Event number")
        self.assertEqual(page.content.decode().count("Event number"), 2 + 3 + 4)
        self.assertContains(page, "Add row")
        self.assertContains(page, "Add a shift")
        self.assertEqual(page.content.decode().count("<details"), page.content.decode().count("</details>"))

    def test_the_summary_methods(self):
        shift = SGSShift.objects.get(order=2)
        self.assertEqual(shift.summary(), {"count": 3, "first": "09:00", "last": "12:00"})
        empty = SGSShift.objects.create(report=self.report, label="Empty", order=9)
        self.assertEqual(empty.summary(), {"count": 0, "first": "", "last": ""})
