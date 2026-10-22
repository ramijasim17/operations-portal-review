"""Action Tracker: who raised actions, of what type, per month, and time to close."""
import datetime

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from action_tracker import insights
from action_tracker.forms import ActionForm
from action_tracker.models import INTERNAL, Action
from wire_management.models import Client

from .dept_testing import make_action, test_department
from .tests_actions_approvals import login
from .models import Role

TODAY = timezone.localdate()
D = datetime.date


def make(client="Client A", kind="equipment", raised=None, closed=None, **kw):
    a = make_action(action_item="x", client=client, action_type=kind, raised_date=raised, **kw)
    if closed:
        Action.objects.filter(pk=a.pk).update(status="Completed", closed_date=closed)
        a.refresh_from_db()
    return a


class ClosedDateTests(TestCase):
    def test_completing_stamps_the_closed_date_and_reopening_clears_it(self):
        a = make_action(action_item="x", raised_date=TODAY - datetime.timedelta(days=4))
        a.status = "Completed"
        a.save(update_fields=["status"])
        a.refresh_from_db()
        self.assertEqual(a.closed_date, TODAY)
        self.assertEqual(a.days_to_close, 4)
        a.status = "Pending"
        a.save()
        a.refresh_from_db()
        self.assertIsNone(a.closed_date)
        self.assertIsNone(a.days_to_close)

    def test_one_click_mark_done_stamps_it(self):
        login(self.client, Role.EDITOR)
        a = make_action(action_item="x", raised_date=TODAY)
        self.client.post(reverse("action_tracker:set_status", args=[a.pk]))
        a.refresh_from_db()
        self.assertEqual(a.closed_date, TODAY)

    def test_editing_an_old_completed_action_does_not_invent_a_closed_date(self):
        a = make_action(action_item="old")
        Action.objects.filter(pk=a.pk).update(status="Completed")         # as it was before the dates existed
        a = Action.objects.get(pk=a.pk)
        a.comments = "edited"
        a.save()
        a.refresh_from_db()
        self.assertIsNone(a.closed_date)
        self.assertIsNone(a.days_to_close)

    def test_a_checklist_finishing_the_action_stamps_it(self):
        a = make_action(action_item="x", raised_date=TODAY)
        a.steps.create(step_description="s", is_completed=True)
        a.sync_status_from_steps()
        a.refresh_from_db()
        self.assertEqual(a.closed_date, TODAY)

    def test_age_of_an_open_action(self):
        a = make_action(action_item="x", raised_date=TODAY - datetime.timedelta(days=6))
        self.assertEqual(a.age_days, 6)


class AnalyticsTests(TestCase):
    def setUp(self):
        self.actions = [
            make("Client A", "equipment", D(2026, 8, 3), D(2026, 8, 5)),     # 2 days
            make("Client A", "equipment", D(2026, 8, 10), D(2026, 8, 20)),   # 10 days
            make("Client A", "personnel", D(2026, 9, 1)),                    # open
            make("CLIENT B", "documentation", D(2026, 9, 2), D(2026, 9, 3)),
        ]

    def test_timing_uses_only_closed_actions_with_dates(self):
        t = insights.timing(self.actions)
        self.assertEqual((t["count"], t["average"], t["median"], t["slowest"]), (3, 4.3, 2, 10))

    def test_undated_actions_are_left_out_of_the_period(self):
        make("Client A", "equipment", None)
        self.assertEqual(len(insights.in_period(Action.objects.all(), D(2026, 8, 1), D(2026, 9, 30))), 4)

    def test_group_by_client_counts_shares_and_averages(self):
        rows = insights.group(self.actions, lambda a: a.client, insights.client_label)
        roo = rows[0]
        self.assertEqual((roo["label"], roo["raised"], roo["open"], roo["closed"], roo["average"]), ("Client A", 3, 1, 2, 6.0))
        self.assertEqual(roo["share"], 75)
        self.assertEqual(rows[1]["label"], "CLIENT B")

    def test_a_client_with_nothing_closed_has_no_average(self):
        rows = insights.group([self.actions[2]], lambda a: a.client, insights.client_label)
        self.assertIsNone(rows[0]["average"])

    def test_monthly_counts_include_empty_months_and_stack_by_client(self):
        m = insights.per_month(self.actions, D(2026, 7, 1), D(2026, 9, 30), split_by_client=True)
        self.assertEqual([b["total"] for b in m["bars"]], [0, 2, 2])
        aug = m["bars"][1]
        self.assertEqual({s["label"]: s["count"] for s in aug["segments"]}, {"Client A": 2})
        sep = m["bars"][2]
        self.assertEqual({s["label"]: s["count"] for s in sep["segments"]}, {"CLIENT B": 1, "Client A": 1})

    def test_filters(self):
        self.assertEqual(len(insights.apply_filters(self.actions, "Client A", "equipment")), 2)
        self.assertEqual(len(insights.apply_filters(self.actions, "", "")), 4)

    def test_slowest_open_lists_the_oldest_first(self):
        old = make_action(action_item="old", raised_date=TODAY - datetime.timedelta(days=30))
        new = make_action(action_item="new", raised_date=TODAY - datetime.timedelta(days=2))
        self.assertEqual(insights.slowest_open([new, old])[0], old)


