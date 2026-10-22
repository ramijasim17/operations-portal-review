"""NPT tracker: jobs, units, causes, per-client bases, KPIs (hours and %), periods and charts."""
import datetime
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.auth.models import User
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from departments.models import Asset, Classification, Department
from job_manager.models import JobManifest
from job_reports.forms import JobReportForm
from job_reports.models import JobReport
from sqb import npt
from sqb.forms import ClientNPTSettingForm, NPTKPIForm, SQBTrackerEntryForm
from sqb.models import NPTKPI, ClientNPTSetting, SQBTrackerEntry
from wire_management.models import Client

from .models import Profile, Role

D = datetime.date
TODAY = D(2026, 9, 30)


# ═══ pure calculations (no database) ═══════════════════════════════════════
class PeriodTests(SimpleTestCase):
    def period(self, **params):
        return npt.parse_period(params, today=TODAY)

    def test_named_periods(self):
        p = self.period(period="this_month")
        self.assertEqual((p["start"], p["end"]), (D(2026, 9, 1), D(2026, 9, 30)))
        p = self.period(period="last_month")
        self.assertEqual((p["start"], p["end"]), (D(2026, 8, 1), D(2026, 8, 31)))
        p = self.period(period="last_3_months")
        self.assertEqual((p["start"], p["end"]), (D(2026, 7, 1), D(2026, 9, 30)))
        p = self.period(period="this_year")
        self.assertEqual((p["start"], p["end"]), (D(2026, 1, 1), D(2026, 12, 31)))

    def test_default_is_last_three_months(self):
        self.assertEqual(self.period()["key"], "last_3_months")

    def test_specific_month(self):
        p = self.period(period="month", month="2026-02")
        self.assertEqual((p["start"], p["end"], p["label"]), (D(2026, 2, 1), D(2026, 2, 28), "February 2026"))

    def test_custom_range_and_swapped_dates(self):
        p = self.period(period="custom", **{"from": "2026-03-10", "to": "2026-04-02"})
        self.assertEqual((p["start"], p["end"]), (D(2026, 3, 10), D(2026, 4, 2)))
        swapped = self.period(period="custom", **{"from": "2026-04-02", "to": "2026-03-10"})
        self.assertEqual((swapped["start"], swapped["end"]), (D(2026, 3, 10), D(2026, 4, 2)))

    def test_bad_input_falls_back_instead_of_crashing(self):
        for params in ({"period": "month", "month": "nonsense"}, {"period": "custom", "from": "x", "to": "y"},
                       {"period": "custom"}, {"period": "banana"}):
            self.assertEqual(self.period(**params)["key"], "last_3_months", params)

    def test_months_between_and_year_boundary(self):
        self.assertEqual(npt.months_between(D(2026, 11, 15), D(2027, 2, 3)),
                         [D(2026, 11, 1), D(2026, 12, 1), D(2027, 1, 1), D(2027, 2, 1)])


class E:
    """A stand-in for an entry."""
    def __init__(self, date, hours, unit_id=None, client="", category="", unit=None, job_report_id=None):
        self.date, self.npt_hours, self.unit_id, self.client, self.category, self.unit = date, hours, unit_id, client, category, unit
        self.job_report_id = job_report_id


class K:
    def __init__(self, target, unit_id=None, client="", category="", name="k", metric="hours"):
        self.target, self.unit_id, self.client, self.category, self.name, self.metric = target, unit_id, client, category, name, metric


class FakeUnits:
    def __init__(self, ids):
        self.ids = ids

    def all(self):
        return [type("U", (), {"pk": i})() for i in self.ids]


class Setting:
    CALLOUT = "callout"

    def __init__(self, client, basis, units=(), callout_hours=12):
        self.client, self.basis, self.callout_hours, self.units = client, basis, callout_hours, FakeUnits(units)

    @property
    def hours_per_day(self):
        return {"monthly_24": 24, "monthly_12": 12}.get(self.basis, self.callout_hours)


class Mfst:
    def __init__(self, client):
        self.client = client


class Job:
    def __init__(self, pk, client, start, end):
        self.pk, self.manifest, self.callout_start, self.callout_end = pk, Mfst(client), start, end


class AvailableHoursTests(SimpleTestCase):
    def test_monthly_24_is_24_hours_a_day_for_each_unit(self):
        s = Setting("Client A", "monthly_24", units=[1, 2])
        self.assertEqual(npt.available_hours(s, D(2026, 9, 1), D(2026, 9, 30), today=TODAY), 24 * 30 * 2)

    def test_monthly_12_is_12_hours_a_day(self):
        s = Setting("Client A", "monthly_12", units=[1])
        self.assertEqual(npt.available_hours(s, D(2026, 2, 1), D(2026, 2, 28), today=TODAY), 12 * 28)

    def test_hours_count_only_days_that_have_happened(self):
        s = Setting("Client A", "monthly_24", units=[1])
        self.assertEqual(npt.available_hours(s, D(2026, 9, 1), D(2026, 9, 30), today=D(2026, 9, 10)), 24 * 10)
        self.assertEqual(npt.available_hours(s, D(2026, 10, 1), D(2026, 10, 31), today=TODAY), 0)

    def test_narrowing_to_some_units(self):
        s = Setting("Client A", "monthly_24", units=[1, 2, 3])
        self.assertEqual(npt.available_hours(s, D(2026, 9, 1), D(2026, 9, 30), unit_ids=[2], today=TODAY), 24 * 30)
        self.assertEqual(npt.available_hours(s, D(2026, 9, 1), D(2026, 9, 30), unit_ids=[99], today=TODAY), 0)

    def test_call_out_uses_12_or_24_hours_times_the_days(self):
        job = Job(1, "Client A", D(2026, 9, 5), D(2026, 9, 9))                      # 5 days
        self.assertEqual(npt.available_hours(Setting("Client A", "callout", callout_hours=12), D(2026, 9, 1), D(2026, 9, 30),
                                             jobs=[job], today=TODAY), 60)
        self.assertEqual(npt.available_hours(Setting("Client A", "callout", callout_hours=24), D(2026, 9, 1), D(2026, 9, 30),
                                             jobs=[job], today=TODAY), 120)

    def test_call_out_counts_each_job_and_only_the_days_inside_the_period(self):
        jobs = [Job(1, "Client A", D(2026, 8, 29), D(2026, 9, 2)),                   # 2 days fall in September
                Job(2, "Client A", D(2026, 9, 10), D(2026, 9, 10)),                  # 1 day
                Job(3, "Client A", D(2026, 7, 1), D(2026, 7, 5)),                    # outside
                Job(4, "OTHER", D(2026, 9, 1), D(2026, 9, 30))]                 # another client's job
        self.assertEqual(npt.available_hours(Setting("Client A", "callout", callout_hours=12), D(2026, 9, 1), D(2026, 9, 30),
                                             jobs=jobs, today=TODAY), (2 + 1) * 12)

    def test_call_out_for_a_unit_counts_only_jobs_that_unit_worked(self):
        jobs = [Job(1, "Client A", D(2026, 9, 1), D(2026, 9, 2)), Job(2, "Client A", D(2026, 9, 10), D(2026, 9, 12))]
        entries = [E(D(2026, 9, 11), 1, unit_id=7, job_report_id=2)]
        self.assertEqual(npt.available_hours(Setting("Client A", "callout"), D(2026, 9, 1), D(2026, 9, 30), jobs=jobs,
                                             entries=entries, unit_ids=[7], today=TODAY), 3 * 12)

    def test_available_for_one_client_or_all_and_whether_anything_is_set_up(self):
        settings = [Setting("Client A", "monthly_24", units=[1]), Setting("B", "monthly_12", units=[2, 3])]
        window = (D(2026, 9, 1), D(2026, 9, 30))
        self.assertEqual(npt.available_for(settings, *window, client="Client A", today=TODAY), (24 * 30, True))
        self.assertEqual(npt.available_for(settings, *window, today=TODAY), (24 * 30 + 12 * 30 * 2, True))
        self.assertEqual(npt.available_for(settings, *window, client="NOBODY", today=TODAY), (0.0, False))

    def test_percent(self):
        self.assertEqual(npt.percent_of(7.2, 720), 1.0)
        self.assertIsNone(npt.percent_of(5, 0))


