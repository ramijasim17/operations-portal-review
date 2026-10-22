"""Light / Night / Auto -- run with:  python manage.py test accounts.tests_theme"""
import re

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Profile, Role
from .tests_brand import INK, WHITE, contrast, css


def block(sheet, opener):
    """The declarations inside the first CSS block that starts with `opener`."""
    start = sheet.index(opener) + len(opener)
    return sheet[start:sheet.index("\n}", start)]


def tokens(text):
    return {name: value.strip() for name, value in re.findall(r"(--[\w-]+):\s*([^;]+);", text)}


def resolved(token_map, name, light):
    value = token_map.get(name) or light.get(name)
    match = re.fullmatch(r"var\((--[\w-]+)\)", value or "")
    return resolved(token_map, match.group(1), light) if match else value


class ThemeSwitchTests(TestCase):
    def test_account_pages_have_the_theme_script_and_the_three_choices(self):
        for name in ("accounts:login", "accounts:signup", "accounts:password_reset"):
            with self.subTest(page=name):
                page = self.client.get(reverse(name))
                self.assertContains(page, "core/js/theme.js")
                for choice in ("light", "dark", "auto"):
                    self.assertContains(page, f'data-theme-set="{choice}"')
                self.assertContains(page, 'name="theme-color"')

    def test_signed_in_pages_have_the_switch_in_the_sidebar(self):
        user = User.objects.create_user("viewer", password="correct-horse-battery")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.VIEWER})
        self.client.force_login(user)
        page = self.client.get(reverse("core:dashboard"))
        html = page.content.decode()
        self.assertIn("core/js/theme.js", html)
        self.assertLess(html.index("theme.js"), html.index("<body"))          # in <head>, so the theme is set before the page paints
        self.assertLess(html.index('id="sidebar"'), html.index("theme-switch"), "the switch lives in the sidebar")
        for choice in ("light", "dark", "auto"):
            self.assertContains(page, f'data-theme-set="{choice}"')


class NightPaletteTests(TestCase):
    def setUp(self):
        self.sheet = css()
        self.light = tokens(block(self.sheet, ":root{"))
        self.dark = tokens(block(self.sheet, ':root[data-theme="dark"]{'))

    def role(self, name):
        return resolved(self.dark, name, self.light)

    def test_night_only_redefines_roles_that_exist_in_light(self):
        self.assertEqual(sorted(set(self.dark) - set(self.light)), [])

    def test_every_surface_text_and_button_role_has_a_night_value(self):
        for name in ("--bg", "--surface", "--surface-2", "--border", "--border-strong", "--text", "--text-secondary", "--link", "--heading",
                     "--hover", "--field-bg", "--track", "--primary-bg", "--primary-fg", "--primary-hover", "--danger", "--warning", "--success",
                     "--chart-bg", "--chart-text", "--chart-grid", "--shadow"):
            self.assertIn(name, self.dark, name)

    def test_night_uses_the_brand_darks_and_cyan(self):
        self.assertEqual(self.dark["--bg"].upper(), INK)                  # the page is Ink
        self.assertEqual(self.dark["--primary-bg"].upper(), "#00A3E4")    # buttons are Flow Cyan ...
        self.assertEqual(self.dark["--primary-fg"].upper(), INK)          # ... with dark text, never white

    def test_night_text_is_readable(self):
        bg, surface = self.role("--bg"), self.role("--surface")
        self.assertGreaterEqual(contrast(self.role("--text"), bg), 7)
        self.assertGreaterEqual(contrast(self.role("--text"), surface), 7)
        self.assertGreaterEqual(contrast(self.role("--text-secondary"), surface), 4.5)
        self.assertGreaterEqual(contrast(self.role("--text-secondary"), bg), 4.5)
        self.assertGreaterEqual(contrast(self.role("--link"), surface), 4.5)
        self.assertGreaterEqual(contrast(self.role("--link"), bg), 4.5)
        self.assertGreaterEqual(contrast(self.role("--heading"), bg), 7)
        self.assertGreaterEqual(contrast(self.role("--primary-fg"), self.role("--primary-bg")), 4.5)
        self.assertGreaterEqual(contrast(self.role("--primary-fg"), self.role("--primary-hover")), 4.5)
        self.assertGreaterEqual(contrast(self.role("--result-fg"), surface), 4.5)

    def test_night_status_colours_are_readable_on_cards(self):
        surface = self.role("--surface")
        for name in ("--danger", "--warning", "--success"):
            self.assertGreaterEqual(contrast(self.role(name), surface), 4.5, name)
        for name in ("--on-danger", "--on-success", "--on-solid"):
            self.assertGreaterEqual(contrast(self.role(name), self.role("--danger" if name == "--on-danger" else "--success")), 4.5, name)

    def test_light_status_colours_are_readable_on_white(self):
        for name in ("--danger", "--warning", "--success", "--text-secondary", "--link"):
            self.assertGreaterEqual(contrast(resolved(self.light, name, self.light), WHITE), 4.5, name)

    def test_the_sidebar_and_logo_do_not_depend_on_the_theme(self):
        sidebar = re.search(r"\.sidebar\{[^}]*\}", self.sheet).group(0)
        self.assertIn("background:var(--indigo)", sidebar)                # always Energy Indigo
        self.assertIn(".white-tab{display:block; background:var(--white);", self.sheet)   # the logo is always shown on white

    def test_printing_is_always_light(self):
        printing = self.sheet[self.sheet.index("@media print"):]
        self.assertIn(':root[data-theme="dark"]', printing)
        self.assertIn("--bg:#fff", printing)
