"""SGS: the data files that go to the client (header as the client expects it, new name, readings as exported)."""
import datetime
import io
import shutil
import tempfile

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from sgs_report import client_files
from sgs_report.models import Gauge, SGSReport

from .models import Role
from .tests_actions_approvals import login

TABLE = (
    "dd/mm/yyyy     Time           Pressure       Temperature    \r\n"
    "15/04/2026     16:15:00       17.057         83.300         \r\n"
    "15/04/2026     16:15:01       17.056         83.300         \r\n"
    "15/04/2026     16:15:02       17.050         83.301         \r\n"
)

# the header the client receives (Kuster's own layout: name padded to 33, then ': value')
CLIENT_HEAD = (
    "Gauge Serial Number              : 8699\r\n"
    "Gauge Model Number               : Quantum Quartz\r\n"
    "Gauge Manufacturer               : Kuster Company\r\n"
    "Maximum Recorder Range           : 15966\r\n"
    "Date of Last Calibration         : 02/08/2024\r\n"
    "Client                           : Client A\r\n"
    "Well NO                          : Ru-618\r\n"
    "Type of survey                   : SGS\r\n"
    "Field                            : South Field\r\n"
    "Date of Survey                   : 20260415\r\n"
    "Pressure Units                   : Psia\r\n"
    "Temperature Units                : F\xb0\r\n"
    "\r\n"
)

# the header of a CSV straight from Kuster: gauge details and a HEADER PAGE marker, no job lines
CSV_HEAD = (
    "Gauge Serial Number: D8699\r\n"
    "Gauge Model Number: Quantum Quartz\r\n"
    "Gauge Manufacturer: Kuster Company\r\n"
    "Maximum Recorder Range: 15966\r\n"
    "Date of Last Calibration: 02/08/2024\r\n"
    "Pressure Units: Psia\r\n"
    "Temperature Units: F\xb0\r\n"
    "\r\n"
    "*****    HEADER PAGE    *****\r\n"
    "*****    HEADER PAGE    *****\r\n"
    "\r\n"
)
LONG_CSV = (
    "dd/mm/yyyy hh:mm:ss, Delta(Hours), Pressure, Temperature, Internal Temp, Comments\r\n"
    "15/04/2026 16:15:00, 0.0000, 17.057, 83.3, 90.1,\r\n"
    "15/04/2026 16:15:01, 0.0003, 17.0565, 83.3004, 90.1,\r\n"
    "15/04/2026 16:15:02, 0.0006, 17.050, 83.301, 90.1,\r\n"
)
SHORT_CSV = (
    "dd/mm/yyyy  Time,Pressure,Temperature,\r\n"
    "15/04/2026  16:15:00,17.057,83.300,\r\n"
    "15/04/2026  16:15:01,17.057,83.300,\r\n"
    "15/04/2026  16:15:02,17.050,83.301,\r\n"
)


def export(serial="8699", well="Ru-618"):
    """A file as Kuster's software exports it (CRLF, the degree sign as the single byte 0xB0)."""
    return (CLIENT_HEAD.replace(": 8699", f": {serial}").replace("Ru-618", well) + TABLE).encode("latin-1")


def make_report(**kw):
    gauge = Gauge.objects.create(serial_number="D8699", calibration_certificate="x.png")
    values = dict(well_name="Ru-618", client="Client A", field="South Field", report_type="SGS",
                  bottom_gauge=gauge, date_of_test=datetime.date(2026, 4, 15))
    values.update(kw)
    return SGSReport.objects.create(**values)