class PercentChartTests(SimpleTestCase):
    def test_bars_are_percent_of_each_months_available_hours(self):
        s = Setting("Client A", "monthly_24", units=[1])
        entries = [E(D(2026, 8, 5), 31, client="Client A"), E(D(2026, 9, 5), 14.4, client="Client A")]
        series = npt.time_series(entries, D(2026, 8, 1), D(2026, 9, 30), kpi_line=3, metric="percent",
                                 settings=[s], client="Client A", today=TODAY)
        self.assertEqual([b["value"] for b in series["bars"]], [round(31 / 744 * 100, 2), 2.0])   # August has 31 days
        self.assertEqual([b["over"] for b in series["bars"]], [True, False])                         # 4.17% is above 3%
        self.assertEqual(series["bars"][1]["available"], 720.0)

    def test_a_percent_kpi_line_is_drawn_even_on_a_daily_chart(self):
        s = Setting("Client A", "monthly_24", units=[1])
        series = npt.time_series([E(D(2026, 9, 2), 2, client="Client A")], D(2026, 9, 1), D(2026, 9, 30), kpi_line=5,
                                 metric="percent", settings=[s], client="Client A", today=TODAY)
        self.assertEqual(series["grouping"], "day")
        self.assertIsNotNone(series["kpi_bottom"])
        hours_daily = npt.time_series([E(D(2026, 9, 2), 2)], D(2026, 9, 1), D(2026, 9, 30), kpi_line=5)
        self.assertEqual(hours_daily["grouping"], "month")                 # an hours target is monthly, so bars are months
        self.assertIsNotNone(hours_daily["kpi_bottom"])

    def test_short_ranges_are_charted_per_day_long_ones_per_month(self):
        entries = [E(D(2026, 9, 2), 3), E(D(2026, 9, 2), 1.5), E(D(2026, 9, 20), 2)]
        daily = npt.time_series(entries, D(2026, 9, 1), D(2026, 9, 30))
        self.assertEqual((daily["grouping"], len(daily["bars"]), daily["bars"][1]["value"]), ("day", 30, 4.5))
        monthly = npt.time_series(entries, D(2026, 7, 1), D(2026, 9, 30), kpi_line=10)
        self.assertEqual(monthly["grouping"], "month")
        self.assertEqual([b["value"] for b in monthly["bars"]], [0, 0, 6.5])

    def test_axis_is_round_and_leaves_room_for_the_kpi_line(self):
        self.assertEqual(npt.nice_axis(17.5, 12), (20, [0, 5, 10, 15, 20]))
        top, ticks = npt.nice_axis(2.4, 3)
        self.assertGreaterEqual(top, 3 * 1.15)                               # the KPI line always fits inside the chart
        self.assertEqual(ticks[0], 0)
        self.assertEqual(ticks[-1], top)
        self.assertEqual(npt.nice_axis(0)[1][0], 0)                          # empty data doesn't crash

    def test_axis_ticks_position_the_kpi_line_on_the_same_scale_as_the_bars(self):
        series = npt.time_series([E(D(2026, 8, 3), 12), E(D(2026, 9, 3), 4)], D(2026, 8, 1), D(2026, 9, 30), kpi_line=8)
        top = series["top"]
        self.assertEqual(series["kpi_bottom"], round(100 * 8 / top, 2))
        self.assertEqual(series["bars"][0]["height"], round(100 * 12 / top, 1))
        self.assertEqual([b["over"] for b in series["bars"]], [True, False])

    def test_breakdown_shares_order_and_colours(self):
        entries = [E(D(2026, 9, 1), 6, client="Client A"), E(D(2026, 9, 2), 2, client="B"), E(D(2026, 9, 3), 2, client="Client A")]
        colours = npt.colour_map(["Client A", "B"])
        rows = npt.breakdown(entries, key=lambda e: e.client, label=lambda e: e.client, colour=colours.get)
        self.assertEqual([(r["label"], r["hours"], r["count"], r["share"]) for r in rows], [("Client A", 8.0, 2, 80), ("B", 2.0, 1, 20)])
        self.assertNotEqual(rows[0]["colour"], rows[1]["colour"])

    def test_each_client_keeps_its_colour(self):
        first = npt.colour_map(["Client A", "B", "TEEPRH"])
        again = npt.colour_map(["TEEPRH", "Client A", "B", ""])
        self.assertEqual(first, again)
        self.assertEqual(len(set(first.values())), 3)


