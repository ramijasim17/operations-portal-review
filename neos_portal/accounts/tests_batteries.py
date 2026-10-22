"""Battery capacity: mAh used per job (hours x the gauge's draw), what is left, and the message after a job."""
import datetime

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from sgs_report.models import Battery, BatteryUsage, Gauge, SGSReport

from .models import Role
from .tests_actions_approvals import login

D = datetime.datetime


def hours(h, m=0):
    return h + m / 60


class BatteryMathTests(TestCase):
    def setUp(self):
        self.battery = Battery.objects.create(serial_number="B-01")
        self.gauge = Gauge.objects.create(serial_number="D8699", calibration_certificate="x.png", battery=self.battery)

    def job(self, well, start, h, m=0, draw=None):
        if draw is not None:
            Gauge.objects.filter(pk=self.gauge.pk).update(draw_ma=draw)
            self.gauge.refresh_from_db()
        report = SGSReport.objects.create(
            well_name=well, bottom_gauge=self.gauge,
            bottom_gauge_connected_at=start, bottom_gauge_disconnected_at=start + datetime.timedelta(hours=h, minutes=m))
        report.record_battery_usage()
        return report

    def test_defaults(self):
        self.assertEqual((self.battery.capacity_mah, self.battery.used_before_mah, self.gauge.draw_ma), (13000.0, 0.0, 8.49))
        self.assertEqual(self.battery.remaining_mah(), 13000.0)

    def test_a_job_costs_hours_times_the_draw_like_the_battery_sheet(self):
        # rows of the sheet: 4:34 -> 38.78, 5:19 -> 45.15, 6:05 -> 51.75, 15:00 -> 128.87
        for h, m, sheet in ((4, 34, 38.78), (5, 19, 45.15), (6, 5, 51.75), (15, 0, 128.87)):
            usage = self.job(f"W{h}{m}", D(2026, 4, 1, 8), h, m).battery_usages.get()
            self.assertAlmostEqual(usage.consumed_mah, sheet, delta=1.6)      # the 15 h row is the sheet's biggest outlier
            self.assertAlmostEqual(usage.consumed_mah, (h + m / 60) * 8.49, delta=0.01)
            self.assertEqual(usage.draw_ma, 8.49)

    def test_remaining_and_percent_after_several_jobs(self):
        self.job("A", D(2026, 4, 1, 8), 4)                      # 33.96 mAh
        self.job("B", D(2026, 4, 5, 8), 4, 30)                  # 38.205 mAh
        self.assertAlmostEqual(self.battery.used_mah(), 72.165, delta=0.01)
        self.assertAlmostEqual(self.battery.remaining_mah(), 12927.835, delta=0.01)
        self.assertAlmostEqual(self.battery.percent_left(), 99.44, places=1)
        self.assertFalse(self.battery.is_low())

    def test_used_before_counts_and_low_is_under_15_percent(self):
        self.battery.used_before_mah = 11100
        self.battery.save()
        self.assertEqual(self.battery.remaining_mah(), 1900)
        self.assertTrue(self.battery.is_low())                  # 1900 < 15% of 13000 (1950)
        self.battery.used_before_mah = 11000
        self.assertFalse(self.battery.is_low())

    def test_changing_the_draw_later_does_not_rewrite_old_jobs(self):
        old = self.job("A", D(2026, 4, 1, 8), 4)
        self.job("B", D(2026, 4, 5, 8), 4, draw=10)
        old_usage = BatteryUsage.objects.get(report=old)
        self.assertEqual((old_usage.draw_ma, old_usage.consumed_mah), (8.49, 33.96))
        self.assertEqual(BatteryUsage.objects.get(report__well_name="B").consumed_mah, 40.0)

    def test_saving_a_job_again_updates_it_instead_of_adding_it_twice(self):
        report = self.job("A", D(2026, 4, 1, 8), 4)
        report.bottom_gauge_disconnected_at = D(2026, 4, 1, 14)
        report.save()
        report.record_battery_usage()
        self.assertEqual(report.battery_usages.count(), 1)
        self.assertEqual(report.battery_usages.get().consumed_mah, 50.94)

    def test_the_summary_for_a_job(self):
        report = self.job("A", D(2026, 4, 1, 8), 4, 34)
        row = report.battery_summary()[0]
        self.assertEqual((row["side"], row["hours_text"]), ("Bottom", "4 h 34 min"))
        self.assertAlmostEqual(row["consumed"], 38.77, delta=0.02)
        self.assertFalse(row["low"])
        self.assertGreater(row["hours_left"], 1500)

    def test_no_battery_or_no_times_means_no_summary(self):
        plain = SGSReport.objects.create(well_name="X")
        self.assertEqual(plain.battery_summary(), [])


