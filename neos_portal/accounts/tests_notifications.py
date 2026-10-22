"""Notifications: managers hear about every change; people hear when something is assigned to them."""
import datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from action_tracker.models import Action
from job_manager.models import JobManifest
from job_reports.models import JobReport, JobSupervisor
from notifications.models import Notification
from personnel.models import Personnel

from .dept_testing import link_department
from .models import Profile, Role


def make_user(name, role=Role.EDITOR, notify_all=False, employee=None):
    user = User.objects.create_user(username=name, password="x")
    Profile.objects.update_or_create(user=user, defaults={"role": role, "notify_all": notify_all, "employee": employee})
    return user


def signed_in(user):
    c = Client()
    c.force_login(user)
    return c


class ChangeAlertTests(TestCase):
    def setUp(self):
        self.manager = make_user("boss", Role.ADMIN, notify_all=True)
        self.editor = make_user("ed", Role.EDITOR)
        self.bystander = make_user("by", Role.EDITOR)            # did not ask for alerts
        self.manifest = JobManifest.objects.create(job_name="M1", client="Client A")
        self.ed = signed_in(self.editor)
        Notification.objects.all().delete()                      # creating the accounts above announced them

    def post_job(self, name="J1"):
        return self.ed.post(reverse("job_reports:create"), {"manifest": self.manifest.pk, "name": name})

    def test_manager_hears_about_what_an_editor_adds(self):
        self.post_job()
        n = Notification.objects.get(recipient=self.manager)
        self.assertIn("ed added job report", n.message)
        self.assertIn("J1", n.message)
        self.assertEqual(n.kind, "activity")
        self.assertEqual(n.link, reverse("job_reports:detail", args=[JobReport.objects.get().pk]))

    def test_only_people_who_asked_are_notified_and_never_the_person_who_did_it(self):
        self.post_job()
        self.assertEqual(Notification.objects.filter(recipient=self.bystander).count(), 0)
        self.assertEqual(Notification.objects.filter(recipient=self.editor).count(), 0)
        signed_in(self.manager).post(reverse("job_reports:create"), {"manifest": self.manifest.pk, "name": "Mine"})
        self.assertEqual(Notification.objects.filter(recipient=self.manager).count(), 1)     # not told about its own

    def test_changes_made_outside_the_portal_are_not_reported(self):
        JobReport.objects.create(manifest=self.manifest, name="script")
        self.assertEqual(Notification.objects.count(), 0)

    def test_edits_and_deletes_are_reported(self):
        job = JobReport.objects.create(manifest=self.manifest, name="J1")
        self.ed.post(reverse("job_reports:edit", args=[job.pk]), {"manifest": self.manifest.pk, "name": "J1b"})
        self.assertTrue(Notification.objects.filter(recipient=self.manager, message__contains="changed job report").exists())
        signed_in(make_user("adm2", Role.ADMIN)).post(reverse("job_reports:delete", args=[job.pk]))
        self.assertTrue(Notification.objects.filter(recipient=self.manager, message__contains="deleted job report").exists())

    def test_an_upload_says_uploaded(self):
        job = JobReport.objects.create(manifest=self.manifest, name="J1")
        self.ed.post(reverse("job_reports:detail", args=[job.pk]), {
            "document_type": "daily_report", "title": "Day 1",
            "file": SimpleUploadedFile("day1.pdf", b"%PDF-1.4 test", content_type="application/pdf")})
        n = Notification.objects.get(recipient=self.manager, verb="uploaded")
        self.assertIn("uploaded", n.message)
        self.assertEqual(n.link, reverse("job_reports:detail", args=[job.pk]))

    def test_a_burst_of_the_same_change_is_folded_into_one_alert(self):
        for i in range(3):
            self.post_job(f"J{i}")
        n = Notification.objects.get(recipient=self.manager)
        self.assertEqual(n.count, 3)
        self.assertEqual(n.message, "ed added 3 job reports")

    def test_a_folded_alert_counts_as_new_so_the_bell_pops_up_again(self):
        self.post_job("a")
        first = Notification.objects.get(recipient=self.manager)
        self.post_job("b")
        second = Notification.objects.get(recipient=self.manager)
        self.assertGreater(second.pk, first.pk)                     # a new row, so a poll with after=<old id> sees it
        manager = signed_in(self.manager)
        news = manager.get(reverse("notifications:poll") + f"?after={first.pk}").json()["new"]
        self.assertEqual([n["message"] for n in news], ["ed added 2 job reports"])
        self.assertEqual(manager.get(reverse("notifications:poll")).json()["count"], 1)       # still one alert, not two

    def test_a_read_alert_is_not_folded_into(self):
        self.post_job("a")
        Notification.objects.update(read_at=datetime.datetime.now(datetime.timezone.utc))
        self.post_job("b")
        self.assertEqual(Notification.objects.filter(recipient=self.manager).count(), 2)

    def test_inactive_manager_gets_nothing(self):
        Profile.objects.filter(user=self.manager).update(is_active_employee=False)
        self.post_job()
        self.assertEqual(Notification.objects.count(), 0)

    def test_new_signup_is_announced_to_managers_once_verified(self):
        newbie = User.objects.create_user(username="newbie", password="x", is_active=False)
        self.assertFalse(Notification.objects.filter(message__contains="newbie").exists())
        newbie.is_active = True
        newbie.save(update_fields=["is_active"])
        self.assertTrue(Notification.objects.filter(recipient=self.manager, message__contains="newbie").exists())


