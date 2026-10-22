"""SGS: gauge times come from the log file, and the run's dates drive the dates of events and stations."""
import datetime
import shutil
import tempfile

from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from sgs_report.forms import SGSSequenceEntryForm, SGSShiftForm, SGSStationForm
from sgs_report.models import Battery, Gauge, SGSReport, SGSSequenceEntry, SGSShift, SGSStation

from .models import Role
from .tests_actions_approvals import login

ZONE = timezone.get_current_timezone()


def local(y, mo, d, h=0, mi=0, s=0):
    return timezone.make_aware(datetime.datetime(y, mo, d, h, mi, s), ZONE)


CSV_LOG = (
    "Gauge Serial Number: D8699\r\nGauge Model Number: Quantum Quartz\r\nGauge Manufacturer: Kuster Company\r\n"
    "Maximum Recorder Range: 15966\r\nDate of Last Calibration: 02/08/2024\r\nPressure Units: Psia\r\nTemperature Units: F\xb0\r\n"
    "\r\n*****    HEADER PAGE    *****\r\n*****    HEADER PAGE    *****\r\n\r\n"
    "dd/mm/yyyy  Time,Pressure,Temperature,\r\n"
    "18/04/2026  10:29:16,16.867,73.465,\r\n"
    "18/04/2026  12:00:00,16.869,73.464,\r\n"
    "18/04/2026  14:14:59,16.669,96.755,\r\n"
).encode("latin-1")


class RunWindowTests(TestCase):
    def test_a_run_starting_after_midnight_is_on_that_local_day(self):
        report = SGSReport.objects.create(well_name="R", run_start=local(2026, 4, 18, 1, 30), run_end=local(2026, 4, 18, 9))
        self.assertEqual(report.run_window(), (datetime.date(2026, 4, 18), datetime.date(2026, 4, 18)))
        report.refresh_from_db()
        self.assertEqual(report.date_of_test, datetime.date(2026, 4, 18))       # not the UTC day, the 17th

    def test_no_run_set(self):
        self.assertEqual(SGSReport.objects.create(well_name="R").run_window(), (None, None))
        only_start = SGSReport.objects.create(well_name="R", run_start=local(2026, 4, 18, 8))
        self.assertEqual(only_start.run_window(), (datetime.date(2026, 4, 18), None))


class FormTests(TestCase):
    def setUp(self):
        self.report = SGSReport.objects.create(well_name="R", run_start=local(2026, 4, 18, 22), run_end=local(2026, 4, 19, 6))
        self.free = SGSReport.objects.create(well_name="Free")

    def test_the_shift_date_is_prefilled_and_limited_to_the_run(self):
        form = SGSShiftForm(report=self.report)
        self.assertEqual(form.fields["shift_date"].initial, datetime.date(2026, 4, 18))
        attrs = form.fields["shift_date"].widget.attrs
        self.assertEqual((attrs["min"], attrs["max"]), ("2026-04-18", "2026-04-19"))

    def test_a_shift_date_outside_the_run_is_refused(self):
        for day, ok in (("2026-04-18", True), ("2026-04-19", True), ("2026-04-17", False), ("2026-05-18", False)):
            form = SGSShiftForm({"label": "Day", "shift_date": day}, report=self.report)
            self.assertEqual(form.is_valid(), ok, day)
        bad = SGSShiftForm({"label": "Day", "shift_date": "2026-05-18"}, report=self.report)
        bad.is_valid()
        self.assertIn("18 Apr 2026 to 19 Apr 2026", bad.errors["shift_date"][0])

    def test_without_a_run_nothing_is_limited(self):
        form = SGSShiftForm({"label": "Day", "shift_date": "2030-01-01"}, report=self.free)
        self.assertTrue(form.is_valid())
        self.assertNotIn("min", SGSShiftForm(report=self.free).fields["shift_date"].widget.attrs)

    def test_station_and_event_pickers_only_offer_the_run_and_refuse_other_days(self):
        for form_class in (SGSStationForm, SGSSequenceEntryForm):
            form = form_class(report=self.report)
            attrs = form.fields["start_datetime"].widget.attrs
            self.assertEqual((attrs["min"], attrs["max"]), ("2026-04-18T00:00:00", "2026-04-19T23:59:59"))
        base = {"step_no": 1, "md_mgl": 100, "end_datetime": ""}
        inside = SGSStationForm({**base, "start_datetime": "2026-04-19T01:00:00"}, report=self.report)
        self.assertTrue(inside.is_valid(), inside.errors)
        outside = SGSStationForm({**base, "start_datetime": "2026-06-19T01:00:00"}, report=self.report)
        self.assertFalse(outside.is_valid())
        self.assertIn("start_datetime", outside.errors)
        ev = SGSSequenceEntryForm({"sr": "1", "start_datetime": "2026-03-01T10:00:00", "description": "x"}, report=self.report)
        self.assertFalse(ev.is_valid())

    def test_an_open_ended_run_only_has_a_lower_limit(self):
        report = SGSReport.objects.create(well_name="Open", run_start=local(2026, 4, 18, 8))
        attrs = SGSStationForm(report=report).fields["start_datetime"].widget.attrs
        self.assertEqual(attrs["min"], "2026-04-18T00:00:00")
        self.assertNotIn("max", attrs)


