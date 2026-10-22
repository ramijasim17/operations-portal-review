"""The visual identity (ABC Brand Identity Guidelines v2.0) -- run with:  python manage.py test accounts.tests_brand"""
import re
from pathlib import Path

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from .models import Profile, Role

INDIGO, CYAN, YELLOW, WHITE = "#43358A", "#00A3E4", "#F5E100", "#FFFFFF"
INK, STEEL, ALLOY = "#1B1830", "#6B6880", "#E4E3EA"
CREDIT = "Designed and built by"


def luminance(hex_colour):
    channels = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def css():
    return Path(finders.find("core/css/portal.css")).read_text()


class BrandPagesTests(TestCase):
    def test_the_sign_in_page_carries_the_logo_the_band_the_credit_and_the_favicon(self):
        page = self.client.get(reverse("accounts:login"))
        for needle in ("core/brand/neos-lockup.png", "core/brand/neos-symbol.png", "signature-band", "white-tab", "hero-cycle",
                       "favicon-32.png", "apple-touch-icon.png", 'name="viewport"', "Energy in continuous motion."):
            self.assertContains(page, needle)
        self.assertContains(page, f"{CREDIT} <b>Rami Jasim</b>")

    def test_every_account_page_has_the_identity_and_the_credit(self):
        for name in ("accounts:signup", "accounts:password_reset"):
            with self.subTest(page=name):
                page = self.client.get(reverse(name))
                self.assertContains(page, "signature-band")
                self.assertContains(page, "neos-lockup.png")
                self.assertContains(page, "Rami Jasim")

    def test_signed_in_pages_have_the_white_tab_logo_the_band_and_the_credit_in_the_footer(self):
        user = User.objects.create_user("boss", password="correct-horse-battery")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.ADMIN})
        self.client.force_login(user)
        for name in ("core:dashboard", "departments:list", "accounts:access_control", "accounts:backup"):
            with self.subTest(page=name):
                page = self.client.get(reverse(name))
                self.assertContains(page, 'class="white-tab"')
                self.assertContains(page, "ABC Company")
                self.assertContains(page, "signature-band")
                self.assertContains(page, "app-footer")
                self.assertContains(page, f"{CREDIT} <b>Rami Jasim</b>")
                self.assertContains(page, "favicon-32.png")
                self.assertContains(page, "menu-logo")

    def test_the_brand_files_exist_and_the_logo_placeholder_is_transparent_capable(self):
        from PIL import Image
        for name in ("neos-lockup.png", "neos-symbol.png", "favicon-32.png", "favicon-192.png", "apple-touch-icon.png"):
            self.assertTrue(finders.find(f"core/brand/{name}"), name)
        lockup = Image.open(finders.find("core/brand/neos-lockup.png"))
        self.assertEqual(lockup.mode, "RGBA")
        self.assertGreaterEqual(lockup.width, 840)                       # minimum lockup size is 140 px; this is sharp on 6x screens


class BrandStylesheetTests(TestCase):
    def test_the_four_brand_colours_and_the_neutrals_are_defined_exactly(self):
        sheet = css()
        for token, value in (("--indigo", INDIGO), ("--cyan", CYAN), ("--yellow", YELLOW), ("--white", WHITE),
                             ("--ink", INK), ("--steel", STEEL), ("--alloy", ALLOY)):
            self.assertRegex(sheet, rf"{re.escape(token)}:\s*{re.escape(value)}", token)

    def test_the_fonts_are_barlow_and_ibm_plex_mono_and_the_old_font_is_gone(self):
        sheet = css()
        self.assertIn("family=Barlow", sheet)
        self.assertIn("IBM+Plex+Mono", sheet)
        self.assertNotIn("Inter", sheet)
        self.assertRegex(sheet, r"--font:\s*'Barlow'")
        self.assertRegex(sheet, r"--mono:\s*'IBM Plex Mono'")

    def test_the_signature_band_is_indigo_cyan_yellow_white_at_4_3_1_1_in_that_order(self):
        sheet = css()
        found = re.findall(r"\.signature-band i:nth-child\((\d)\)\{flex:(\d); background:var\(--(\w+)\);\}", sheet)
        self.assertEqual(found, [("1", "4", "indigo"), ("2", "3", "cyan"), ("3", "1", "yellow"), ("4", "1", "white")])

    def test_the_page_is_white_and_the_dark_theme_is_gone(self):
        sheet = css()
        self.assertRegex(sheet, r"--bg:var\(--white\)")
        self.assertRegex(sheet, r"html\{color-scheme:light;\}")
        self.assertGreater(sheet.index("color-scheme:dark"), sheet.index(':root[data-theme="dark"]'))      # Night exists, but only on request
        self.assertNotIn("#0b0d12", sheet)

    def test_text_pairings_meet_the_contrast_the_guide_promises(self):
        self.assertGreaterEqual(contrast(INK, WHITE), 7)                 # body text on white: AAA
        self.assertGreaterEqual(contrast(INDIGO, WHITE), 7)              # headings and links on white: AAA
        self.assertGreaterEqual(contrast(WHITE, INDIGO), 7)              # the sidebar and primary buttons: AAA
        self.assertGreaterEqual(contrast(STEEL, WHITE), 4.5)             # secondary text on white: AA
        self.assertGreaterEqual(contrast(INK, CYAN), 4.5)                # dark text is what sits on cyan ...
        self.assertGreaterEqual(contrast(INK, YELLOW), 7)                # ... and on yellow
        self.assertLess(contrast(WHITE, CYAN), 3)                        # which is why white text never goes there
        self.assertLess(contrast(WHITE, YELLOW), 1.5)

    def test_no_rule_puts_white_text_on_cyan_or_yellow(self):
        for block in re.findall(r"[^{}]+\{[^{}]*\}", css()):
            props = block.split("{", 1)[1]
            background = re.search(r"background:\s*var\(--(cyan|yellow)\)", props)
            colour = re.search(r"(?<![-\w])color:\s*(#fff\b|#FFF\b|var\(--white\))", props)
            self.assertFalse(background and colour, block.strip()[:120])


class BrandPaletteTests(TestCase):
    def test_chart_colours_start_with_the_brand_colours(self):
        from sqb.npt import PALETTE
        self.assertEqual([c.upper() for c in PALETTE[:3]], [INDIGO, CYAN, YELLOW])
        self.assertEqual(len(PALETTE), len(set(PALETTE)))

    def test_avatar_colours_let_the_dark_brand_text_be_read(self):
        from personnel.templatetags.personnel_extras import AVATAR_COLOURS
        for colour in AVATAR_COLOURS:
            self.assertGreaterEqual(contrast(INK, colour), 4.5, colour)