class KPITests(SimpleTestCase):
    def test_kpi_scope_matching(self):
        entry = E(D(2026, 9, 1), 1, unit_id=7, client="Client A", category="personnel")
        self.assertTrue(npt.kpi_matches(K(5), entry))
        self.assertTrue(npt.kpi_matches(K(5, unit_id=7), entry))
        self.assertFalse(npt.kpi_matches(K(5, unit_id=8), entry))
        self.assertFalse(npt.kpi_matches(K(5, client="Client A", category="equipment"), entry))

    def test_hours_kpi_thresholds(self):
        s, e = D(2026, 9, 1), D(2026, 9, 30)
        def status(hours):
            return npt.evaluate_kpis([K(10)], [E(D(2026, 9, 5), hours)], s, e, today=TODAY)[0]["status"]
        self.assertEqual([status(5), status(8), status(10), status(10.5)], ["ok", "near", "near", "over"])

    def test_hours_kpi_judged_on_the_latest_month_counting_months_over(self):
        entries = [E(D(2026, 7, 5), 12), E(D(2026, 8, 5), 3), E(D(2026, 9, 5), 11)]
        row = npt.evaluate_kpis([K(10)], entries, D(2026, 7, 1), D(2026, 9, 30), today=TODAY)[0]
        self.assertEqual((row["actual"], row["status"], row["months_over"], row["months_total"], row["period_hours"]),
                         (11, "over", 2, 3, 26))

    def test_a_year_range_is_judged_on_the_current_month(self):
        row = npt.evaluate_kpis([K(10)], [E(D(2026, 9, 5), 4)], D(2026, 1, 1), D(2026, 12, 31), today=TODAY)[0]
        self.assertEqual((row["focus"], row["actual"], row["months_total"]), (D(2026, 9, 1), 4, 9))

    def test_percent_kpi_uses_the_clients_available_hours(self):
        s = Setting("Client A", "monthly_24", units=[1])                      # 720 h in September
        entries = [E(D(2026, 9, 5), 14.4, client="Client A"), E(D(2026, 9, 6), 100, client="OTHER")]
        row = npt.evaluate_kpis([K(3, client="Client A", metric="percent")], entries, D(2026, 9, 1), D(2026, 9, 30),
                                today=TODAY, settings=[s])[0]
        self.assertEqual((row["actual"], row["status"], row["available"], row["npt_hours"]), (2.0, "ok", 720.0, 14.4))
        over = npt.evaluate_kpis([K(1, client="Client A", metric="percent")], entries, D(2026, 9, 1), D(2026, 9, 30),
                                 today=TODAY, settings=[s])[0]
        self.assertEqual(over["status"], "over")

    def test_percent_kpi_for_a_client_with_no_setup_is_unknown_not_zero(self):
        row = npt.evaluate_kpis([K(3, client="Client A", metric="percent")], [E(D(2026, 9, 5), 5, client="Client A")],
                                D(2026, 9, 1), D(2026, 9, 30), today=TODAY, settings=[])[0]
        self.assertEqual(row["status"], "unknown")

    def test_percent_kpi_for_one_unit_uses_only_that_units_hours(self):
        s = Setting("Client A", "monthly_24", units=[1, 2])
        entries = [E(D(2026, 9, 5), 7.2, unit_id=1, client="Client A")]
        row = npt.evaluate_kpis([K(2, unit_id=1, client="Client A", metric="percent")], entries, D(2026, 9, 1), D(2026, 9, 30),
                                today=TODAY, settings=[s])[0]
        self.assertEqual((row["available"], row["actual"]), (720.0, 1.0))      # not 1440: just unit 1

    def test_percent_kpi_with_call_out_hours(self):
        s = Setting("Client A", "callout", callout_hours=24)
        jobs = [Job(1, "Client A", D(2026, 9, 1), D(2026, 9, 5))]                   # 5 days x 24 h = 120 h
        row = npt.evaluate_kpis([K(10, client="Client A", metric="percent")], [E(D(2026, 9, 2), 6, client="Client A", job_report_id=1)],
                                D(2026, 9, 1), D(2026, 9, 30), today=TODAY, settings=[s], jobs=jobs)[0]
        self.assertEqual((row["available"], row["actual"], row["status"]), (120.0, 5.0, "ok"))

    def test_chart_picks_the_closest_matching_kpi(self):
        everything, roo, roo_unit = K(9, name="all"), K(5, client="Client A", name="roo"), K(3, client="Client A", unit_id=7, name="roo-unit")
        pct = K(2, client="Client A", metric="percent", name="roo-pct")
        kpis = [everything, roo, roo_unit, pct]
        self.assertEqual(npt.pick_chart_kpi(kpis, "hours", "Client A", "7").name, "roo-unit")
        self.assertEqual(npt.pick_chart_kpi(kpis, "hours", "Client A", "").name, "roo")
        self.assertEqual(npt.pick_chart_kpi(kpis, "hours", "Client A", "8").name, "roo")          # no KPI for unit 8: fall back to the client's
        self.assertEqual(npt.pick_chart_kpi(kpis, "hours", "", "").name, "all")
        self.assertEqual(npt.pick_chart_kpi(kpis, "percent", "Client A", "").name, "roo-pct")
        self.assertIsNone(npt.pick_chart_kpi(kpis, "percent", "B", ""))


# ═══ with the database ═════════════════════════════════════════════════════
def make_world():
    dept = Department.objects.create(name="Wireline", location="Site A")
    units = Classification.objects.create(department=dept, name="Units", is_unit=True)
    other = Classification.objects.create(department=dept, name="PCE")
    world = {
        "dept": dept,
        "truck": Asset.objects.create(classification=units, name="A 84828", description="MAN Truck"),
        "skid": Asset.objects.create(classification=units, name="1206-238", description="Lee skid unit"),
        "pce": Asset.objects.create(classification=other, name="PCE-1", description="not a unit"),
    }
    Client.objects.create(name="Client A")
    Client.objects.create(name="Client B")
    roo_m = JobManifest.objects.create(job_name="Callout Client A", client="Client A", location="Main Field")
    gaz_m = JobManifest.objects.create(job_name="Callout B", client="Client B", location="Site B")
    none_m = JobManifest.objects.create(job_name="No client manifest", client="", location="x")
    world["roo_job"] = JobReport.objects.create(manifest=roo_m, name="Well Ru-1")
    world["gaz_job"] = JobReport.objects.create(manifest=gaz_m, name="Well G-2")
    world["orphan_job"] = JobReport.objects.create(manifest=none_m, name="Unassigned job")
    return world


