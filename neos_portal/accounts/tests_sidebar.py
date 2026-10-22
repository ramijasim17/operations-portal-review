"""The side menu on short screens -- run with:  python manage.py test accounts.tests_sidebar"""
import re

from django.contrib.auth.models import User
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from .models import Profile, Role
from .tests_brand import css


class SidebarStructureTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("viewer", password="correct-horse-battery")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.VIEWER})
        self.client.force_login(user)
        self.html = self.client.get(reverse("core:dashboard")).content.decode()
        self.sidebar = self.html[self.html.index('<aside class="sidebar"'):self.html.index("</aside>")]

    def test_the_menu_and_the_account_block_are_separate_parts_in_that_order(self):
        order = [self.sidebar.index(part) for part in ('class="white-tab"', "<nav>", "</nav>", 'class="sidebar-foot"')]
        self.assertEqual(order, sorted(order))

    def test_notifications_the_theme_switch_and_sign_out_are_all_in_the_fixed_block(self):
        foot = self.sidebar[self.sidebar.index('class="sidebar-foot"'):]
        for needle in ('id="notif-link"', 'class="theme-switch"', 'data-theme-set="dark"', 'class="user-box"', "Sign out"):
            self.assertIn(needle, foot)
        menu = self.sidebar[self.sidebar.index("<nav>"):self.sidebar.index("</nav>")]
        for needle in ("theme-switch", "Sign out", "notif-link"):
            self.assertNotIn(needle, menu)                                  # none of them can scroll out of sight with the menu


class SidebarStyleTests(TestCase):
    def setUp(self):
        self.sheet = css()

    def rule(self, selector):
        match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self.sheet)
        self.assertIsNotNone(match, selector)
        return match.group(1)

    def test_only_the_menu_scrolls_the_sidebar_itself_does_not(self):
        self.assertIn("overflow:hidden", self.rule(".sidebar"))
        self.assertIn("overflow-y:auto", self.rule(".sidebar nav"))
        self.assertIn("min-height:0", self.rule(".sidebar nav"))
        self.assertIn("flex:none", self.rule(".sidebar > .white-tab, .sidebar > .portal-name, .sidebar > .sidebar-foot"))

    def test_the_menu_has_a_scroll_bar_that_is_always_visible(self):
        self.assertIn("width:10px", self.rule(".sidebar nav::-webkit-scrollbar"))
        self.assertIn("::-webkit-scrollbar-thumb", self.sheet)
        self.assertIn("scrollbar-color", self.sheet)                        # the same, for Firefox

    def test_short_screens_get_tighter_spacing_so_the_menu_keeps_room(self):
        self.assertIn("@media (max-height: 960px)", self.sheet)
        self.assertIn("@media (max-height: 720px)", self.sheet)

    def test_the_phone_drawer_no_longer_scrolls_as_one_piece(self):
        drawer = self.sheet[self.sheet.index("@media (max-width: 1000px)"):]
        self.assertNotIn("position:sticky; bottom:-16px", drawer)
        self.assertNotRegex(drawer.split("}", 1)[0], r"overflow-y:auto")

    def test_the_script_brings_the_current_pages_menu_item_into_view(self):
        script = open(finders.find("core/js/portal.js")).read()
        self.assertIn("function initSidebarScroll", script)
        self.assertIn("initSidebarScroll();", script)


class FooterCreditFixedTests(TestCase):
    def test_footer_is_sticky_at_bottom(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parent.parent / "core/static/core/css/portal.css").read_text()
        line = next(l for l in css.splitlines() if l.startswith(".app-footer{"))
        self.assertIn("position:sticky", line)
        self.assertIn("bottom:0", line)


class NotificationSoundTests(TestCase):
    def test_sound_toggle_and_chime_are_wired_up(self):
        import pathlib
        from django.conf import settings
        js = (pathlib.Path(settings.BASE_DIR) / "core/static/core/js/portal.js").read_text()
        self.assertIn("chime()", js)
        self.assertIn("neos-sound", js)
        html = (pathlib.Path(settings.BASE_DIR) / "core/templates/core/base.html").read_text()
        self.assertIn('id="sound-toggle"', html)


class LiveRefreshTests(TestCase):
    def test_live_refresh_is_wired_up_and_skips_risky_pages(self):
        import pathlib
        from django.conf import settings
        js = (pathlib.Path(settings.BASE_DIR) / "core/static/core/js/portal.js").read_text()
        self.assertIn("function initLiveRefresh", js)
        self.assertIn("EVERY = 5000", js)
        self.assertIn("initLiveRefresh();", js)
        self.assertIn("whiteboard", js)                    # the whiteboard is never refreshed under the user
        self.assertIn('main.querySelector("script', js)    # pages that run their own scripts are left alone


class OperationsGroupTests(TestCase):
    def setUp(self):
        from django.contrib.auth.models import User
        from accounts.models import Profile, Role

        self.user = User.objects.create_user("opsuser", password="x")
        Profile.objects.update_or_create(user=self.user, defaults={"role": Role.VIEWER})
        self.client.force_login(self.user)

    def test_four_modules_sit_under_one_operations_item(self):
        page = self.client.get(reverse("core:dashboard"))
        items = page.context["nav_items"]
        group = next(i for i in items if i["label"] == "Operations")
        self.assertEqual([c["label"] for c in group["children"]], ["Job Manager", "Job Reports", "SQB", "SGS Reports"])
        top_labels = [i["label"] for i in items]
        for gone in ("Job Manager", "Job Reports", "SQB", "SGS Reports"):
            self.assertNotIn(gone, top_labels)                 # no longer separate menu items
        self.assertContains(page, 'data-nav-group')
        self.assertContains(page, reverse("job_manager:list"))

    def test_the_group_is_highlighted_on_any_of_its_pages(self):
        for name in ("job_manager:list", "job_reports:list", "sqb:job_list", "sgs_report:list"):
            page = self.client.get(reverse(name))
            group = next(i for i in page.context["nav_items"] if i["label"] == "Operations")
            self.assertTrue(group["is_active"], name)
            self.assertEqual([c["url_name"] for c in group["children"] if c["is_active"]], [name], name)
        other = self.client.get(reverse("maintenance:list"))
        self.assertFalse(next(i for i in other.context["nav_items"] if i["label"] == "Operations")["is_active"])


class FlyoutOnTopTests(TestCase):
    def test_the_menu_sits_above_the_page_so_the_flyout_is_never_hidden_behind_cards(self):
        import pathlib
        import re
        from django.conf import settings
        css = (pathlib.Path(settings.BASE_DIR) / "core/static/core/css/portal.css").read_text()
        rule = re.search(r"\n\.sidebar\{(.*?)\n\}", css, re.S).group(1)
        self.assertRegex(rule, r"z-index:\s*60")
