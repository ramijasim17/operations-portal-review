from decimal import Decimal

from django.test import TestCase

from wire_management.forms import WireDrumForm
from wire_management.models import WireDrum
from core.templatetags.units import ft_m


class WireUnitTests(TestCase):
    base = {"wire_number": "#1", "diameter": "0.108", "drum_number": "D1"}

    def test_metres_are_stored_as_feet(self):
        form = WireDrumForm({**self.base, "total_length_ft": "1000", "length_unit": "m"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().total_length_ft, Decimal("3280.8"))

    def test_feet_are_left_alone(self):
        form = WireDrumForm({**self.base, "total_length_ft": "1000", "length_unit": "ft"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().total_length_ft, Decimal("1000.0"))

    def test_display_shows_both_units(self):
        self.assertEqual(ft_m(Decimal("1000")), "1000.0 ft (304.8 m)")