class FormTests(TestCase):
    def setUp(self):
        Client.objects.create(name="Client A")

    def data(self, **kw):
        base = {"action_item": "Fix it", "client": "Client A", "action_type": "equipment", "priority": "High", "department": test_department().pk}
        base.update(kw)
        return base

    def test_new_action_defaults_the_raised_date_to_today(self):
        form = ActionForm(self.data())
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().raised_date, TODAY)

    def test_client_is_a_dropdown_with_a_neos_internal_choice(self):
        form = ActionForm()
        values = [v for v, _ in form.fields["client"].choices]
        self.assertIn("Client A", values)
        self.assertIn(INTERNAL, values)
        self.assertFalse(ActionForm(self.data(client="Made Up")).is_valid())

    def test_a_future_raised_date_is_rejected(self):
        self.assertFalse(ActionForm(self.data(raised_date=TODAY + datetime.timedelta(days=2))).is_valid())

    def test_the_jobs_client_fills_in_a_blank_client(self):
        from job_manager.models import JobManifest
        from job_reports.models import JobReport
        manifest = JobManifest.objects.create(job_name="M", client="Client A")
        job = JobReport.objects.create(name="J1", manifest=manifest)
        form = ActionForm(self.data(client="", job_report=job.pk))
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().client, "Client A")

    def test_renaming_a_client_in_the_shared_list_updates_actions(self):
        from wire_management.lists import rename
        a = make_action(action_item="x", client="Client A")
        rename("client", Client.objects.get(name="Client A"), "Client A Ltd")
        a.refresh_from_db()
        self.assertEqual(a.client, "Client A Ltd")


class InsightsPageTests(TestCase):
    def setUp(self):
        login(self.client, Role.EDITOR)
        Client.objects.create(name="Client A")
        self.a = make("Client A", "equipment", TODAY - datetime.timedelta(days=5), TODAY)
        make("Client A", "personnel", TODAY - datetime.timedelta(days=1))
        make_action(action_item="old")           # no raised date

    def test_page_renders_with_numbers(self):
        r = self.client.get(reverse("action_tracker:insights") + "?period=this_month")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Action insights")
        self.assertContains(r, "no raised date")            # the old action is mentioned, not counted

    def test_page_filters_by_client_and_type(self):
        url = reverse("action_tracker:insights")
        for q in ("?client=Client A", "?client=Client A&type=equipment", "?period=last_3_months&type=personnel", "?client=Nobody"):
            self.assertEqual(self.client.get(url + q).status_code, 200)

    def test_viewers_can_read_it(self):
        self.client.logout()
        login(self.client, Role.VIEWER, "v")
        self.assertEqual(self.client.get(reverse("action_tracker:insights")).status_code, 200)

    def test_list_and_detail_show_client_and_type(self):
        self.assertContains(self.client.get(reverse("action_tracker:list") + "?tab=all"), "Equipment")
        self.assertContains(self.client.get(reverse("action_tracker:detail", args=[self.a.pk])), "closed in")

    def test_query_count_does_not_grow_with_actions(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        url = reverse("action_tracker:insights")
        self.client.get(url)                      # warm-up: the first visit also records "last seen"
        with CaptureQueriesContext(connection) as few:
            self.client.get(url)
        for _ in range(15):
            make("Client A", "equipment", TODAY - datetime.timedelta(days=3), TODAY)
        with CaptureQueriesContext(connection) as many:
            self.client.get(url)
        self.assertEqual(len(few), len(many))