class AssignmentTests(TestCase):
    def setUp(self):
        self.person = Personnel.objects.create(employee_id="7", name="Sam Super", position="Slickline Supervisor", department="Test dept")
        self.sam = make_user("sam", Role.EDITOR, employee=self.person)
        self.editor = make_user("ed", Role.EDITOR)
        link_department(self.editor)
        self.ed = signed_in(self.editor)
        Notification.objects.all().delete()

    def create_action(self, **kw):
        data = {"action_item": "Fix the lubricator", "priority": "High", "assignee": "Sam Super"}
        data.update(kw)
        return self.ed.post(reverse("action_tracker:create"), data)

    def test_assignee_is_notified(self):
        self.create_action()
        n = Notification.objects.get(recipient=self.sam)
        self.assertEqual(n.kind, "assigned")
        self.assertIn("ed assigned you an action", n.message)
        self.assertEqual(n.link, reverse("action_tracker:detail", args=[Action.objects.get().pk]))

    def test_nobody_is_notified_when_no_account_matches(self):
        self.create_action(assignee="")
        self.assertEqual(Notification.objects.filter(kind="assigned").count(), 0)

    def test_reassigning_notifies_the_new_person_but_editing_other_fields_does_not(self):
        self.create_action(assignee="")
        action = Action.objects.get()
        edit = reverse("action_tracker:edit", args=[action.pk])
        self.ed.post(edit, {"action_item": "Fix it", "priority": "High", "assignee": "Sam Super", "status": "Pending"})
        self.assertEqual(Notification.objects.filter(recipient=self.sam).count(), 1)
        self.ed.post(edit, {"action_item": "Fix it today", "priority": "Low", "assignee": "Sam Super", "status": "Pending"})
        self.assertEqual(Notification.objects.filter(recipient=self.sam).count(), 1)         # same person, no repeat

    def test_assigning_to_yourself_is_silent(self):
        signed_in(self.sam).post(reverse("action_tracker:create"), {"action_item": "Mine", "priority": "Low", "assignee": "Sam Super"})
        self.assertEqual(Notification.objects.filter(recipient=self.sam, kind="assigned").count(), 0)

    def test_supervisor_assignment_notifies_the_person(self):
        job = JobReport.objects.create(manifest=JobManifest.objects.create(job_name="M", client="Client A"), name="Job A")
        self.ed.post(reverse("job_reports:supervisor_add", args=[job.pk]),
                     {"person": self.person.pk, "shift": "night", "start_date": datetime.date.today()})
        n = Notification.objects.get(recipient=self.sam, kind="assigned")
        self.assertIn("Job A", n.message)
        self.assertIn("night shift", n.message)


