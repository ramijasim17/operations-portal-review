"""Access levels 1 / 2 / 3 and the approval workflow."""
import contextlib
import datetime

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from approvals import services
from approvals.models import Approval, ApprovalLog
from departments.models import Asset, Classification, Department
from job_manager.models import JobManifest
from job_reports.forms import JobReportForm, supervisor_queryset
from maintenance.models import MaintenanceLog, MaintenanceRecord
from notifications.middleware import _actor
from notifications.models import Notification
from personnel.models import Personnel
from sgs_report.models import SGSReport, SGSStation

from .dept_testing import link_department
from .models import Profile, Role

ZONE = timezone.get_current_timezone()


def make_user(name, role):
    user = User.objects.create_user(username=name, password="x")
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    return User.objects.get(pk=user.pk)          # a fresh object: the one create_user returned caches the default profile


def client_for(user):
    c = Client()
    c.force_login(user)
    return c


@contextlib.contextmanager
def as_actor(user):
    """Act as `user` for ORM work, the way the portal does for a signed-in request."""
    token = _actor.set(user)
    try:
        yield
    finally:
        _actor.reset(token)


class Base(TestCase):
    def setUp(self):
        self.l1 = make_user("level1", Role.VIEWER)
        self.l2 = make_user("level2", Role.EDITOR)
        self.l3 = make_user("level3", Role.APPROVER)
        self.admin = make_user("theadmin", Role.ADMIN)
        self.dept = Department.objects.create(name="WL", location="Yard")
        self.klass = Classification.objects.create(department=self.dept, name="Tools")
        link_department(self.l1, self.dept)               # Level 1 / 2 see only their own department
        link_department(self.l2, self.dept)

    def manifest_by(self, user, **kw):
        with as_actor(user):
            return JobManifest.objects.create(job_name=kw.pop("job_name", "Job A"), client="Client A", department=self.dept, **kw)

    def asset_by(self, user, name="A-1"):
        with as_actor(user):
            return Asset.objects.create(classification=self.klass, name=name)

    def person_by(self, user, name="New Person", emp="900"):
        with as_actor(user):
            return Personnel.objects.create(employee_id=emp, name=name, department=self.dept.name)


