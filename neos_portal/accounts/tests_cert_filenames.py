"""Expiry dates read from certificate file names, including the EXP prefix."""
import datetime

from django.test import SimpleTestCase

from core.cert_filenames import clean_serial, drop_marker_parts, read_date
from departments.views import _parse_bulk_cert_filename as equipment
from personnel.views import _parse_personnel_cert_filename as personnel

D = datetime.date


class ReadDateTests(SimpleTestCase):
    def test_plain_ddmmyyyy(self):
        self.assertEqual(read_date("03082026"), D(2026, 8, 3))
        self.assertEqual(read_date("20122026"), D(2026, 12, 20))

    def test_exp_prefix_with_any_separator(self):
        for text in ("EXP 20260803", "EXP20260803", "exp-20260803", "Exp.20260803", "EXPIRY 20260803", "EXP: 20260803"):
            self.assertEqual(read_date(text), D(2026, 8, 3), text)

    def test_with_the_marker_eight_digits_are_yyyymmdd_without_it_ddmmyyyy(self):
        self.assertEqual(read_date("EXP 20102010"), D(2010, 10, 20))
        self.assertEqual(read_date("20102010"), D(2010, 10, 20))        # both orders agree here
        self.assertEqual(read_date("EXP 11122011"), D(2011, 12, 11))      # only ddmmyyyy is a real date
        self.assertEqual(read_date("11122011"), D(2011, 12, 11))
        self.assertEqual(read_date("EXP 20111212"), D(2011, 12, 12))

    def test_separated_dates(self):
        self.assertEqual(read_date("2026-08-03"), D(2026, 8, 3))
        self.assertEqual(read_date("03-08-2026"), D(2026, 8, 3))
        self.assertEqual(read_date("03.08.2026"), D(2026, 8, 3))

    def test_text_after_the_date_is_ignored(self):
        self.assertEqual(read_date("EXP 20260620 MPI LUB CLAMP T01"), D(2026, 6, 20))

    def test_not_dates(self):
        for text in ("hello", "", "EXP", "EXP soon", "99999999", "31022026", "1234", "EXPERT 20260803"):
            self.assertIsNone(read_date(text), text)

    def test_marker_only_parts_are_dropped(self):
        self.assertEqual(drop_marker_parts(["EXP", "20260803", "x"]), ["20260803", "x"])
        self.assertEqual(drop_marker_parts(["EXPIRY", "a"]), ["a"])
        self.assertEqual(drop_marker_parts(["EXPERT", "a"]), ["EXPERT", "a"])

    def test_clean_serial(self):
        self.assertEqual(clean_serial("A2190 (1)"), "A2190")
        self.assertEqual(clean_serial("NE12079-2"), "NE12079-2")


class EquipmentFileNameTests(SimpleTestCase):
    def test_your_real_file_names(self):
        for name, serial, expiry in [
            ("EXP 20260803_DES-LF-26-043-316_Single Leg Wire rope sling_A2190.pdf", "A2190", D(2026, 8, 3)),
            ("EXP 20260803_DES-LF-26-043-283_Fuor Leg Wire rope sling_NE12079-2.pdf", "NE12079-2", D(2026, 8, 3)),
            ("EXP 20260731_DES-LF-26-043-248_Single Leg Chain Sling_B42241-1.pdf", "B42241-1", D(2026, 7, 31)),
            ("EXP 20260803_DES-LF-26-043-274_FULL BODY SAFTY HARNES_LS-96271-20139.pdf", "LS-96271-20139", D(2026, 8, 3)),
        ]:
            self.assertEqual(equipment(name), (expiry, serial, None), name)

    def test_the_older_format_still_works(self):
        self.assertEqual(equipment("03082026_cert-type_provider_A2190.pdf"), (D(2026, 8, 3), "A2190", None))
        self.assertEqual(equipment("03082026_A2190.pdf"), (D(2026, 8, 3), "A2190", None))

    def test_marker_as_its_own_part_and_repeated_download_suffix(self):
        self.assertEqual(equipment("EXP_20260803_sling_A2190 (1).pdf"), (D(2026, 8, 3), "A2190", None))

    def test_unreadable_names_say_why(self):
        expiry, serial, error = equipment("sling_A2190.pdf")
        self.assertIsNone(expiry)
        self.assertIn("EXP", error)
        self.assertIsNotNone(equipment("A2190.pdf")[2])


class PersonnelFileNameTests(SimpleTestCase):
    def test_exp_before_the_date(self):
        self.assertEqual(personnel("1234_Ali Hassan_EXP 20260803.pdf"), (D(2026, 8, 3), "1234", None))
        self.assertEqual(personnel("1234_Ali_EXP20260803.pdf"), (D(2026, 8, 3), "1234", None))
        self.assertEqual(personnel("1234_Ali_EXP_20260803.pdf"), (D(2026, 8, 3), "1234", None))

    def test_the_older_format_still_works(self):
        self.assertEqual(personnel("1234_Ali Hassan_03082026.pdf"), (D(2026, 8, 3), "1234", None))

    def test_unreadable(self):
        self.assertIsNotNone(personnel("1234_Ali_foo.pdf")[2])
        self.assertIsNotNone(personnel("1234.pdf")[2])