class EntryFormTests(TestCase):
    def setUp(self):
        self.w = make_world()

    def data(self, **over):
        base = {"date": "2026-09-01", "job_report": self.w["roo_job"].pk, "unit": self.w["truck"].pk,
                "category": "personnel", "npt_hours": "1.5"}
        return base | over

    def test_a_job_is_required_and_lists_the_jobs_with_their_client(self):
        self.assertIn("job_report", SQBTrackerEntryForm({}).errors)
        labels = [label for _, label in SQBTrackerEntryForm().fields["job_report"].choices]
        self.assertTrue(any(l.startswith("Well Ru-1 — Client A") for l in labels))
        self.assertTrue(any("no client" in l for l in labels))

    def test_the_client_comes_from_the_jobs_manifest(self):
        form = SQBTrackerEntryForm(self.data(client="Client B"))          # a wrong client can't override the job's
        self.assertTrue(form.is_valid(), form.errors)
        entry = form.save()
        self.assertEqual((entry.client, entry.job_ticket, entry.job_report), ("Client A", "Well Ru-1", self.w["roo_job"]))

    def test_a_job_whose_manifest_has_no_client_needs_one_picked(self):
        self.assertIn("client", SQBTrackerEntryForm(self.data(job_report=self.w["orphan_job"].pk)).errors)
        ok = SQBTrackerEntryForm(self.data(job_report=self.w["orphan_job"].pk, client="Client A"))
        self.assertTrue(ok.is_valid(), ok.errors)
        self.assertEqual(ok.save().client, "Client A")

    def test_unit_cause_date_and_hours_are_still_required_and_hours_cannot_be_negative(self):
        self.assertEqual(set(SQBTrackerEntryForm({}).errors), {"date", "job_report", "unit", "category", "npt_hours"})
        self.assertFalse(SQBTrackerEntryForm(self.data(npt_hours="-2")).is_valid())

    def test_unit_dropdown_lists_only_units(self):
        self.assertEqual([u.name for u in SQBTrackerEntryForm().fields["unit"].queryset], ["1206-238", "A 84828"])

    def test_npt_cannot_be_logged_for_a_future_date(self):
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        form = SQBTrackerEntryForm(self.data(date=tomorrow))
        self.assertIn("date", form.errors)
        self.assertTrue(SQBTrackerEntryForm(self.data(date=datetime.date.today().isoformat())).is_valid())


class JobReportCalloutTests(TestCase):
    def setUp(self):
        self.manifest = JobManifest.objects.create(job_name="M", client="Client A")

    def form(self, **extra):
        return JobReportForm({"manifest": self.manifest.pk, "name": "Well", **extra})

    def test_callout_days_count_both_ends(self):
        job = JobReport(manifest=self.manifest, name="x", callout_start=D(2026, 9, 5), callout_end=D(2026, 9, 9))
        self.assertEqual(job.callout_days, 5)
        self.assertEqual(JobReport(manifest=self.manifest, name="y", callout_start=D(2026, 9, 5), callout_end=D(2026, 9, 5)).callout_days, 1)
        self.assertIsNone(JobReport(manifest=self.manifest, name="z").callout_days)

    def test_both_dates_or_neither(self):
        self.assertTrue(self.form().is_valid())
        self.assertTrue(self.form(callout_start="2026-09-01", callout_end="2026-09-03").is_valid())
        self.assertFalse(self.form(callout_start="2026-09-01").is_valid())
        self.assertFalse(self.form(callout_end="2026-09-01").is_valid())

    def test_cannot_end_before_it_starts(self):
        self.assertIn("callout_end", self.form(callout_start="2026-09-05", callout_end="2026-09-01").errors)


class KPIFormTests(TestCase):
    def setUp(self):
        self.w = make_world()

    def test_hours_and_percent_targets(self):
        self.assertFalse(NPTKPIForm({"name": "x", "metric": "hours", "target": "0"}).is_valid())
        hours = NPTKPIForm({"name": "All NPT", "metric": "hours", "target": "20", "is_active": "on"})
        self.assertTrue(hours.is_valid(), hours.errors)
        pct = NPTKPIForm({"name": "Client A", "metric": "percent", "target": "2.5", "client": "Client A"})
        self.assertTrue(pct.is_valid(), pct.errors)
        saved = pct.save()
        self.assertEqual((saved.target_text, saved.scope_text), ("2.5%", "client Client A"))
        self.assertEqual(hours.save().target_text, "20 h / month")

    def test_a_percentage_cannot_exceed_100(self):
        self.assertIn("target", NPTKPIForm({"name": "x", "metric": "percent", "target": "101"}).errors)

    def test_scope_text(self):
        scoped = NPTKPIForm({"name": "T", "metric": "hours", "target": "4", "unit": self.w["truck"].pk,
                             "category": "personnel", "client": "Client A"})
        self.assertTrue(scoped.is_valid(), scoped.errors)
        self.assertEqual(scoped.save().scope_text, "unit A 84828, client Client A, personnel")


class ClientSetupTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.admin = User.objects.create_user(username="cs_admin", password="x")
        Profile.objects.update_or_create(user=self.admin, defaults={"role": Role.ADMIN})
        self.client.force_login(self.admin)
        self.roo = Client.objects.get(name="Client A")

    def test_basis_hours_per_day(self):
        self.assertEqual(ClientNPTSetting(client="a", basis="monthly_24").hours_per_day, 24)
        self.assertEqual(ClientNPTSetting(client="a", basis="monthly_12").hours_per_day, 12)
        self.assertEqual(ClientNPTSetting(client="a", basis="callout", callout_hours=24).hours_per_day, 24)
        self.assertEqual(ClientNPTSetting(client="a", basis="callout", callout_hours=12).basis_text, "Call-out · 12 h × days")

    def test_setting_up_a_client_with_its_units(self):
        response = self.client.post(reverse("sqb:client_npt_edit", args=[self.roo.pk]), {
            "basis": "monthly_12", "callout_hours": 12, "units": [self.w["truck"].pk, self.w["skid"].pk]})
        self.assertEqual(response.status_code, 302)
        setting = ClientNPTSetting.objects.get(client="Client A")
        self.assertEqual((setting.basis, setting.units.count()), ("monthly_12", 2))

    def test_manage_page_lists_every_client_with_its_state(self):
        ClientNPTSetting.objects.create(client="Client A", basis="monthly_24")
        html = self.client.get(reverse("sqb:client_npt_manage")).content.decode()
        self.assertIn("Monthly — 24 hours a day", html)
        self.assertIn("not set up", html)                                   # Client B

    def test_only_editors_can_change_a_clients_basis(self):
        viewer = User.objects.create_user(username="cs_viewer", password="x")
        Profile.objects.update_or_create(user=viewer, defaults={"role": Role.VIEWER})
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse("sqb:client_npt_edit", args=[self.roo.pk]), {"basis": "monthly_24"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("sqb:client_npt_manage")).status_code, 403)

    def test_setup_page_has_the_sections_the_basis_switch_shows_and_hides(self):
        html = self.client.get(reverse("sqb:client_npt_edit", args=[self.roo.pk])).content.decode()
        for marker in ('id="callout-row"', 'id="units-row"', 'name="basis"', "Monthly \u2014 12 hours a day", "Call-out"):
            self.assertIn(marker, html)
        self.assertNotIn("<p><label", html.split('id="client-npt-form"')[1])       # no fields trapped in paragraphs

    def test_form_offers_only_units(self):
        names = {u.name for u in ClientNPTSettingForm().fields["units"].queryset}
        self.assertEqual(names, {"A 84828", "1206-238"})


class NPTPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = make_world()
        cls.today = datetime.date.today()
        cls.month = cls.today.replace(day=1)
        w = cls.w
        mk = SQBTrackerEntry.objects.create
        cls.e1 = mk(date=cls.month, unit=w["truck"], job_report=w["roo_job"], client="Client A", category="equipment", npt_hours=6, job_ticket="Well Ru-1", description="pump")
        mk(date=cls.month, unit=w["truck"], job_report=w["roo_job"], client="Client A", category="personnel", npt_hours=3)
        mk(date=cls.month, unit=w["skid"], job_report=w["gaz_job"], client="Client B", category="documents", npt_hours=2)
        mk(date=cls.month - datetime.timedelta(days=400), unit=w["skid"], client="Client A", category="equipment", npt_hours=50)

    def setUp(self):
        user = User.objects.create_user(username="npt_admin", password="x")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.ADMIN})
        self.client.force_login(user)

    def page(self, **params):
        response = self.client.get(reverse("sqb:tracker_list"), params)
        self.assertEqual(response.status_code, 200)
        return response

    def html(self, **params):
        return self.page(**params).content.decode()

    # ── period & filters ────────────────────────────────────────────────
    def test_period_limits_what_is_counted(self):
        self.assertIn('<div class="stat-value">11<small', self.html(period="this_month", show="hours"))
        year_ago = (self.month - datetime.timedelta(days=400)).strftime("%Y-%m")
        self.assertIn('<div class="stat-value">50<small', self.html(period="month", month=year_ago, show="hours"))

    def test_filters_by_unit_client_and_cause(self):
        page = lambda **p: self.html(period="this_month", show="hours", **p)
        self.assertIn('<div class="stat-value">9<small', page(unit=self.w["truck"].pk))
        self.assertIn('<div class="stat-value">2<small', page(client="Client B"))
        self.assertIn('<div class="stat-value">3<small', page(category="personnel"))

    def test_bad_query_values_do_not_crash(self):
        self.page(period="custom", **{"from": "junk"})
        self.page(unit="not-a-number", category="???", client="nobody", show="banana")

    # ── basis & percentages ─────────────────────────────────────────────
    @staticmethod
    def page_percent(npt, available):
        """The page rounds to 2 places (half up) and then shows 1 place."""
        two = (Decimal(100 * npt) / Decimal(available)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{two.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP):f}"

    def setup_roo(self, basis="monthly_24", units=None, **extra):
        s = ClientNPTSetting.objects.create(client="Client A", basis=basis, **extra)
        s.units.set(units if units is not None else [self.w["truck"]])
        return s

    def test_percent_is_npt_over_the_hours_available_on_the_clients_basis(self):
        self.setup_roo("monthly_24")                                          # 1 unit x 24 h x days so far this month
        days = self.today.day
        html = self.html(period="this_month", client="Client A", show="percent")
        expected = self.page_percent(9, 24 * days)
        self.assertIn(f'<div class="stat-value">{expected}<small', html)
        self.assertIn("worked out on", html)
        self.assertIn("Monthly — 24 hours a day", html)

    def test_a_12_hour_basis_gives_a_bigger_percentage_for_the_same_npt(self):
        self.setup_roo("monthly_12")
        days = self.today.day
        html = self.html(period="this_month", client="Client A", show="percent")
        expected = self.page_percent(9, 12 * days)
        self.assertIn(f'<div class="stat-value">{expected}<small', html)

    def test_call_out_basis_uses_the_job_dates(self):
        job = self.w["roo_job"]
        JobReport.objects.filter(pk=job.pk).update(callout_start=self.month, callout_end=self.month + datetime.timedelta(days=min(4, self.today.day - 1)))
        self.setup_roo("callout", callout_hours=24)
        days = min(5, self.today.day)
        html = self.html(period="this_month", client="Client A", show="percent")
        self.assertIn(f"{days * 24} h", html)                                # available hours shown

    def test_each_client_is_worked_out_on_its_own_basis(self):
        self.setup_roo("monthly_24")
        gaz = ClientNPTSetting.objects.create(client="Client B", basis="monthly_12")
        gaz.units.set([self.w["skid"]])
        table = self.page(period="this_month", show="percent").context["clients"]
        by_name = {c["name"]: c for c in table}
        days = self.today.day
        self.assertEqual(by_name["Client A"]["available"], 24 * days * 1)
        self.assertEqual(by_name["Client B"]["available"], 12 * days * 1)
        self.assertEqual(by_name["Client A"]["percent"], round(100 * 9 / (24 * days), 2))

    def test_a_client_without_setup_shows_hours_but_no_percent(self):
        by_name = {c["name"]: c for c in self.page(period="this_month").context["clients"]}
        self.assertIsNone(by_name["Client A"]["percent"])
        self.assertEqual(by_name["Client A"]["hours"], 9)
        self.assertIn("not set up", self.html(period="this_month"))

    def test_client_total_available_ignores_the_client_filter_so_clients_can_be_compared(self):
        self.setup_roo()
        names = [c["name"] for c in self.page(period="this_month", client="Client B").context["clients"]]
        self.assertIn("Client A", names)

    # ── KPIs & chart ────────────────────────────────────────────────────
    def test_percent_kpi_is_drawn_as_a_line_and_bars_above_it_are_flagged(self):
        self.setup_roo("monthly_24")
        NPTKPI.objects.create(name="Client A KPI", metric="percent", target="1", client="Client A")   # 9 h is far more than 1%
        series = self.page(period="last_3_months", client="Client A", show="percent").context["series"]
        self.assertEqual(series["kpi_line"], 1.0)
        self.assertTrue(series["bars"][-1]["over"])
        html = self.html(period="last_3_months", client="Client A", show="percent")
        self.assertIn("chart-bar is-over", html)
        self.assertIn("KPI 1%", html)
        self.assertIn("Above KPI", html)

    def test_a_generous_percent_kpi_leaves_the_bar_below_the_line(self):
        self.setup_roo("monthly_24")
        NPTKPI.objects.create(name="Client A KPI", metric="percent", target="90", client="Client A")
        html = self.html(period="last_3_months", client="Client A", show="percent")
        self.assertIn("chart-bar is-under", html)
        self.assertIn("Below KPI", html)

    def test_y_axis_ticks_are_drawn(self):
        html = self.html(period="last_3_months", show="hours")
        self.assertIn('class="chart-yaxis"', html)
        self.assertIn('class="y-tick"', html)
        self.assertIn('class="chart-grid"', html)

    def test_no_matching_kpi_means_no_line_and_says_why(self):
        html = self.html(period="last_3_months", show="hours")
        self.assertNotIn('class="chart-kpi"', html)
        self.assertIn("no target line is drawn", html)

    def test_kpi_for_a_client_with_no_setup_says_so_instead_of_showing_zero(self):
        NPTKPI.objects.create(name="Needs setup", metric="percent", target="3", client="Client A")
        html = self.html(period="this_month")
        self.assertIn("No setup", html)
        self.assertIn("no NPT setup", html)

    def test_hours_kpis_still_work(self):
        NPTKPI.objects.create(name="All NPT", target="8")
        NPTKPI.objects.create(name="Documents", target="5", category="documents")
        html = self.html(period="this_month", show="hours")
        self.assertIn("Above KPI", html)
        self.assertIn("Below KPI", html)
        self.assertIn("1/2", html)

    def test_inactive_kpis_are_ignored(self):
        NPTKPI.objects.create(name="Old", target="1", is_active=False)
        self.assertIn("No KPIs set yet", self.html(period="this_month"))

    # ── colours ─────────────────────────────────────────────────────────
    def test_clients_have_colours_in_the_client_table_and_entries(self):
        html = self.html(period="this_month")
        self.assertIn('class="dot"', html)
        colours = self.page(period="this_month").context["colours"]
        self.assertEqual(set(colours), {"Client A", "Client B"})
        for colour in colours.values():
            self.assertIn(f"background:{colour}", html)

    # ── units ───────────────────────────────────────────────────────────
    def test_by_unit_shows_percent_for_units_on_the_clients_contract(self):
        self.setup_roo("monthly_24", units=[self.w["truck"], self.w["skid"]])
        units = {u["label"]: u for u in self.page(period="this_month", client="Client A").context["units"]}
        days = self.today.day
        self.assertEqual(units["A 84828"]["percent"], round(100 * 9 / (24 * days), 2))
        self.assertEqual(units["1206-238"]["percent"], 0.0)

    def test_by_unit_lists_every_unit_even_with_no_npt(self):
        Asset.objects.create(classification=Classification.objects.get(name="Units"), name="IDLE-1", description="never had NPT")
        html = self.html(period="this_month")
        self.assertIn("IDLE-1", html)
        self.assertNotIn("PCE-1", html)

    # ── job ─────────────────────────────────────────────────────────────
    def test_entries_show_their_job(self):
        html = self.html(period="this_month", show="hours")
        self.assertIn("Well Ru-1", html)
        self.assertIn(reverse("job_reports:detail", args=[self.w["roo_job"].pk]), html)

    def test_legacy_entries_without_a_job_still_display(self):
        SQBTrackerEntry.objects.create(date=self.month, client="Client A", npt_hours=1, job_ticket="old-ticket")
        html = self.html(period="this_month", show="hours")
        self.assertIn("old-ticket", html)
        self.assertIn("not linked", html)

    def test_logging_an_entry_against_a_job_takes_the_clients_from_it(self):
        response = self.client.post(reverse("sqb:tracker_list"), {
            "date": self.month.isoformat(), "job_report": self.w["gaz_job"].pk, "unit": self.w["skid"].pk,
            "category": "personnel", "npt_hours": "2.5", "description": "late crew"})
        self.assertEqual(response.status_code, 302)
        entry = SQBTrackerEntry.objects.get(description="late crew")
        self.assertEqual((entry.client, entry.job_report, entry.unit, entry.npt_hours), ("Client B", self.w["gaz_job"], self.w["skid"], 2.5))

    def test_editing_an_entry(self):
        self.client.post(reverse("sqb:tracker_edit", args=[self.e1.pk]), {
            "date": self.month.isoformat(), "job_report": self.w["gaz_job"].pk, "unit": self.w["truck"].pk,
            "category": "equipment", "npt_hours": "4"})
        self.e1.refresh_from_db()
        self.assertEqual((self.e1.client, self.e1.job_report, self.e1.npt_hours), ("Client B", self.w["gaz_job"], 4))

    # ── permissions ─────────────────────────────────────────────────────
    def test_kpi_management_and_permissions(self):
        self.client.post(reverse("sqb:kpi_manage"), {"name": "All", "metric": "hours", "target": "10", "is_active": "on"})
        kpi = NPTKPI.objects.get()
        self.client.post(reverse("sqb:kpi_edit", args=[kpi.pk]), {"name": "All", "metric": "percent", "target": "4", "is_active": "on"})
        kpi.refresh_from_db()
        self.assertEqual((kpi.metric, float(kpi.target)), ("percent", 4))
        editor = User.objects.create_user(username="npt_editor", password="x")
        Profile.objects.update_or_create(user=editor, defaults={"role": Role.EDITOR})
        self.client.force_login(editor)
        self.assertEqual(self.client.post(reverse("sqb:kpi_delete", args=[kpi.pk])).status_code, 403)
        viewer = User.objects.create_user(username="npt_viewer", password="x")
        Profile.objects.update_or_create(user=viewer, defaults={"role": Role.VIEWER})
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse("sqb:tracker_list")).status_code, 200)
        self.assertEqual(self.client.get(reverse("sqb:npt_compare")).status_code, 200)
        self.assertEqual(self.client.post(reverse("sqb:kpi_manage"), {"name": "C", "target": "3"}).status_code, 403)

    def test_query_count_stays_flat_as_entries_grow(self):
        self.setup_roo()
        NPTKPI.objects.create(name="k", metric="percent", target="3", client="Client A")
        def count():
            with CaptureQueriesContext(connection) as ctx:
                self.page(period="last_3_months", client="Client A", show="percent")
            return len(ctx)
        before = count()
        for _ in range(60):
            SQBTrackerEntry.objects.create(date=self.month, unit=self.w["skid"], job_report=self.w["roo_job"], client="Client A", category="equipment", npt_hours=1)
        self.assertLessEqual(count(), before + 1)


class CompareUnitsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = make_world()
        cls.third = Asset.objects.create(classification=Classification.objects.get(name="Units"), name="THIRD-3", description="third unit")
        cls.today = datetime.date.today()
        cls.month = cls.today.replace(day=1)
        cls.setting = ClientNPTSetting.objects.create(client="Client A", basis="monthly_24")
        cls.setting.units.set([cls.w["truck"], cls.w["skid"], cls.third])
        mk = SQBTrackerEntry.objects.create
        mk(date=cls.month, unit=cls.w["truck"], job_report=cls.w["roo_job"], client="Client A", category="equipment", npt_hours=6)
        mk(date=cls.month, unit=cls.w["skid"], job_report=cls.w["roo_job"], client="Client A", category="personnel", npt_hours=2)
        mk(date=cls.month, unit=cls.w["truck"], job_report=cls.w["gaz_job"], client="Client B", category="documents", npt_hours=40)  # another client

    def setUp(self):
        user = User.objects.create_user(username="cmp", password="x")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.VIEWER})
        self.client.force_login(user)

    def get(self, **params):
        response = self.client.get(reverse("sqb:npt_compare"), params)
        self.assertEqual(response.status_code, 200)
        return response

    def test_asks_for_a_client_first(self):
        self.assertIn("Choose a client", self.get().content.decode())

    def test_offers_only_the_units_on_the_clients_contract(self):
        choices = {u.name for u in self.get(client="Client A").context["unit_choices"]}
        self.assertEqual(choices, {"A 84828", "1206-238", "THIRD-3"})
        ClientNPTSetting.objects.create(client="Client B", basis="monthly_24")          # no units assigned: any unit can be compared
        self.assertEqual(len(self.get(client="Client B").context["unit_choices"]), 3)

    def test_compares_the_chosen_units_using_only_that_clients_npt(self):
        page = self.get(client="Client A", period="this_month", units=[self.w["truck"].pk, self.w["skid"].pk], show="hours")
        rows = {u["unit"].name: u for u in page.context["comparison"]["units"]}
        self.assertEqual(rows["A 84828"]["hours"], 6)                   # the 40 h with Client B is not counted
        self.assertEqual(rows["1206-238"]["hours"], 2)
        self.assertEqual(rows["A 84828"]["top_cause"], "Base equipment")
        self.assertEqual(len(page.context["chart"]["groups"][0]["bars"]), 2)    # one bar per unit in each time slot

    def test_percent_comparison_uses_each_units_available_hours(self):
        page = self.get(client="Client A", period="this_month", units=[self.w["truck"].pk, self.w["skid"].pk], show="percent")
        rows = {u["unit"].name: u for u in page.context["comparison"]["units"]}
        days = self.today.day
        self.assertEqual(rows["A 84828"]["available"], 24 * days)
        self.assertEqual(rows["A 84828"]["percent"], round(100 * 6 / (24 * days), 2))
        self.assertGreater(rows["A 84828"]["percent"], rows["1206-238"]["percent"])

    def test_kpi_line_and_above_below_markers(self):
        # the percentages depend on how far into the month it is, so put the target between the truck (6 h) and the skid (2 h)
        target = round(4 / (24 * self.today.day) * 100, 2)
        NPTKPI.objects.create(name="Client A", metric="percent", target=str(target), client="Client A")
        page = self.get(client="Client A", period="this_month", units=[self.w["truck"].pk, self.w["skid"].pk, self.third.pk], show="percent")
        html = page.content.decode()
        self.assertEqual(page.context["chart"]["kpi_line"], target)
        self.assertIn("Above KPI", html)
        self.assertIn("Below KPI", html)

    def test_units_get_distinct_colours(self):
        page = self.get(client="Client A", period="this_month", units=[self.w["truck"].pk, self.w["skid"].pk, self.third.pk])
        colours = [u["colour"] for u in page.context["comparison"]["units"]]
        self.assertEqual(len(set(colours)), 3)

    def test_one_unit_asks_for_another(self):
        page = self.get(client="Client A", period="this_month", units=[self.w["truck"].pk])
        self.assertTrue(page.context["few_units"])
        self.assertIn("two or more units", page.content.decode())

    def test_without_a_client_setup_hours_can_still_be_compared(self):
        page = self.get(client="Client B", period="this_month", units=[self.w["truck"].pk, self.w["skid"].pk], show="hours")
        rows = {u["unit"].name: u for u in page.context["comparison"]["units"]}
        self.assertEqual(rows["A 84828"]["hours"], 40)
        self.assertIsNone(rows["A 84828"]["percent"])

    def test_bad_input_does_not_crash(self):
        self.get(client="Nobody", units=["x", "9999"], period="custom")