class LevelTests(Base):
    def test_what_each_level_can_do(self):
        expected = {
            "level1": (False, False, False, False),
            "level2": (True, True, False, False),
            "level3": (True, True, True, True),
            "theadmin": (True, True, True, True),
        }
        for name, (edit, download, delete, approve) in expected.items():
            p = User.objects.get(username=name).profile
            self.assertEqual((p.can_edit, p.can_download, p.can_delete, p.can_approve), (edit, download, delete, approve), name)
        self.assertEqual([u.profile.is_admin for u in (self.l1, self.l2, self.l3, self.admin)], [False, False, False, True])

    def test_level_names(self):
        self.assertEqual(self.l3.profile.get_role_display(), "Level 3 — Approve")
        self.assertEqual(self.l1.profile.get_role_display(), "Level 1 — View")

    def test_level_1_sees_everything_but_cannot_add_or_download(self):
        c = client_for(self.l1)
        self.assertEqual(c.get(reverse("job_manager:list")).status_code, 200)
        self.assertEqual(c.get(reverse("personnel:list")).status_code, 200)
        self.assertEqual(c.post(reverse("job_manager:create"), {"job_name": "x"}).status_code, 403)
        self.assertEqual(c.get(reverse("sgs_report:battery_log_export")).status_code, 403)

    def test_level_2_can_add_and_download_but_not_delete_or_approve(self):
        c = client_for(self.l2)
        self.assertNotEqual(c.get(reverse("sgs_report:battery_log_export")).status_code, 403)
        asset = self.asset_by(self.admin)
        self.assertEqual(c.post(reverse("departments:asset_delete", args=[asset.pk])).status_code, 403)
        self.assertEqual(c.post(reverse("approvals:bulk"), {"ids": [1]}).status_code, 403)

    def test_level_3_can_delete_and_approve_but_not_manage_accounts(self):
        c = client_for(self.l3)
        asset = self.asset_by(self.admin)
        c.post(reverse("departments:asset_delete", args=[asset.pk]))
        self.assertFalse(Asset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(c.post(reverse("approvals:bulk"), {"ids": []}).status_code, 302)
        page = c.get(reverse("accounts:access_control"), follow=True)
        self.assertEqual(page.redirect_chain[-1][0], reverse("core:dashboard"))            # only an Admin gets in

    def test_access_control_offers_the_four_levels(self):
        page = client_for(self.admin).get(reverse("accounts:access_control"))
        for label in ("Level 1 — View", "Level 2 — Add &amp; edit", "Level 3 — Approve", "Admin"):
            self.assertContains(page, label)
        target = self.l2.profile
        client_for(self.admin).post(reverse("accounts:access_control"), {"profile_id": target.pk, "role": "approver"})
        target.refresh_from_db()
        self.assertEqual(target.role, Role.APPROVER)


class EngineTests(Base):
    def test_level_2_work_is_pending_and_level_3_hears_about_it(self):
        job = self.manifest_by(self.l2)
        approval = Approval.objects.get()
        self.assertEqual((approval.kind, approval.status, approval.submitted_by), ("manifest", "pending", self.l2))
        self.assertFalse(services.is_approved(job, "manifest"))
        n = Notification.objects.get(recipient=self.l3)
        self.assertEqual(n.kind, "approval")
        self.assertIn("waiting for your approval", n.message)
        self.assertFalse(Notification.objects.filter(recipient=self.l2).exists())

    def test_level_3_work_is_approved_on_the_spot(self):
        job = self.manifest_by(self.l3)
        approval = Approval.objects.get()
        self.assertEqual((approval.status, approval.auto, approval.decided_by), ("approved", True, self.l3))
        self.assertTrue(services.is_approved(job, "manifest"))
        self.assertFalse(Notification.objects.filter(kind="approval").exists())

    def test_things_made_outside_the_portal_count_as_approved(self):
        job = JobManifest.objects.create(job_name="Imported", department=self.dept)
        self.assertFalse(Approval.objects.exists())
        self.assertTrue(services.is_approved(job, "manifest"))

    def test_approve_and_reject(self):
        job = self.manifest_by(self.l2)
        approval = Approval.objects.get()
        c = client_for(self.l3)
        c.post(reverse("approvals:decide", args=[approval.pk]), {"decision": "reject", "note": "wrong client"})
        approval.refresh_from_db()
        self.assertEqual((approval.status, approval.note, approval.decided_by), ("rejected", "wrong client", self.l3))
        note = Notification.objects.get(recipient=self.l2)
        self.assertIn("rejected", note.message)
        self.assertIn("wrong client", note.message)
        # a rejected item can be sent again, then approved
        client_for(self.l2).post(reverse("approvals:resubmit", args=[approval.pk]))
        approval.refresh_from_db()
        self.assertEqual(approval.status, "pending")
        c.post(reverse("approvals:decide", args=[approval.pk]), {"decision": "approve"})
        approval.refresh_from_db()
        self.assertEqual(approval.status, "approved")
        self.assertTrue(services.is_approved(job, "manifest"))
        self.assertEqual([l.action for l in ApprovalLog.objects.order_by("id")], ["submitted", "rejected", "resubmitted", "approved"])

    def test_a_decided_item_cannot_be_decided_again(self):
        self.manifest_by(self.l2)
        approval = Approval.objects.get()
        c = client_for(self.l3)
        c.post(reverse("approvals:decide", args=[approval.pk]), {"decision": "approve"})
        self.assertEqual(c.post(reverse("approvals:decide", args=[approval.pk]), {"decision": "reject"}).status_code, 404)

    def test_bulk_approve(self):
        for i in range(3):
            self.manifest_by(self.l2, job_name=f"Job {i}")
        ids = list(Approval.objects.values_list("pk", flat=True))
        client_for(self.l3).post(reverse("approvals:bulk"), {"ids": ids})
        self.assertEqual(Approval.objects.filter(status="approved").count(), 3)

    def test_inbox_for_each_level(self):
        self.manifest_by(self.l2, job_name="Mine")
        self.manifest_by(self.admin, job_name="Theirs")
        page = client_for(self.l3).get(reverse("approvals:inbox"))
        self.assertContains(page, "Mine")
        self.assertContains(page, "Approve")
        page = client_for(self.l2).get(reverse("approvals:inbox"))
        self.assertContains(page, "My requests")
        self.assertNotContains(page, "waiting for approval</div>")                   # no decision buttons for Level 2
        self.assertEqual(client_for(self.l1).get(reverse("approvals:inbox")).status_code, 200)

    def test_sidebar_and_dashboard_show_the_waiting_count_to_level_3_only(self):
        self.manifest_by(self.l2)
        self.assertEqual(client_for(self.l3).get(reverse("core:dashboard")).context["pending_approvals"], 1)
        self.assertContains(client_for(self.l3).get(reverse("core:dashboard")), "waiting for your approval")
        self.assertNotIn("pending_approvals", client_for(self.l2).get(reverse("core:dashboard")).context)

    def test_deleting_the_record_clears_its_approval(self):
        job = self.manifest_by(self.l2)
        job.delete()
        self.assertFalse(Approval.objects.exists())


class ManifestTests(Base):
    def post_manifest(self, user, status):
        return client_for(user).post(reverse("job_manager:create"), {
            "job_name": "Well job", "client": "", "location": "", "status": status, "department": self.dept.pk})

    def test_a_pending_manifest_cannot_be_dispatched(self):
        self.post_manifest(self.l2, "Active")
        job = JobManifest.objects.get()
        self.assertEqual(job.status, "Draft")                                # held back
        client_for(self.l3).post(reverse("approvals:decide", args=[Approval.objects.get().pk]), {"decision": "approve"})
        r = client_for(self.l2).post(reverse("job_manager:edit", args=[job.pk]), {
            "job_name": "Well job", "client": "", "location": "", "status": "Active", "department": self.dept.pk})
        job.refresh_from_db()
        self.assertEqual(job.status, "Active")                               # fine once approved

    def test_a_level_3_manifest_can_be_dispatched_straight_away(self):
        self.post_manifest(self.l3, "Active")
        self.assertEqual(JobManifest.objects.get().status, "Active")

    def test_job_reports_can_only_be_made_from_approved_manifests(self):
        pending = self.manifest_by(self.l2, job_name="Pending one")
        approved = self.manifest_by(self.l3, job_name="Approved one")
        choices = set(JobReportForm().fields["manifest"].queryset.values_list("pk", flat=True))
        self.assertEqual(choices, {approved.pk})
        client_for(self.l3).post(reverse("approvals:decide", args=[Approval.objects.get(object_id=pending.pk).pk]), {"decision": "approve"})
        self.assertEqual(set(JobReportForm().fields["manifest"].queryset.values_list("pk", flat=True)), {pending.pk, approved.pk})

    def test_list_and_detail_show_the_state(self):
        job = self.manifest_by(self.l2)
        self.assertContains(client_for(self.l3).get(reverse("job_manager:list")), "Pending approval")
        self.assertContains(client_for(self.l3).get(reverse("job_manager:detail", args=[job.pk])), "Waiting for approval")


class AssetAndPersonnelTests(Base):
    def test_a_pending_asset_cannot_go_on_a_manifest(self):
        asset = self.asset_by(self.l2, "PEND-1")
        ok = self.asset_by(self.l3, "OK-1")
        job = self.manifest_by(self.l3)
        client_for(self.l3).post(reverse("job_manager:detail", args=[job.pk]), {"add_serials": "1", "add_serial": ["PEND-1", "OK-1"]})
        job.refresh_from_db()
        self.assertEqual(job.serial_list(), ["OK-1"])
        client_for(self.l3).post(reverse("approvals:decide", args=[Approval.objects.get(object_id=asset.pk, kind="asset").pk]), {"decision": "approve"})
        client_for(self.l3).post(reverse("job_manager:detail", args=[job.pk]), {"add_serials": "1", "add_serial": ["PEND-1"]})
        job.refresh_from_db()
        self.assertEqual(sorted(job.serial_list()), ["OK-1", "PEND-1"])

    def test_level_2_asks_for_a_removal_and_level_3_approves_it(self):
        asset = self.asset_by(self.l3, "GONE-1")
        client_for(self.l2).post(reverse("departments:asset_request_removal", args=[asset.pk]))
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())                       # stays until approved
        approval = Approval.objects.get(kind="asset_removal")
        self.assertEqual(approval.status, "pending")
        # while the request is open the asset can't be put on a manifest
        self.assertEqual(services.blocked_asset_names(["GONE-1"]), {"GONE-1"})
        client_for(self.l3).post(reverse("approvals:decide", args=[approval.pk]), {"decision": "approve"})
        self.assertFalse(Asset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(Approval.objects.exists())
        self.assertTrue(ApprovalLog.objects.filter(kind="asset_removal", action="approved").exists())

    def test_a_declined_removal_keeps_the_asset(self):
        asset = self.asset_by(self.l3, "KEEP-1")
        client_for(self.l2).post(reverse("departments:asset_request_removal", args=[asset.pk]))
        client_for(self.l3).post(reverse("approvals:decide", args=[Approval.objects.get(kind="asset_removal").pk]),
                                 {"decision": "reject", "note": "still needed"})
        self.assertTrue(Asset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(services.blocked_asset_names(["KEEP-1"]), set())

    def test_level_3_removes_directly(self):
        asset = self.asset_by(self.l3, "DIRECT-1")
        client_for(self.l3).post(reverse("departments:asset_request_removal", args=[asset.pk]))
        self.assertFalse(Asset.objects.filter(pk=asset.pk).exists())

    def test_the_asset_page_offers_the_right_button(self):
        asset = self.asset_by(self.l3, "BTN-1")
        url = reverse("departments:classification_detail", args=[self.klass.pk])
        self.assertContains(client_for(self.l2).get(url), "Request removal")
        page = client_for(self.l3).get(url)
        self.assertNotContains(page, "Request removal")
        self.assertContains(page, "Delete this asset?")

    def test_a_pending_person_is_not_offered_as_a_supervisor_or_an_assignee(self):
        person = self.person_by(self.l2, "Pending Pat", "901")
        self.assertNotIn(person, supervisor_queryset())
        from action_tracker.forms import ActionForm
        self.assertNotIn("Pending Pat", [v for v, _ in ActionForm().fields["assignee"].choices])
        client_for(self.l3).post(reverse("approvals:decide", args=[Approval.objects.get(object_id=person.pk).pk]), {"decision": "approve"})
        self.assertIn(person, supervisor_queryset())
        self.assertIn("Pending Pat", [v for v, _ in ActionForm().fields["assignee"].choices])

    def test_a_pending_person_cannot_sign_up_yet(self):
        from accounts.forms import SignupForm
        self.person_by(self.l2, "Pending Pat Smith", "902")
        form = SignupForm({"employee_id": "902", "username": "pat", "email": "pat@x.com",
                           "password": "S3cure!pass", "password_confirm": "S3cure!pass"})
        form.is_valid()
        self.assertIn("waiting for approval", " ".join(form.errors.get("employee_id", [])))

    def test_personnel_removal_request(self):
        person = self.person_by(self.l3, "Leaver", "903")
        client_for(self.l2).post(reverse("personnel:request_removal", args=[person.pk]))
        self.assertTrue(Personnel.objects.filter(pk=person.pk).exists())
        client_for(self.l3).post(reverse("approvals:decide", args=[Approval.objects.get(kind="personnel_removal").pk]), {"decision": "approve"})
        self.assertFalse(Personnel.objects.filter(pk=person.pk).exists())

    def test_personnel_pages_show_the_badges_and_the_request_button(self):
        person = self.person_by(self.l2, "Badge Bob", "904")
        page = client_for(self.l2).get(reverse("personnel:list"))
        self.assertContains(page, "Pending approval")
        self.assertContains(page, "Request removal")
        self.assertContains(client_for(self.l3).get(reverse("personnel:detail", args=[person.pk])), "Waiting for approval")


class MaintenanceTests(Base):
    def setUp(self):
        super().setUp()
        Asset.objects.create(classification=self.klass, name="M-1")
        self.record = MaintenanceRecord.objects.create(asset_serial="M-1", schedule="repeating", every_count=1, every_unit="month",
                                                       due_date=timezone.localdate() - datetime.timedelta(days=2))

    def test_level_2_report_waits_and_the_schedule_moves_on_approval(self):
        before = self.record.due_date
        self.record.complete(datetime.date(2026, 5, 10), user=self.l2, notes="done")
        self.record.refresh_from_db()
        self.assertEqual(self.record.due_date, before)
        approval = Approval.objects.get(kind="maintenance_log")
        client_for(self.l3).post(reverse("approvals:decide", args=[approval.pk]), {"decision": "approve"})
        self.record.refresh_from_db()
        self.assertEqual(self.record.due_date, datetime.date(2026, 6, 10))
        self.assertEqual(self.record.last_done, datetime.date(2026, 5, 10))

    def test_a_rejected_report_never_moves_the_schedule(self):
        before = self.record.due_date
        self.record.complete(datetime.date(2026, 5, 10), user=self.l2)
        client_for(self.l3).post(reverse("approvals:decide", args=[Approval.objects.get(kind="maintenance_log").pk]),
                                 {"decision": "reject", "note": "no photo"})
        self.record.refresh_from_db()
        self.assertEqual(self.record.due_date, before)

    def test_level_3_report_moves_it_at_once_and_is_on_record(self):
        self.record.complete(datetime.date(2026, 5, 10), user=self.l3)
        self.record.refresh_from_db()
        self.assertEqual(self.record.due_date, datetime.date(2026, 6, 10))
        self.assertEqual(Approval.objects.get(kind="maintenance_log").status, "approved")

    def test_a_failure_report_is_not_held_for_approval(self):
        self.record.report_failure(datetime.date(2026, 5, 10), user=self.l2)
        self.record.refresh_from_db()
        self.assertEqual(self.record.due_date, datetime.date(2026, 5, 10))
        self.assertFalse(Approval.objects.exists())


class SGSTests(Base):
    def setUp(self):
        super().setUp()
        self.report = SGSReport.objects.create(well_name="R-1", client="Client A", run_start=timezone.make_aware(datetime.datetime(2026, 4, 18, 8), ZONE))
        self.excel = reverse("sgs_report:export_excel", args=[self.report.pk])
        self.pdf = reverse("sgs_report:generate_pdf", args=[self.report.pk])

    def test_level_2_cannot_open_the_result_or_final_report_until_approved(self):
        c = client_for(self.l2)
        r = c.get(self.excel)
        self.assertEqual(r.status_code, 302)
        self.assertIn("tab=result", r["Location"])
        self.assertEqual(c.get(self.pdf).status_code, 302)
        self.assertEqual(c.post(reverse("sgs_report:generate", args=[self.report.pk])).status_code, 302)
        self.assertEqual(c.get(reverse("sgs_report:client_file", args=[self.report.pk, "bottom"])).status_code, 302)

    def test_level_3_can_always_open_them(self):
        r = client_for(self.l3).get(self.excel)
        self.assertEqual(r.status_code, 200)

    def test_send_for_approval_then_approve_releases_it(self):
        c2, c3 = client_for(self.l2), client_for(self.l3)
        c2.post(reverse("approvals:submit_sgs", args=[self.report.pk, "sgs_result"]))
        approval = Approval.objects.get(kind="sgs_result")
        self.assertEqual(approval.status, "pending")
        self.assertEqual(c2.get(self.excel).status_code, 302)
        c3.post(reverse("approvals:decide", args=[approval.pk]), {"decision": "approve"})
        self.assertEqual(c2.get(self.excel).status_code, 200)
        self.assertEqual(c2.get(self.pdf).status_code, 302)                               # the final report is a separate approval

    def test_level_3_approves_a_report_in_one_step(self):
        client_for(self.l3).post(reverse("approvals:submit_sgs", args=[self.report.pk, "sgs_final"]))
        self.assertEqual(Approval.objects.get(kind="sgs_final").status, "approved")

    def test_a_level_2_change_after_approval_reopens_it_but_level_3_keeps_it(self):
        client_for(self.l3).post(reverse("approvals:submit_sgs", args=[self.report.pk, "sgs_result"]))
        with as_actor(self.l3):
            SGSStation.objects.create(report=self.report, step_no=1, md_mgl=100)
        self.assertEqual(Approval.objects.get(kind="sgs_result").status, "approved")
        with as_actor(self.l2):
            SGSStation.objects.create(report=self.report, step_no=2, md_mgl=200)
        self.assertEqual(Approval.objects.get(kind="sgs_result").status, "pending")
        self.assertTrue(ApprovalLog.objects.filter(kind="sgs_result", action="reopened").exists())

    def test_a_report_never_sent_is_not_reopened(self):
        with as_actor(self.l2):
            SGSStation.objects.create(report=self.report, step_no=1, md_mgl=100)
        self.assertFalse(Approval.objects.exists())

    def test_the_tabs_show_the_state_and_the_right_buttons(self):
        url = reverse("sgs_report:detail", args=[self.report.pk])
        page = client_for(self.l2).get(url + "?tab=result")
        self.assertContains(page, "Send for approval")
        self.assertContains(page, "&#128274;")                                             # the locked Excel / PDF buttons
        page = client_for(self.l3).get(url + "?tab=result")
        self.assertContains(page, "Approve")
        self.assertNotContains(page, "&#128274;")
        client_for(self.l2).post(reverse("approvals:submit_sgs", args=[self.report.pk, "sgs_final"]))
        self.assertContains(client_for(self.l3).get(url + "?tab=final"), "Waiting for approval")

    def test_unknown_approval_kind_is_a_404(self):
        self.assertEqual(client_for(self.l2).post(reverse("approvals:submit_sgs", args=[self.report.pk, "nonsense"])).status_code, 404)


class NotificationPageTests(Base):
    def test_approval_notifications_have_their_own_tab(self):
        self.manifest_by(self.l2)
        page = client_for(self.l3).get(reverse("notifications:list") + "?kind=approval")
        self.assertContains(page, "waiting for your approval")
