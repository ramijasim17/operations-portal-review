"""Every tab of the redesigned pages must render, with its navigation intact."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from job_manager.models import JobManifest
from job_reports.models import JobReport
from personnel.models import Personnel, PersonnelCert, TrainingCourse
from sgs_report.models import SGSReport
from sqb.models import SQBPackage, SQBPartDefinition, SQBPartUpload, SQBType

from .models import Profile, Role


class PageSmokeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manifest = JobManifest.objects.create(job_name="Call out", client="Client A", location="Site")
        cls.job = JobReport.objects.create(manifest=cls.manifest, name="Well 1", needs_survey_report=True,
                                           survey_report_type="SGS")
        cls.sgs = SGSReport.objects.create(well_name="Well 1", client="Client A")
        cls.job.linked_sgs_report = cls.sgs
        cls.job.save()
        cls.person = Personnel.objects.create(employee_id="9001", name="Test Person", status="Active")
        course = TrainingCourse.objects.create(course_name="H2S")
        PersonnelCert.objects.create(personnel=cls.person, course=course, expiry_date="2020-01-01")

    def setUp(self):
        user = User.objects.create_user(username="admin_t", password="x")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.ADMIN})
        self.client.force_login(user)

    def get(self, name, *args, **params):
        response = self.client.get(reverse(name, args=args), params)
        self.assertEqual(response.status_code, 200, f"{name} {params}")
        return response.content.decode()

    def test_sgs_detail_tabs_keep_their_navigation(self):
        for tab in ("common", "gauge_logs", "result", "final"):
            html = self.get("sgs_report:detail", self.sgs.pk, tab=tab)
            for label in ("Common Information", "Gauge Logs", "Build SGS Result", "Build Final Report"):
                self.assertIn(label, html, f"tab={tab} lost the '{label}' tab link")
        self.assertIn("From job", self.get("sgs_report:detail", self.sgs.pk))

    def test_sgs_list(self):
        html = self.get("sgs_report:list")
        self.assertIn("Well 1", html)
        self.assertIn("sent to client", html)

    def test_job_report_pages(self):
        self.assertIn("Well 1", self.get("job_reports:list"))
        html = self.get("job_reports:detail", self.job.pk)
        self.assertIn("Survey report", html)
        self.assertIn("Create SQB", html)              # SQB step is pending

    def test_sqb_tabs_and_progress(self):
        sqb_type = SQBType.objects.create(name="T")
        for n in ("A", "B"):
            SQBPartDefinition.objects.create(sqb_type=sqb_type, name=n, order=1)
        package = SQBPackage.objects.create(job_name="x", job_report=self.job, sqb_type=sqb_type)
        for i, definition in enumerate(sqb_type.parts.all()):
            SQBPartUpload.objects.create(package=package, part_definition=definition, file="p.pdf" if i == 0 else "")
        for tab in ("missing", "open", "closed"):
            self.get("sqb:job_list", tab=tab)
        html = self.get("sqb:job_list", tab="open")
        self.assertIn("1 / 2", html)

    def test_personnel_tabs_and_certificate_health(self):
        html = self.get("personnel:list")
        self.assertIn("1 expired", html)                # per-person certificate pill
        self.assertIn("Test Person", html)
        for tab in ("employees", "courses", "certificates"):
            page = self.get("personnel:list", tab=tab)
            for label in ("Employees", "Required courses", "Certificates"):
                self.assertIn(label, page)
        self.assertIn("Test Person", self.get("personnel:list", q="9001"))
        for tab in ("employees", "courses", "certificates"):
            self.assertNotIn("border-accent", self.get("personnel:list", tab=tab))   # no leftover old tab markup


class SidebarHighlightTests(TestCase):
    def test_sub_pages_keep_their_module_highlighted(self):
        from core.context_processors import active_nav_item
        cases = {
            "action_tracker:list": "action_tracker:list",
            "action_tracker:detail": "action_tracker:list",
            "action_tracker:edit": "action_tracker:list",
            "job_manager:detail": "job_manager:list",
            "job_reports:detail": "job_reports:list",
            "sgs_report:detail": "sgs_report:list",
            "sqb:package_detail": "sqb:job_list",
            "departments:classification_detail": "departments:list",
            "personnel:cert_matrix": "personnel:list",
            "wire_management:detail": "wire_management:list",        # the Wire Management item...
            "wire_management:lists": "wire_management:lists",          # ...and Clients & Fields stay separate
            "wire_management:list_edit": "wire_management:lists",
            "core:settings": "core:dashboard",
        }
        for view_name, expected in cases.items():
            self.assertEqual(active_nav_item(view_name), expected, view_name)
        self.assertIsNone(active_nav_item(None))

    def test_action_detail_page_highlights_the_sidebar_and_has_a_clear_way_back(self):
        from action_tracker.models import Action
        user = User.objects.create_user(username="nav", password="x")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.VIEWER})
        from .dept_testing import link_department, make_action
        link_department(user)
        self.client.force_login(user)
        action = make_action(action_item="Something")
        html = self.client.get(reverse("action_tracker:detail", args=[action.pk])).content.decode()
        self.assertRegex(html, r'href="/actions/" class="active">Action Tracker')
        self.assertIn('class="page-sub crumbs"', html)
        self.assertIn("/actions/?tab=open", html)
        action.status = "Completed"
        action.save()
        self.assertIn("/actions/?tab=completed",
                      self.client.get(reverse("action_tracker:detail", args=[action.pk])).content.decode())