class UnitsFromDepartmentsTests(TestCase):
    """The units NPT is measured against are the ones Departments marks as units."""

    @classmethod
    def setUpTestData(cls):
        cls.wireline = Department.objects.create(name="Wireline", location="Site A")
        cls.testing = Department.objects.create(name="Well Testing", location="Site A")
        cls.units = Classification.objects.create(department=cls.wireline, name="Units", is_unit=True)
        cls.test_units = Classification.objects.create(department=cls.testing, name="Test spreads", is_unit=True)  # no "unit" in the name
        cls.tools = Classification.objects.create(department=cls.wireline, name="Tools")
        cls.truck = Asset.objects.create(classification=cls.units, name="A 84828", description="MAN Truck")
        cls.idle = Asset.objects.create(classification=cls.units, name="IDLE-1", description="never had NPT")
        cls.spread = Asset.objects.create(classification=cls.test_units, name="WT-1", description="Test spread")
        cls.tool = Asset.objects.create(classification=cls.tools, name="TOOL-1")

    def setUp(self):
        user = User.objects.create_user(username="dep_admin", password="x")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.ADMIN})
        self.client.force_login(user)

    def log(self, unit, hours=2, **kw):
        return SQBTrackerEntry.objects.create(date=datetime.date.today(), unit=unit, npt_hours=hours,
                                              category=kw.pop("category", "equipment"), **kw)

    def test_units_come_from_every_department_not_from_a_name(self):
        names = {u.name for u in SQBTrackerEntryForm().fields["unit"].queryset}
        self.assertEqual(names, {"A 84828", "IDLE-1", "WT-1"})          # both departments, and no TOOL-1

    def test_the_department_is_shown_when_units_span_departments(self):
        labels = [label for _, label in SQBTrackerEntryForm().fields["unit"].choices if label.startswith("WT-1")]
        self.assertEqual(labels, ["WT-1 \u2014 Test spread (Well Testing)"])

    def test_department_is_left_out_when_all_units_are_in_one(self):
        Classification.objects.filter(pk=self.test_units.pk).update(is_unit=False)
        labels = [label for _, label in SQBTrackerEntryForm().fields["unit"].choices]
        self.assertIn("A 84828 \u2014 MAN Truck", labels)

    def test_with_nothing_marked_every_asset_is_offered_instead_of_an_empty_list(self):
        Classification.objects.update(is_unit=False)
        self.assertEqual(SQBTrackerEntryForm().fields["unit"].queryset.count(), 4)

    def test_by_unit_lists_every_unit_even_with_no_npt(self):
        self.log(self.truck, 5)
        html = self.client.get(reverse("sqb:tracker_list"), {"period": "this_month", "show": "hours"}).content.decode()
        self.assertIn("IDLE-1", html)                                    # a unit with 0 h still gets a row
        self.assertIn("WT-1", html)
        self.assertNotIn("TOOL-1", html)                                 # not a unit
        self.assertLess(html.index("A 84828"), html.index("IDLE-1"))     # most NPT first
        self.assertIn("never had NPT", html)

    def test_unit_table_keeps_units_that_have_entries_but_are_no_longer_marked(self):
        self.log(self.tool, 4)                                           # logged before the classification was un-marked
        rows = npt.unit_table([self.truck], [SQBTrackerEntry.objects.select_related("unit__classification__department").get()])
        self.assertEqual([r["label"] for r in rows], ["TOOL-1", "A 84828"])

    def test_entries_with_no_unit_get_their_own_row_so_totals_add_up(self):
        SQBTrackerEntry.objects.create(date=datetime.date.today(), npt_hours=9, client="Client A")   # legacy
        self.log(self.truck, 3)
        rows = npt.unit_table([self.truck], list(SQBTrackerEntry.objects.select_related("unit__classification__department")))
        self.assertEqual({r["label"]: r["hours"] for r in rows}, {"No unit recorded": 9.0, "A 84828": 3.0})

    def test_unit_kpi_appears_for_a_unit_with_no_npt_yet(self):
        NPTKPI.objects.create(name="Idle unit", target=5, unit=self.idle)
        self.assertIn("0 / 5 h", self.client.get(reverse("sqb:tracker_list"), {"period": "this_month", "show": "hours"}).content.decode())

    def test_the_unit_table_ignores_the_unit_filter_so_units_stay_comparable(self):
        self.log(self.truck, 5)
        self.log(self.spread, 1)
        html = self.client.get(reverse("sqb:tracker_list"), {"period": "this_month", "unit": self.truck.pk, "show": "hours"}).content.decode()
        self.assertIn("WT-1", html)                                      # still listed while A 84828 is selected

    def test_kpi_form_offers_the_same_units(self):
        names = {u.name for u in NPTKPIForm().fields["unit"].queryset}
        self.assertEqual(names, {"A 84828", "IDLE-1", "WT-1"})

    # ── Departments side ──────────────────────────────────────────────────
    def test_classification_can_be_marked_as_units_and_unmarked(self):
        url = reverse("departments:classification_edit", args=[self.tools.pk])
        self.client.post(url, {"name": "Tools", "is_unit": "on"})
        self.tools.refresh_from_db()
        self.assertTrue(self.tools.is_unit)
        self.assertIn("TOOL-1", {u.name for u in SQBTrackerEntryForm().fields["unit"].queryset})
        self.client.post(url, {"name": "Tools"})
        self.tools.refresh_from_db()
        self.assertFalse(self.tools.is_unit)

    def test_viewer_cannot_change_what_counts_as_a_unit(self):
        viewer = User.objects.create_user(username="dep_viewer", password="x")
        Profile.objects.update_or_create(user=viewer, defaults={"role": Role.VIEWER})
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse("departments:classification_edit", args=[self.tools.pk]),
                                          {"name": "Tools", "is_unit": "on"}).status_code, 403)

    def test_new_classification_can_be_created_as_units(self):
        self.client.post(reverse("departments:detail", args=[self.testing.pk]) + "?tab=assets",
                         {"name": "Crew vans", "is_unit": "on"})
        self.assertTrue(Classification.objects.get(name="Crew vans").is_unit)

    def test_unit_classification_page_shows_npt_per_unit(self):
        self.log(self.truck, 4)
        SQBTrackerEntry.objects.create(date=datetime.date.today() - datetime.timedelta(days=90), unit=self.truck,
                                       npt_hours=10, category="equipment")
        html = self.client.get(reverse("departments:classification_detail", args=[self.units.pk])).content.decode()
        self.assertIn("NPT this month", html)
        self.assertIn("NPT total", html)
        self.assertIn("<b>4</b> h", html)                                # this month
        self.assertIn(">14 h<", html)                                    # all time: 4 + 10
        self.assertIn(reverse("sqb:tracker_list") + f"?period=this_month&unit={self.truck.pk}", html)

    def test_other_classifications_do_not_get_npt_columns(self):
        html = self.client.get(reverse("departments:classification_detail", args=[self.tools.pk])).content.decode()
        self.assertNotIn("NPT this month", html)

    def test_existing_classifications_named_like_units_are_switched_on_by_the_migration(self):
        import importlib

        from django.apps import apps
        migration = importlib.import_module("departments.migrations.0004_classification_is_unit")
        Classification.objects.update(is_unit=False)
        migration.flag_existing_unit_classifications(apps, None)
        flagged = set(Classification.objects.filter(is_unit=True).values_list("name", flat=True))
        self.assertEqual(flagged, {"Units"})          # "Test spreads" and "Tools" have no "unit" in the name
        Classification.objects.create(department=self.wireline, name="Well Testing Unit")
        migration.flag_existing_unit_classifications(apps, None)
        self.assertTrue(Classification.objects.get(name="Well Testing Unit").is_unit)
        self.assertFalse(Classification.objects.get(name="Tools").is_unit)