class HeaderTests(TestCase):
    def setUp(self):
        self.report = make_report()

    def test_the_header_is_the_one_the_client_receives(self):
        made = client_files.prepare(self.report, "bottom", export())
        self.assertEqual(made["content"].decode("latin-1"), CLIENT_HEAD + TABLE)

    def test_a_kuster_csv_header_page_is_replaced_by_it(self):
        raw = (CSV_HEAD + SHORT_CSV).encode("latin-1")
        text = client_files.prepare(self.report, "bottom", raw)["content"].decode("latin-1")
        self.assertEqual(text, CLIENT_HEAD + TABLE.replace("17.056", "17.057"))
        self.assertNotIn("HEADER PAGE", text)

    def test_job_lines_come_from_the_report_not_the_file(self):
        self.report.client, self.report.field, self.report.report_type = "TEEPRH", "West Qurna", "PBU"
        text = client_files.prepare(self.report, "bottom", export())["content"].decode("latin-1")
        self.assertIn("Client                           : TEEPRH\r\n", text)
        self.assertIn("Field                            : West Qurna\r\n", text)
        self.assertIn("Type of survey                   : PBU\r\n", text)
        self.assertNotIn("Client A", text)

    def test_the_serial_in_the_header_is_the_number_only(self):
        for given in ("8699", "D8699"):
            text = client_files.prepare(self.report, "bottom", export(given))["content"].decode("latin-1")
            self.assertTrue(text.startswith("Gauge Serial Number              : 8699\r\n"))
        self.assertEqual(client_files.plain_serial("D8698"), "8698")
        self.assertEqual(client_files.plain_serial("8698"), "8698")

    def test_date_of_survey_is_the_reports_date(self):
        self.report.date_of_test = datetime.date(2026, 4, 18)
        made = client_files.prepare(self.report, "bottom", export())
        self.assertIn("Date of Survey                   : 20260418\r\n", made["content"].decode("latin-1"))
        self.assertTrue(any("20260415" in w for w in made["warnings"]))            # the file said another day

    def test_date_falls_back_to_the_file_then_the_first_reading(self):
        self.report.date_of_test = None
        self.assertIn("20260415", client_files.prepare(self.report, "bottom", export())["name"])
        self.assertIn("20260415", client_files.prepare(self.report, "bottom", (CSV_HEAD + SHORT_CSV).encode("latin-1"))["name"])

    def test_the_degree_sign_stays_one_byte_and_lf_files_stay_lf(self):
        self.assertIn(b"F\xb0", client_files.prepare(self.report, "bottom", export())["content"])
        lf = client_files.prepare(self.report, "bottom", export().replace(b"\r\n", b"\n"))["content"]
        self.assertNotIn(b"\r", lf)

    def test_empty_client_or_field_is_warned_and_falls_back_to_the_files_own_lines(self):
        self.report.client = self.report.field = ""
        made = client_files.prepare(self.report, "bottom", export())
        self.assertIn("Client                           : Client A\r\n", made["content"].decode("latin-1"))
        blank = client_files.prepare(self.report, "bottom", (CSV_HEAD + SHORT_CSV).encode("latin-1"))
        self.assertIn("Client                           : \r\n", blank["content"].decode("latin-1"))
        self.assertTrue(any("Client is empty" in w for w in blank["warnings"]))
        self.assertTrue(any("Field is empty" in w for w in blank["warnings"]))

    def test_not_a_kuster_export_or_missing_header_lines(self):
        with self.assertRaises(ValueError):
            client_files.prepare(self.report, "bottom", b"TotalChans=3\r\nSerialNumber=D8700\r\n[Data]\r\n")
        with self.assertRaises(ValueError):
            client_files.prepare(self.report, "bottom", b"Gauge Serial Number : 1\r\n\r\n" + TABLE.encode())


class ReadingsTests(TestCase):
    def setUp(self):
        self.report = make_report()

    def test_txt_readings_are_copied_byte_for_byte(self):
        raw = export()
        made = client_files.prepare(self.report, "bottom", raw)
        self.assertTrue(made["content"].endswith(raw[raw.index(b"dd/mm/yyyy"):]))
        self.assertFalse(made["converted"])

    def test_csv_readings_become_the_fixed_width_table(self):
        made = client_files.prepare(self.report, "bottom", (CSV_HEAD + LONG_CSV).encode("latin-1"))
        self.assertTrue(made["converted"])
        text = made["content"].decode("latin-1")
        self.assertIn("15/04/2026     16:15:01       17.057         83.300         \r\n", text)   # 17.0565 -> 17.057, 83.3004 -> 83.300
        self.assertIn("15/04/2026     16:15:00       17.057         83.300         \r\n", text)

    def test_unreadable_csv_lines_are_reported_and_an_empty_csv_is_an_error(self):
        messy = (CSV_HEAD + LONG_CSV + "Total, rows, 3\r\n").encode("latin-1")
        self.assertTrue(any("could not be read" in w for w in client_files.prepare(self.report, "bottom", messy)["warnings"]))
        empty = (CSV_HEAD + "dd/mm/yyyy hh:mm:ss, Delta, Pressure, Temperature\r\n").encode("latin-1")
        with self.assertRaises(ValueError):
            client_files.prepare(self.report, "bottom", empty)

    def test_columns_come_from_the_header_names(self):
        self.assertEqual(client_files.column_positions("dd/mm/yyyy  Time,Pressure,Temperature,"), (1, 2))
        self.assertEqual(client_files.column_positions("dd/mm/yyyy hh:mm:ss, Delta(Hours), Pressure, Temperature, Internal Temp"), (2, 3))
        self.assertEqual(client_files.column_positions("dd/mm/yyyy Time"), (2, 3))

    def test_the_gauge_log_preview_reads_both_csv_layouts_and_the_txt(self):
        from sgs_report.views import _parse_kuster_calibrated_csv
        for raw in ((CSV_HEAD + SHORT_CSV), (CSV_HEAD + LONG_CSV), export().decode("latin-1")):
            parsed = _parse_kuster_calibrated_csv(io.BytesIO(raw.encode("latin-1")))
            self.assertEqual(parsed["num_samples"], 3)
            self.assertEqual(parsed["pressure"][0], 17.057)
            self.assertEqual(parsed["temp1"][0], 83.3)
            self.assertEqual(parsed["model_number"], "Quantum Quartz")
            self.assertEqual(parsed["calibration_date"], "02/08/2024")