class PageTests(TestCase):
    def setUp(self):
        self.viewer = make_user("v", Role.VIEWER)
        self.other = make_user("o", Role.VIEWER)
        self.mine = Notification.objects.create(recipient=self.viewer, message="Mine", link=reverse("action_tracker:list"))
        Notification.objects.create(recipient=self.other, message="Theirs")
        self.c = signed_in(self.viewer)

    def test_list_shows_only_my_alerts(self):
        r = self.c.get(reverse("notifications:list"))
        self.assertContains(r, "Mine")
        self.assertNotContains(r, "Theirs")

    def test_bell_count_is_on_every_page(self):
        self.assertContains(self.c.get(reverse("action_tracker:list")), 'id="notif-badge"')
        self.assertEqual(self.c.get(reverse("action_tracker:list")).context["unread_count"], 1)

    def test_opening_marks_it_read_and_goes_to_the_page(self):
        r = self.c.get(reverse("notifications:open", args=[self.mine.pk]))
        self.assertRedirects(r, reverse("action_tracker:list"), fetch_redirect_response=False)
        self.mine.refresh_from_db()
        self.assertIsNotNone(self.mine.read_at)

    def test_cannot_open_someone_elses(self):
        theirs = Notification.objects.get(recipient=self.other)
        self.assertEqual(self.c.get(reverse("notifications:open", args=[theirs.pk])).status_code, 404)

    def test_a_viewer_can_mark_all_read_even_though_viewers_cannot_edit(self):
        self.c.post(reverse("notifications:read_all"))
        self.assertFalse(Notification.objects.filter(recipient=self.viewer, read_at__isnull=True).exists())
        self.assertTrue(Notification.objects.filter(recipient=self.other, read_at__isnull=True).exists())

    def test_poll_returns_count_and_only_newer_alerts(self):
        data = self.c.get(reverse("notifications:poll") + f"?after={self.mine.pk - 1}").json()
        self.assertEqual(data["count"], 1)
        self.assertEqual([n["message"] for n in data["new"]], ["Mine"])
        self.assertEqual(self.c.get(reverse("notifications:poll") + f"?after={self.mine.pk}").json()["new"], [])

    def test_anonymous_users_are_sent_to_login(self):
        self.assertEqual(Client().get(reverse("notifications:list")).status_code, 302)
        self.assertEqual(Client().get(reverse("notifications:poll")).status_code, 302)


class AccessControlTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss", Role.ADMIN)
        self.target = make_user("ed", Role.EDITOR)
        self.person = Personnel.objects.create(employee_id="7", name="Sam Super")
        self.c = signed_in(self.admin)
        self.url = reverse("accounts:access_control")

    def test_toggle_every_change_alerts(self):
        pid = self.target.profile.pk
        self.c.post(self.url, {"profile_id": pid, "toggle_notify": "1"})
        self.target.profile.refresh_from_db()
        self.assertTrue(self.target.profile.notify_all)
        self.c.post(self.url, {"profile_id": pid, "toggle_notify": "1"})
        self.target.profile.refresh_from_db()
        self.assertFalse(self.target.profile.notify_all)

    def test_link_an_account_to_an_employee_once(self):
        pid = self.target.profile.pk
        self.c.post(self.url, {"profile_id": pid, "link_employee": "1", "employee": self.person.pk})
        self.target.profile.refresh_from_db()
        self.assertEqual(self.target.profile.employee, self.person)
        self.c.post(self.url, {"profile_id": self.admin.profile.pk, "link_employee": "1", "employee": self.person.pk})
        self.admin.profile.refresh_from_db()
        self.assertIsNone(self.admin.profile.employee)                      # already taken
        self.c.post(self.url, {"profile_id": pid, "link_employee": "1", "employee": ""})
        self.target.profile.refresh_from_db()
        self.assertIsNone(self.target.profile.employee)

    def test_page_renders(self):
        self.assertContains(self.c.get(self.url), "Every-change alerts")

    def test_changing_a_role_saves_and_the_page_has_a_save_button(self):
        page = self.c.get(self.url).content.decode()
        self.assertNotIn("requestSubmit", page)                     # not every iPad supports it; a Save button is used
        self.assertIn('>Save</button>', page)
        self.c.post(self.url, {"profile_id": self.target.profile.pk, "role": "viewer"})
        self.target.profile.refresh_from_db()
        self.assertEqual(self.target.profile.role, "viewer")


class PollHeaderTests(TestCase):
    def test_poll_answers_are_never_cached(self):
        c = signed_in(make_user("v", Role.VIEWER))
        self.assertEqual(c.get(reverse("notifications:poll")).headers["Cache-Control"], "no-store")




class NewAccountNoticeTests(TestCase):
    def test_an_unverified_signup_is_not_announced_until_the_email_is_verified(self):
        manager = User.objects.create_user("opsmgr", password="x-long-enough-1")
        Profile.objects.filter(user=manager).update(notify_all=True, role=Role.ADMIN)
        newbie = User.objects.create_user("newbie", password="x-long-enough-1", is_active=False)
        self.assertFalse(Notification.objects.filter(message__contains="newbie").exists())
        newbie.is_active = True
        newbie.save(update_fields=["is_active"])
        self.assertEqual(Notification.objects.filter(recipient=manager, message__contains="newbie").count(), 1)