class PageTests(TestCase):
    def setUp(self):
        login(self.client, Role.ADMIN, "a")
        self.report = SGSReport.objects.create(well_name="R", run_start=local(2026, 4, 18, 22), run_end=local(2026, 4, 19, 6))
        self.detail = reverse("sgs_report:detail", args=[self.report.pk])

    def test_the_new_shift_form_shows_the_run_date(self):
        page = self.client.get(self.detail + "?tab=final")
        self.assertContains(page, "Run dates:")
        self.assertContains(page, 'value="2026-04-18"')
        self.assertContains(page, 'min="2026-04-18"')

    def test_adding_a_shift_outside_the_run_says_why(self):
        r = self.client.post(self.detail + "?tab=final", {"add_shift": "1", "label": "Day", "shift_date": "2026-06-01"})
        self.assertFalse(SGSShift.objects.exists())
        self.assertTrue(any("Pick a day inside it" in str(m) for m in get_messages(r.wsgi_request)))
        self.client.post(self.detail + "?tab=final", {"add_shift": "1", "label": "Day", "shift_date": "2026-04-18"})
        self.assertEqual(SGSShift.objects.count(), 1)

    def test_station_times_take_the_run_date_and_roll_past_midnight(self):
        self.client.post(self.detail + "?tab=result", {"add_station": "1", "step_no": 1, "md_mgl": 100, "start_time": "23:50:00", "end_time": "00:10:00"})
        station = SGSStation.objects.get()
        self.assertEqual(timezone.localtime(station.start_datetime).date(), datetime.date(2026, 4, 18))
        self.assertEqual(timezone.localtime(station.end_datetime).date(), datetime.date(2026, 4, 19))

    def test_editing_a_station_outside_the_run_is_refused(self):
        station = SGSStation.objects.create(report=self.report, step_no=1, md_mgl=1, start_datetime=local(2026, 4, 18, 23))
        url = reverse("sgs_report:station_edit", args=[station.pk])
        r = self.client.post(url, {"step_no": 1, "md_mgl": 1, "start_datetime": "2026-09-09T10:00:00"})
        self.assertEqual(r.status_code, 200)
        r = self.client.post(url, {"step_no": 1, "md_mgl": 1, "start_datetime": "2026-04-19T02:00:00"})
        self.assertEqual(r.status_code, 302)


