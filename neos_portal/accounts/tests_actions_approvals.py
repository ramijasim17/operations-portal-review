"""Action Tracker and Work Approval & SOC."""
import datetime

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from action_tracker.forms import ActionForm
from action_tracker.models import Action, ActionStep
from personnel.models import Personnel
from work_approval.forms import WARecordForm
from work_approval.models import WAFileType, WARecord
from wire_management.models import Location

from .dept_testing import link_department, make_action
from .models import Profile, Role

TODAY = timezone.localdate()


def days(n):
    return TODAY + datetime.timedelta(days=n)


def login(client, role, name="u"):
    user = User.objects.create_user(username=f"{name}_{role}", password="x")
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    link_department(user)
    client.force_login(user)
    return user


class ActionTrackerTests(TestCase):
    def setUp(self):
        login(self.client, Role.EDITOR)

    def test_open_actions_sort_by_urgency_and_completed_go_last(self):
        done = make_action(action_item="done", status="Completed", due_date=days(-10))
        later = make_action(action_item="later", due_date=days(20))
        overdue = make_action(action_item="overdue", due_date=days(-5))
        undated = make_action(action_item="undated")
        soon_high = make_action(action_item="soon high", due_date=days(2), priority="High")
        ordered = sorted([done, later, overdue, undated, soon_high], key=Action.urgency_key)
        self.assertEqual([a.action_item for a in ordered], ["overdue", "soon high", "later", "undated", "done"])

    def test_overdue_only_counts_open_actions(self):
        make_action(action_item="a", due_date=days(-1))
        make_action(action_item="b", due_date=days(-1), status="Completed")
        html = self.client.get(reverse("action_tracker:list"), {"tab": "overdue"}).content.decode()
        self.assertIn("a</a>", html)
        self.assertNotIn(">b</a>", html)

    def test_tabs_and_filters_render(self):
        make_action(action_item="Fix pump", assignee="Zain", priority="High", due_date=days(1))
        for tab in ("open", "overdue", "completed", "all"):
            self.assertEqual(self.client.get(reverse("action_tracker:list"), {"tab": tab}).status_code, 200)
        page = self.client.get(reverse("action_tracker:list"), {"q": "pump", "priority": "High", "assignee": "Zain"})
        self.assertContains(page, "Fix pump")
        self.assertNotContains(self.client.get(reverse("action_tracker:list"), {"assignee": "Nobody"}), "Fix pump")

    def test_an_action_can_be_edited(self):
        action = make_action(action_item="old", due_date=days(3))
        response = self.client.post(reverse("action_tracker:edit", args=[action.pk]), {
            "action_item": "new title", "assignee": "", "priority": "High", "due_date": str(days(9)),
            "status": "In Progress", "comments": "c"})
        self.assertEqual(response.status_code, 302)
        action.refresh_from_db()
        self.assertEqual((action.action_item, action.priority, action.status, action.due_date),
                         ("new title", "High", "In Progress", days(9)))

    def test_assignee_dropdown_uses_active_personnel_and_keeps_old_names(self):
        Personnel.objects.create(employee_id="1", name="Ali Active", status="Active")
        Personnel.objects.create(employee_id="2", name="Ivan Inactive", status="Inactive")
        names = [c[0] for c in ActionForm().fields["assignee"].choices]
        self.assertIn("Ali Active", names)
        self.assertNotIn("Ivan Inactive", names)
        legacy = make_action(action_item="x", assignee="Zain")
        self.assertIn("Zain", [c[0] for c in ActionForm(instance=legacy).fields["assignee"].choices])

    def test_new_form_has_steps_and_no_status_while_edit_is_the_reverse(self):
        self.assertIn("steps_input", ActionForm().fields)
        self.assertNotIn("status", ActionForm().fields)
        action = make_action(action_item="x")
        self.assertIn("status", ActionForm(instance=action).fields)
        self.assertNotIn("steps_input", ActionForm(instance=action).fields)

    def test_one_click_done_ticks_every_step_and_reopen_restores(self):
        action = make_action(action_item="x")
        ActionStep.objects.create(action=action, step_description="s1")
        ActionStep.objects.create(action=action, step_description="s2")
        self.client.post(reverse("action_tracker:set_status", args=[action.pk]))
        action.refresh_from_db()
        self.assertEqual(action.status, "Completed")
        self.assertEqual(action.steps.filter(is_completed=True).count(), 2)
        self.client.post(reverse("action_tracker:set_status", args=[action.pk]))
        action.refresh_from_db()
        self.assertEqual(action.status, "Pending")

    def test_closing_a_step_stamps_the_time_and_writes_a_note(self):
        action = make_action(action_item="x", comments="old note")
        step = ActionStep.objects.create(action=action, step_description="Order spare")
        url = reverse("action_tracker:detail", args=[action.pk])
        self.client.post(url, {"toggle_step": step.pk})
        step.refresh_from_db(); action.refresh_from_db()
        self.assertTrue(step.is_completed and step.completed_at and step.completed_by)
        self.assertTrue(action.comments.startswith("old note\n["))
        self.assertIn("Step closed: Order spare by", action.comments)
        self.assertContains(self.client.get(url), "closed ")
        self.client.post(url, {"toggle_step": step.pk})                        # untick
        step.refresh_from_db(); action.refresh_from_db()
        self.assertIsNone(step.completed_at)
        self.assertIn("Step reopened: Order spare", action.comments)

    def test_mark_done_closes_every_open_step_with_a_stamp(self):
        action = make_action(action_item="y")
        ActionStep.objects.create(action=action, step_description="a")
        self.client.post(reverse("action_tracker:set_status", args=[action.pk]))
        self.assertTrue(ActionStep.objects.get().completed_at)
        action.refresh_from_db(); self.assertIn("Step closed: a", action.comments)

    def test_checklist_drives_the_status(self):
        action = make_action(action_item="x")
        s1 = ActionStep.objects.create(action=action, step_description="s1")
        s2 = ActionStep.objects.create(action=action, step_description="s2")
        url = reverse("action_tracker:detail", args=[action.pk])
        self.client.post(url, {"toggle_step": s1.pk})
        action.refresh_from_db(); self.assertEqual(action.status, "In Progress")
        self.client.post(url, {"toggle_step": s2.pk})
        action.refresh_from_db(); self.assertEqual(action.status, "Completed")
        self.client.post(url, {"add_step": "1", "step_description": "one more"})   # a new open step reopens it
        action.refresh_from_db(); self.assertEqual(action.status, "In Progress")
        self.client.post(url, {"delete_step": ActionStep.objects.get(step_description="one more").pk})
        action.refresh_from_db(); self.assertEqual(action.status, "Completed")

    def test_viewer_can_read_but_not_change(self):
        action = make_action(action_item="x")
        login(self.client, Role.VIEWER, "v")
        self.assertEqual(self.client.get(reverse("action_tracker:detail", args=[action.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("action_tracker:set_status", args=[action.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("action_tracker:edit", args=[action.pk]), {}).status_code, 403)

    def test_only_admin_deletes(self):
        action = make_action(action_item="x")
        self.assertEqual(self.client.post(reverse("action_tracker:delete", args=[action.pk])).status_code, 403)
        login(self.client, Role.ADMIN, "a")
        self.client.post(reverse("action_tracker:delete", args=[action.pk]))
        self.assertFalse(Action.objects.filter(pk=action.pk).exists())


class WorkApprovalTests(TestCase):
    def setUp(self):
        login(self.client, Role.EDITOR)
        self.wa = WAFileType.objects.create(type_name="Work Approval")
        self.ato = WAFileType.objects.create(type_name="ATO")

    def rec(self, file_type=None, expiry=None, details="d", has_file=True, **kw):
        return WARecord.objects.create(file_type=file_type or self.wa, expiry_date=expiry, details=details,
                                       file="wa_files/x.pdf" if has_file else "", **kw)

    def test_status_wording(self):
        self.assertEqual(self.rec(expiry=days(-2)).status_label, "Expired")
        self.assertEqual(self.rec(expiry=days(5)).status_label, "Expiring soon")
        self.assertEqual(self.rec(expiry=days(90)).status_label, "Valid")
        self.assertEqual(self.rec().status_label, "No expiry date")
        self.assertEqual(self.rec(has_file=False).status_label, "No file")

    def test_sort_puts_the_most_urgent_first_and_undated_last(self):
        valid = self.rec(expiry=days(90), details="valid")
        undated = self.rec(details="undated")
        soon = self.rec(expiry=days(6), details="soon")
        expired_long = self.rec(expiry=days(-40), details="expired long")
        expired_recent = self.rec(expiry=days(-1), details="expired recent")
        nofile = self.rec(has_file=False, details="nofile")
        order = sorted([valid, undated, soon, expired_long, expired_recent, nofile], key=WARecord.urgency_key)
        self.assertEqual([r.details for r in order],
                         ["expired long", "expired recent", "soon", "valid", "undated", "nofile"])

    def test_tabs_search_and_status_filter(self):
        self.rec(details="Main Field 24h", expiry=days(3), location_name="South Field")
        self.rec(file_type=self.ato, details="Crane 14A", expiry=days(-3))
        page = lambda **p: self.client.get(reverse("work_approval:list"), p).content.decode()
        self.assertIn("Main Field 24h", page()); self.assertIn("Crane 14A", page())
        self.assertNotIn("Crane 14A", page(file_type=self.wa.pk))
        self.assertIn("Main Field 24h", page(q="south"))                       # searches the location too
        self.assertNotIn("Main Field 24h", page(status="expired"))
        self.assertIn("Crane 14A", page(status="expired"))

    def test_renew_prefills_and_keeps_the_old_record(self):
        old = self.rec(expiry=days(-1), details="Crane 14A", location_name="North")
        Location.objects.create(name="North")
        form_page = self.client.get(reverse("work_approval:renew", args=[old.pk]))
        self.assertContains(form_page, "Renew")
        self.assertContains(form_page, "Crane 14A")
        response = self.client.post(reverse("work_approval:renew", args=[old.pk]), {
            "file_type": self.wa.pk, "details": "Crane 14A", "location_name": "North",
            "expiry_date": str(days(180)), "file": SimpleUploadedFile("new.pdf", b"%PDF-1.4")})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(WARecord.objects.count(), 2)
        old.refresh_from_db()
        self.assertEqual(old.expiry_date, days(-1))                          # untouched
        self.assertTrue(WARecord.objects.filter(expiry_date=days(180), details="Crane 14A").exists())

    def test_renewal_needs_the_new_file_and_date(self):
        old = self.rec(expiry=days(-1))
        url = reverse("work_approval:renew", args=[old.pk])
        base = {"file_type": self.wa.pk, "details": "d"}
        self.assertEqual(self.client.post(url, base | {"expiry_date": str(days(30))}).status_code, 200)   # no file
        self.assertEqual(self.client.post(url, base | {"file": SimpleUploadedFile("n.pdf", b"x")}).status_code, 200)  # no date
        self.assertEqual(WARecord.objects.count(), 1)

    def test_only_expiring_or_expired_records_offer_renew(self):
        self.rec(expiry=days(-1), details="expired one")
        self.rec(expiry=days(200), details="fine one")
        html = self.client.get(reverse("work_approval:list")).content.decode()
        self.assertEqual(html.count("/renew/"), 1)

    def test_viewer_cannot_renew(self):
        old = self.rec(expiry=days(-1))
        login(self.client, Role.VIEWER, "v")
        self.assertEqual(self.client.post(reverse("work_approval:renew", args=[old.pk]), {}).status_code, 403)

    def test_location_is_a_shared_list_dropdown(self):
        Location.objects.create(name="Main Field")
        self.assertEqual([c[0] for c in WARecordForm().fields["location_name"].choices], ["", "Main Field"])
