"""Job Reports: supervisors by shift and by date, for jobs that run several days."""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from job_manager.models import JobManifest
from job_reports.forms import JobReportForm, JobSupervisorForm
from job_reports.models import JobReport, JobSupervisor
from personnel.models import Personnel

from .models import Role
from .tests_actions_approvals import login

TODAY = timezone.localdate()


def d(n):
    return TODAY + datetime.timedelta(days=n)


class SupervisorTests(TestCase):
    def setUp(self):
        login(self.client, Role.EDITOR)
        self.manifest = JobManifest.objects.create(job_name="M1", client="Client A")
        self.job = JobReport.objects.create(manifest=self.manifest, name="J1")
        self.sam = Personnel.objects.create(employee_id="1", name="Sam Super", position="Slickline Supervisor", department="Test dept")
        self.nora = Personnel.objects.create(employee_id="2", name="Nora Night", position="Wireline Supervisor", department="Test dept")
        self.op = Personnel.objects.create(employee_id="3", name="Olly Operator", position="Wireline Operator", department="Test dept")
        self.gone = Personnel.objects.create(employee_id="4", name="Gone Guy", position="Supervisor", department="Test dept", status="Inactive")

    def add(self, person, shift="day", start=-5, end=None, job=None):
        return JobSupervisor.objects.create(job_report=job or self.job, person=person, shift=shift,
                                            start_date=d(start), end_date=d(end) if end is not None else None)

    def form(self, person, shift="day", start=-5, end=None, instance=None):
        data = {"person": person.pk, "shift": shift, "start_date": d(start)}
        if end is not None:
            data["end_date"] = d(end)
        return JobSupervisorForm(data, job=self.job, instance=instance)

    # -- one or two supervisors, and changes during a long job
    def test_one_two_or_no_supervisors(self):
        self.assertEqual(self.job.supervisors, [])
        self.add(self.sam, "day")
        self.assertEqual([s for s, _ in JobReport.objects.get(pk=self.job.pk).supervisors], ["Day shift"])
        self.add(self.nora, "night")
        self.assertEqual(len(JobReport.objects.get(pk=self.job.pk).supervisors), 2)

    def test_the_supervisor_can_change_part_way_through_a_long_job(self):
        self.add(self.sam, "day", -10, -4)           # left a few days ago
        self.add(self.nora, "day", -3)               # took over, still on it
        job = JobReport.objects.get(pk=self.job.pk)
        self.assertEqual([p.name for _, p in job.supervisors], ["Nora Night"])
        self.assertEqual(job.assignments.count(), 2)  # the history is kept

    def test_when_nobody_is_on_it_now_the_last_people_are_shown_as_former(self):
        self.add(self.sam, "day", -10, -4)
        job = JobReport.objects.get(pk=self.job.pk)
        self.assertEqual(job.supervisors, [])
        self.assertEqual([p.name for _, p in job.former_supervisors], ["Sam Super"])

    def test_a_future_assignment_is_not_current_yet(self):
        self.add(self.sam, "day", 3)
        self.assertEqual(JobReport.objects.get(pk=self.job.pk).supervisors, [])

    def test_days_covered(self):
        a = self.add(self.sam, "day", -10, -4)
        self.assertEqual(a.days, 7)
        self.assertIsNone(self.add(self.nora, "night", -3).days)

    # -- validation
    def test_valid_stretches_for_the_two_shifts_can_run_together(self):
        self.add(self.sam, "day")
        self.assertTrue(self.form(self.nora, "night").is_valid())

    def test_the_same_shift_cannot_have_two_people_on_the_same_days(self):
        self.add(self.sam, "day", -5)
        f = self.form(self.nora, "day", -2)
        self.assertFalse(f.is_valid())
        self.assertIn("already covers", f.non_field_errors()[0])

    def test_back_to_back_handover_is_allowed(self):
        self.add(self.sam, "day", -10, -4)
        self.assertTrue(self.form(self.nora, "day", -3).is_valid())

    def test_one_person_covering_both_shifts_blocks_the_others(self):
        self.add(self.sam, "both", -5)
        self.assertFalse(self.form(self.nora, "night", -1).is_valid())

    def test_end_cannot_be_before_start(self):
        self.assertFalse(self.form(self.sam, "day", -2, -6).is_valid())

    def test_editing_an_assignment_does_not_clash_with_itself(self):
        a = self.add(self.sam, "day", -5)
        self.assertTrue(self.form(self.sam, "day", -5, 0, instance=a).is_valid())

    def test_dropdown_lists_supervisors_first_and_hides_inactive(self):
        names = [p.name for p in JobSupervisorForm(job=self.job).fields["person"].queryset]
        self.assertEqual(names, ["Nora Night", "Sam Super", "Olly Operator"])

    def test_the_job_form_no_longer_has_supervisor_fields(self):
        self.assertNotIn("day_supervisor", JobReportForm().fields)

    # -- pages
    def test_add_edit_remove_through_the_pages(self):
        url = reverse("job_reports:supervisor_add", args=[self.job.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {"person": self.sam.pk, "shift": "day", "start_date": d(-2)})
        self.assertRedirects(r, reverse("job_reports:detail", args=[self.job.pk]))
        a = self.job.assignments.get()
        self.assertEqual(a.person, self.sam)
        r = self.client.post(reverse("job_reports:supervisor_edit", args=[a.pk]),
                             {"person": self.sam.pk, "shift": "day", "start_date": d(-2), "end_date": d(0)})
        a.refresh_from_db()
        self.assertEqual(a.end_date, d(0))
        self.client.post(reverse("job_reports:supervisor_delete", args=[a.pk]))
        self.assertTrue(self.job.assignments.exists())          # an editor may not remove
        self.client.logout()
        login(self.client, Role.ADMIN, "a")
        self.client.post(reverse("job_reports:supervisor_delete", args=[a.pk]))
        self.assertFalse(self.job.assignments.exists())

    def test_detail_and_list_show_who_is_on_the_job(self):
        self.add(self.sam, "day")
        self.add(self.nora, "night")
        self.assertContains(self.client.get(reverse("job_reports:detail", args=[self.job.pk])), "Nora Night")
        self.assertContains(self.client.get(reverse("job_reports:list")), "Sam Super")

    def test_viewers_cannot_add_supervisors(self):
        self.client.logout()
        login(self.client, Role.VIEWER, "v")
        r = self.client.post(reverse("job_reports:supervisor_add", args=[self.job.pk]),
                             {"person": self.sam.pk, "shift": "day", "start_date": d(0)})
        self.assertFalse(self.job.assignments.exists())

    def test_search_by_supervisor_name_finds_the_job_once(self):
        self.add(self.sam, "day", -10, -4)
        self.add(self.sam, "night", -3)
        JobReport.objects.create(manifest=self.manifest, name="Other")
        r = self.client.get(reverse("job_reports:list") + "?q=Sam")
        self.assertEqual(r.content.decode().count("J1</a>"), 1)
        self.assertNotContains(r, "Other</a>")

    def test_deleting_a_person_keeps_the_job(self):
        self.add(self.sam)
        self.sam.delete()
        self.assertTrue(JobReport.objects.filter(pk=self.job.pk).exists())

    def test_list_query_count_is_flat(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        url = reverse("job_reports:list")
        self.add(self.sam)
        self.client.get(url)                      # warm-up: the first visit also records "last seen"
        with CaptureQueriesContext(connection) as few:
            self.client.get(url)
        for i in range(10):
            job = JobReport.objects.create(manifest=self.manifest, name=f"b{i}")
            self.add(self.sam, "day", -9, -5, job=job)
            self.add(self.nora, "night", -4, job=job)
        with CaptureQueriesContext(connection) as many:
            self.client.get(url)
        self.assertEqual(len(few), len(many))

    # -- each person's page lists the jobs they supervised
    def test_page_lists_jobs_stints_and_days(self):
        self.add(self.sam, "day", -10, -6)                      # 5 days
        self.add(self.sam, "night", -2)                         # 3 days so far (today counts)
        other = JobReport.objects.create(manifest=self.manifest, name="Old job")
        self.add(self.sam, "day", -40, -31, job=other)          # 10 days, finished
        r = self.client.get(reverse("personnel:detail", args=[self.sam.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["total_days"], 18)
        self.assertEqual(r.context["current_count"], 1)
        self.assertEqual([x["job"].name for x in r.context["jobs"]], ["J1", "Old job"])    # the current job first
        self.assertEqual(r.context["by_shift"], {"day": 2, "night": 1, "both": 0})
        self.assertContains(r, "on it now")

    def test_page_for_someone_with_no_jobs(self):
        r = self.client.get(reverse("personnel:detail", args=[self.op.pk]))
        self.assertContains(r, "Not recorded as a supervisor")

    def test_list_links_to_the_page_and_shows_the_job_count(self):
        self.add(self.sam)
        r = self.client.get(reverse("personnel:list"))
        self.assertContains(r, reverse("personnel:detail", args=[self.sam.pk]))
        self.assertContains(r, "on a job now")

    def test_viewers_can_open_it(self):
        self.client.logout()
        login(self.client, Role.VIEWER, "v2")
        self.assertEqual(self.client.get(reverse("personnel:detail", args=[self.sam.pk])).status_code, 200)

    # -- the Personnel list: filters, cards / table, certificate health
    def _list(self, **params):
        from urllib.parse import urlencode
        return self.client.get(reverse("personnel:list") + "?" + urlencode(params))

    def test_cards_are_the_default_and_table_is_available(self):
        self.assertContains(self._list(), "person-card")
        self.assertContains(self._list(view="table"), "personnel-table")
        self.assertNotContains(self._list(view="table"), "person-card")

    def test_tab_bar_is_intact(self):
        page = self._list().content.decode()
        for label in ("Employees", "Required courses", "Certificates"):
            self.assertIn(f">{label}</a>", page)

    def test_filter_by_position_department_and_search(self):
        Personnel.objects.filter(pk=self.op.pk).update(department="WL/SL")
        login(self.client, Role.ADMIN, "boss")                                   # an admin sees every department
        self.assertContains(self._list(dept="all", position="Wireline Operator"), "Olly Operator")
        self.assertNotContains(self._list(dept="all", position="Wireline Operator"), "Sam Super")
        self.assertContains(self._list(dept="all", department="WL/SL"), "Olly Operator")
        self.assertNotContains(self._list(dept="all", department="WL/SL"), "Nora Night")
        self.assertContains(self._list(dept="all", q="supervisor"), "Sam Super")  # position is searched too

    def test_filter_by_certificate_state_and_needs_attention_sort(self):
        from personnel.models import PersonnelCert, TrainingCourse
        course = TrainingCourse.objects.create(course_name="H2S")
        PersonnelCert.objects.create(personnel=self.sam, course=course, expiry_date=d(-3))
        PersonnelCert.objects.create(personnel=self.nora, course=course, expiry_date=d(400))
        r = self._list(certs="expired")
        self.assertContains(r, "Sam Super")
        self.assertNotContains(r, "Nora Night")
        self.assertContains(self._list(certs="ok"), "Nora Night")
        self.assertContains(self._list(certs="none"), "Olly Operator")
        names = [p.name for p in self._list(sort="attention").context["people"]]
        self.assertEqual(names[0], "Sam Super")

    def test_on_a_job_now_badge_and_avatar_initials(self):
        self.add(self.sam)
        r = self._list()
        self.assertContains(r, "on a job now")
        self.assertContains(r, ">SS<")
        self.assertContains(self.client.get(reverse("personnel:detail", args=[self.sam.pk])), "avatar-lg")