class BatteryPageTests(TestCase):
    def setUp(self):
        self.battery = Battery.objects.create(serial_number="B-01")
        self.gauge = Gauge.objects.create(serial_number="D8699", calibration_certificate="x.png", battery=self.battery)
        self.report = SGSReport.objects.create(well_name="R-376", bottom_gauge=self.gauge)

    def apply_times(self, hours_connected):
        start = D(2026, 4, 18, 10, 0)
        return self.client.post(
            reverse("sgs_report:detail", args=[self.report.pk]) + "?tab=gauge_logs",
            {"apply_gauge_times": "1", "side": "bottom", "start_iso": start.isoformat(),
             "end_iso": (start + datetime.timedelta(hours=hours_connected)).isoformat()})

    def test_the_message_after_a_job_says_what_was_used_and_what_is_left(self):
        login(self.client, Role.EDITOR, "e")
        r = self.apply_times(4.5)
        text = " ".join(str(m) for m in get_messages(r.wsgi_request))
        self.assertIn("battery B-01", text)
        self.assertIn("4 h 30 min", text)
        self.assertIn("38.2 mAh", text)                       # 4.5 h x 8.5 mA = 38.25
        self.assertIn("12,962 of 13,000 mAh left", text)
        self.assertNotIn("low", text.lower())

    def test_a_low_battery_warns_to_change_it(self):
        Battery.objects.filter(pk=self.battery.pk).update(used_before_mah=11500)
        login(self.client, Role.EDITOR, "e")
        r = self.apply_times(4)
        msgs = list(get_messages(r.wsgi_request))
        self.assertTrue(any(m.level_tag == "warning" and "change it before the next job" in str(m) for m in msgs))

    def test_battery_page_shows_the_running_balance(self):
        login(self.client, Role.EDITOR, "e")
        self.apply_times(4)
        page = self.client.get(reverse("sgs_report:battery_detail", args=[self.battery.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "R-376")
        self.assertContains(page, "34.0 mAh")
        self.assertEqual(round(page.context["usages"][0].left_after), 12966)

    def test_report_page_shows_the_battery_card(self):
        login(self.client, Role.EDITOR, "e")
        self.apply_times(4)
        page = self.client.get(reverse("sgs_report:detail", args=[self.report.pk]) + "?tab=gauge_logs")
        self.assertContains(page, "Battery used on this job")

    def test_gauge_library_lists_capacity_left(self):
        login(self.client, Role.EDITOR, "e")
        page = self.client.get(reverse("sgs_report:gauge_list"))
        self.assertContains(page, "13000 mAh")
        self.assertContains(page, "8.49 mA")

    def test_editing_a_battery_needs_edit_access_and_checks_the_numbers(self):
        url = reverse("sgs_report:battery_edit", args=[self.battery.pk])
        login(self.client, Role.VIEWER, "v")
        self.client.post(url, {"serial_number": "B-01", "capacity_mah": 9000, "used_before_mah": 0})
        self.battery.refresh_from_db()
        self.assertEqual(self.battery.capacity_mah, 13000)
        self.client.logout()
        login(self.client, Role.EDITOR, "e")
        bad = self.client.post(url, {"serial_number": "B-01", "capacity_mah": 0, "used_before_mah": -5})
        self.assertEqual(bad.status_code, 200)
        self.client.post(url, {"serial_number": "B-01", "capacity_mah": 13000, "used_before_mah": 1020})
        self.battery.refresh_from_db()
        self.assertEqual(self.battery.used_before_mah, 1020)

    def test_battery_log_download_has_the_mah_columns(self):
        import io
        import openpyxl
        login(self.client, Role.ADMIN, "a")
        self.apply_times(4)
        r = self.client.get(reverse("sgs_report:battery_log_export"))
        sheet = openpyxl.load_workbook(io.BytesIO(r.content)).active
        self.assertEqual([c.value for c in sheet[1]][4:], ["Battery Total Hours", "Draw (mA)", "Used This Job (mAh)", "Battery Remaining (mAh)"])
        self.assertEqual(sheet[2][6].value, 33.96)


class AmendTests(TestCase):
    """Amending one job's mAh by hand, after the job."""

    def setUp(self):
        self.battery = Battery.objects.create(serial_number="B-01")
        self.gauge = Gauge.objects.create(serial_number="D8699", calibration_certificate="x.png", battery=self.battery)
        self.report = SGSReport.objects.create(
            well_name="R-376", bottom_gauge=self.gauge,
            bottom_gauge_connected_at=D(2026, 4, 18, 10, 0), bottom_gauge_disconnected_at=D(2026, 4, 18, 14, 0))
        self.report.record_battery_usage()
        self.usage = self.report.battery_usages.get()
        self.url = reverse("sgs_report:battery_usage_edit", args=[self.usage.pk])

    def test_the_default_is_the_sheets_average(self):
        self.assertEqual((self.usage.draw_ma, self.usage.consumed_mah, self.usage.manual), (8.49, 33.96, False))

    def test_amending_sets_the_mah_and_flags_it(self):
        login(self.client, Role.EDITOR, "e")
        r = self.client.post(self.url, {"consumed_mah": "37"})
        self.assertRedirects(r, reverse("sgs_report:battery_detail", args=[self.battery.pk]))
        self.usage.refresh_from_db()
        self.assertEqual((self.usage.consumed_mah, self.usage.manual), (37.0, True))
        self.assertAlmostEqual(self.battery.remaining_mah(), 12963.0)

    def test_an_amended_figure_survives_saving_the_report_again_but_the_hours_follow(self):
        login(self.client, Role.EDITOR, "e")
        self.client.post(self.url, {"consumed_mah": "37"})
        self.report.bottom_gauge_disconnected_at = D(2026, 4, 18, 15, 0)
        self.report.save()
        self.report.record_battery_usage()
        self.usage.refresh_from_db()
        self.assertEqual((self.usage.consumed_mah, self.usage.hours), (37.0, 5.0))

    def test_back_to_calculated(self):
        login(self.client, Role.EDITOR, "e")
        self.client.post(self.url, {"consumed_mah": "37"})
        Gauge.objects.filter(pk=self.gauge.pk).update(draw_ma=9.0)
        self.client.post(self.url, {"reset": "1"})
        self.usage.refresh_from_db()
        self.assertEqual((self.usage.manual, self.usage.draw_ma, self.usage.consumed_mah), (False, 9.0, 36.0))

    def test_validation_and_permissions(self):
        login(self.client, Role.EDITOR, "e")
        self.assertEqual(self.client.post(self.url, {"consumed_mah": "-5"}).status_code, 200)
        self.assertEqual(self.client.post(self.url, {"consumed_mah": "abc"}).status_code, 200)
        self.usage.refresh_from_db()
        self.assertFalse(self.usage.manual)
        self.client.logout()
        login(self.client, Role.VIEWER, "v")
        self.client.post(self.url, {"consumed_mah": "40"})
        self.usage.refresh_from_db()
        self.assertFalse(self.usage.manual)

    def test_it_returns_to_the_page_you_came_from_but_never_to_another_site(self):
        login(self.client, Role.EDITOR, "e")
        back = reverse("sgs_report:detail", args=[self.report.pk]) + "?tab=gauge_logs"
        r = self.client.post(self.url, {"consumed_mah": "37", "next": back})
        self.assertRedirects(r, back, fetch_redirect_response=False)
        r = self.client.post(self.url, {"consumed_mah": "38", "next": "https://evil.example/x"})
        self.assertRedirects(r, reverse("sgs_report:battery_detail", args=[self.battery.pk]))

    def test_pages_show_the_amend_link_and_the_amended_badge(self):
        login(self.client, Role.EDITOR, "e")
        self.assertContains(self.client.get(reverse("sgs_report:battery_detail", args=[self.battery.pk])), "Amend")
        self.client.post(self.url, {"consumed_mah": "37"})
        self.assertContains(self.client.get(reverse("sgs_report:battery_detail", args=[self.battery.pk])), "amended")
        page = self.client.get(reverse("sgs_report:detail", args=[self.report.pk]) + "?tab=gauge_logs")
        self.assertContains(page, "battery_usage_edit".replace("battery_usage_edit", "/sgs-report/batteries/usage/"))
        self.assertContains(page, "amended")
