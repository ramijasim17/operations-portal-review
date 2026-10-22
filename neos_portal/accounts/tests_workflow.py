"""Manifest -> job -> SGS report -> SQB sequence."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from job_manager.models import JobManifest
from job_reports.models import JobReport
from sgs_report.models import SGSReport
from sqb.models import SQBPackage, SQBPartDefinition, SQBType

from .models import Profile, Role


class WorkflowTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="editor", password="x")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.EDITOR})
        self.client.force_login(user)
        self.manifest = JobManifest.objects.create(job_name="Call out 01", client="Client A", location="Main Field")
        self.sqb_type = SQBType.objects.create(name="Client A")
        SQBPartDefinition.objects.create(sqb_type=self.sqb_type, name="JSA", order=1)

    def create_job(self, **extra):
        data = {"manifest": self.manifest.pk, "name": "Well Testing", "description": ""} | extra
        return self.client.post(reverse("job_reports:create"), data)

    def test_job_without_survey_creates_no_sgs_report(self):
        self.create_job()
        self.assertEqual(SGSReport.objects.count(), 0)

    def test_job_needing_a_survey_creates_the_sgs_report(self):
        self.create_job(needs_survey_report="on", survey_report_type="PBU")
        job = JobReport.objects.get()
        self.assertIsNotNone(job.linked_sgs_report)
        self.assertEqual(job.linked_sgs_report.client, "Client A")
        self.assertEqual(job.linked_sgs_report.report_type, "PBU")

    def test_new_job_appears_under_missing_sqb(self):
        self.create_job()
        page = self.client.get(reverse("sqb:job_list") + "?tab=missing")
        self.assertContains(page, "Well Testing")
        self.assertContains(page, "Create SQB")

    def test_manifest_alone_is_not_listed_in_sqb(self):
        page = self.client.get(reverse("sqb:job_list") + "?tab=missing")
        self.assertNotContains(page, "Call out 01")

    def test_creating_the_sqb_moves_the_job_out_of_missing(self):
        self.create_job()
        job = JobReport.objects.get()
        self.client.post(reverse("sqb:package_create", args=[job.pk]), {"sqb_type": self.sqb_type.pk})
        package = SQBPackage.objects.get()
        self.assertEqual(package.job_report, job)
        self.assertEqual(package.parts.count(), 1)
        missing = self.client.get(reverse("sqb:job_list") + "?tab=missing")
        self.assertContains(missing, '<div class="stat-value">0</div><div class="stat-label">jobs missing an SQB')
        self.assertContains(missing, '<div class="stat-value">1</div><div class="stat-label">open')

    def test_two_jobs_with_the_same_name_each_get_their_own_sqb(self):
        other = JobManifest.objects.create(job_name="Call out 02", client="Client A")
        first = JobReport.objects.create(manifest=self.manifest, name="Well Testing")
        second = JobReport.objects.create(manifest=other, name="Well Testing")
        for job in (first, second):
            self.client.post(reverse("sqb:package_create", args=[job.pk]), {"sqb_type": self.sqb_type.pk})
        self.assertEqual(SQBPackage.objects.count(), 2)

    def test_job_page_and_sgs_list_show_the_links(self):
        self.create_job(needs_survey_report="on", survey_report_type="SGS")
        job = JobReport.objects.get()
        self.assertContains(self.client.get(reverse("job_reports:detail", args=[job.pk])), "Missing")
        self.assertContains(self.client.get(reverse("sgs_report:list")), "&middot; job <a href")