class NameTests(TestCase):
    def setUp(self):
        self.report = make_report()

    def test_file_name(self):
        self.assertEqual(client_files.prepare(self.report, "bottom", export())["name"], "Ru-618_SGS Data_D8699_Bot_20260415.txt")
        self.report.top_gauge = Gauge.objects.create(serial_number="D8698", calibration_certificate="y.png")
        self.assertEqual(client_files.prepare(self.report, "top", export("8698"))["name"], "Ru-618_SGS Data_D8698_Top_20260415.txt")

    def test_the_serial_is_the_library_one_and_odd_serials_warn(self):
        self.assertEqual(client_files.prepare(self.report, "bottom", export("8699"))["serial"], "D8699")
        wrong = client_files.prepare(self.report, "bottom", export(well="Ru-999"))
        self.assertTrue(any("Ru-999" in w for w in wrong["warnings"]))
        stray = client_files.prepare(self.report, "bottom", export("1234"))
        self.assertEqual(stray["serial"], "D8699")                       # this report's chosen bottom gauge
        self.assertTrue(any("1234" in w for w in stray["warnings"]))
        self.report.bottom_gauge = None
        unknown = client_files.prepare(self.report, "bottom", export("1234"))
        self.assertEqual(unknown["serial"], "1234")
        self.assertTrue(unknown["warnings"])


class DownloadViewTests(TestCase):
    def setUp(self):
        self._media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._media, True)
        override = override_settings(MEDIA_ROOT=self._media)
        override.enable()
        self.addCleanup(override.disable)
        self.report = make_report()
        self.report.bottom_gauge_log_file.save("R.csv", ContentFile((CSV_HEAD + SHORT_CSV).encode("latin-1")))
        self.url = reverse("sgs_report:client_file", args=[self.report.pk, "bottom"])

    def test_admin_downloads_the_renamed_file(self):
        login(self.client, Role.ADMIN, "a")
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertIn("Ru-618_SGS Data_D8699_Bot_20260415.txt", r["Content-Disposition"])
        self.assertEqual(r.content.decode("latin-1"), CLIENT_HEAD + TABLE.replace("17.056", "17.057"))

    def test_level_1_cannot_download_but_level_2_can(self):
        login(self.client, Role.VIEWER, "v")
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.logout()
        login(self.client, Role.EDITOR, "e")
        self.assertEqual(self.client.get(self.url).status_code, 302)          # a Level 2 needs the result approved first
        from approvals.models import Approval
        from django.contrib.contenttypes.models import ContentType
        Approval.objects.create(content_type=ContentType.objects.get_for_model(self.report), object_id=self.report.pk,
                                kind="sgs_result", status="approved")
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_no_file_uploaded_goes_back_to_the_tab_and_unknown_side_is_404(self):
        login(self.client, Role.ADMIN, "a")
        self.assertEqual(self.client.get(reverse("sgs_report:client_file", args=[self.report.pk, "top"])).status_code, 302)
        self.assertEqual(self.client.get(reverse("sgs_report:client_file", args=[self.report.pk, "left"])).status_code, 404)

    def test_the_tab_shows_the_new_name_and_flags_a_txt_source_but_not_a_csv_source(self):
        login(self.client, Role.ADMIN, "a")
        url = reverse("sgs_report:detail", args=[self.report.pk]) + "?tab=gauge_logs"
        page = self.client.get(url)
        self.assertContains(page, "Files for the client")
        self.assertContains(page, "Ru-618_SGS Data_D8699_Bot_20260415.txt")
        self.assertContains(page, "made from the CSV export")
        self.assertNotContains(page, "Made from Kuster's .txt export")
        self.report.bottom_gauge_log_file.save("R.txt", ContentFile(export()))
        self.assertContains(self.client.get(url), "Made from Kuster's .txt export")