class LogTimesTests(TestCase):
    def setUp(self):
        self._media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media, True)
        override = override_settings(MEDIA_ROOT=self._media)
        override.enable()
        self.addCleanup(override.disable)
        login(self.client, Role.EDITOR, "e")
        self.battery = Battery.objects.create(serial_number="B-01")
        self.gauge = Gauge.objects.create(serial_number="D8699", calibration_certificate="x.png", battery=self.battery)
        self.report = SGSReport.objects.create(well_name="R-376", bottom_gauge=self.gauge)
        self.url = reverse("sgs_report:detail", args=[self.report.pk]) + "?tab=gauge_logs"

    def upload(self, content=CSV_LOG, name="R-376-BG.csv"):
        return self.client.post(self.url, {"preview_gauge_log": "1", "bottom_log": SimpleUploadedFile(name, content)})

    def test_uploading_the_log_sets_the_connected_and_disconnected_times(self):
        self.upload()
        self.report.refresh_from_db()
        self.assertEqual(timezone.localtime(self.report.bottom_gauge_connected_at).replace(tzinfo=None), datetime.datetime(2026, 4, 18, 10, 29, 16))
        self.assertEqual(timezone.localtime(self.report.bottom_gauge_disconnected_at).replace(tzinfo=None), datetime.datetime(2026, 4, 18, 14, 14, 59))

    def test_that_drives_the_battery_hours(self):
        self.upload()
        usage = self.report.battery_usages.get()
        self.assertAlmostEqual(usage.hours, 3 + 45 / 60 + 43 / 3600, places=3)          # 10:29:16 -> 14:14:59
        self.assertAlmostEqual(usage.consumed_mah, usage.hours * 8.49, places=1)

    def test_an_empty_run_is_filled_from_it_and_the_date_follows(self):
        r = self.upload()
        self.report.refresh_from_db()
        self.assertEqual(timezone.localtime(self.report.run_start).replace(tzinfo=None), datetime.datetime(2026, 4, 18, 10, 29, 16))
        self.assertEqual(self.report.date_of_test, datetime.date(2026, 4, 18))
        self.assertEqual(self.report.run_window(), (datetime.date(2026, 4, 18), datetime.date(2026, 4, 18)))
        text = " ".join(str(m) for m in get_messages(r.wsgi_request))
        self.assertIn("from the log", text)
        self.assertIn("events and stations follow that date", text)

    def test_a_run_that_is_already_set_is_not_overwritten(self):
        self.report.run_start, self.report.run_end = local(2026, 4, 18, 9), local(2026, 4, 18, 16)
        self.report.save()
        self.upload()
        self.report.refresh_from_db()
        self.assertEqual(timezone.localtime(self.report.run_start).hour, 9)
        self.assertEqual(timezone.localtime(self.report.run_end).hour, 16)
        self.assertEqual(timezone.localtime(self.report.bottom_gauge_connected_at).hour, 10)        # but the gauge times are the log's

    def test_uploading_a_new_log_replaces_the_times(self):
        self.upload()
        later = CSV_LOG.replace(b"18/04/2026  14:14:59", b"18/04/2026  16:00:00")
        self.upload(later, "again.csv")
        self.report.refresh_from_db()
        self.assertEqual(timezone.localtime(self.report.bottom_gauge_disconnected_at).hour, 16)

    def test_both_gauges_make_the_run_cover_them_both(self):
        top = Gauge.objects.create(serial_number="D8698", calibration_certificate="y.png")
        self.report.top_gauge = top
        self.report.save()
        top_log = CSV_LOG.replace(b"10:29:16", b"09:00:00").replace(b"14:14:59", b"15:30:00")
        self.client.post(self.url, {"preview_gauge_log": "1",
                                    "bottom_log": SimpleUploadedFile("b.csv", CSV_LOG), "top_log": SimpleUploadedFile("t.csv", top_log)})
        self.report.refresh_from_db()
        self.assertEqual(timezone.localtime(self.report.run_start).hour, 9)
        self.assertEqual(timezone.localtime(self.report.run_end).hour, 15)

    def test_an_unreadable_file_warns_and_changes_nothing(self):
        r = self.upload(b"hello", "bad.txt")
        self.report.refresh_from_db()
        self.assertIsNone(self.report.bottom_gauge_connected_at)
        self.assertTrue(any(m.level_tag == "warning" for m in get_messages(r.wsgi_request)))
