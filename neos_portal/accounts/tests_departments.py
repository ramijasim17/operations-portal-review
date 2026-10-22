"""Departments pages: counts, layout, and (importantly) a bounded number of queries."""
import datetime

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from departments import stats as dept_stats
from departments.models import (
    Asset, AssetCert, AssetFieldValue, CertType, Classification, ClassificationCertRequirement,
    Department, FieldDefinition,
)

from .models import Profile, Role

TODAY = datetime.date.today()


def days(n):
    return TODAY + datetime.timedelta(days=n)


class DepartmentPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(name="Wireline", location="Site A")
        cls.pce = Classification.objects.create(department=cls.dept, name="PCE")
        cls.gears = Classification.objects.create(department=cls.dept, name="Lifting Gears")
        cls.f1 = FieldDefinition.objects.create(classification=cls.pce, label="Size", field_type="text")
        cls.f2 = FieldDefinition.objects.create(classification=cls.pce, label="Rating", field_type="text")
        cls.cert = CertType.objects.create(name="Pressure Test")
        ClassificationCertRequirement.objects.create(classification=cls.pce, cert_type=cls.cert)
        cls.assets = []
        for i in range(40):
            a = Asset.objects.create(classification=cls.pce, name=f"P-{i:03d}", description=f"item {i}",
                                     current_location="Workshop" if i % 4 else "Main Field")
            AssetFieldValue.objects.create(asset=a, field_definition=cls.f1, text_value="3in")
            AssetFieldValue.objects.create(asset=a, field_definition=cls.f2, text_value="10k")
            AssetCert.objects.create(asset=a, cert_type=cls.cert, expiry_date=days(-5 if i < 3 else 10 if i < 8 else 200))
            cls.assets.append(a)
        Asset.objects.create(classification=cls.gears, name="G-1")

    def setUp(self):
        user = User.objects.create_user(username="dept_admin", password="x")
        Profile.objects.update_or_create(user=user, defaults={"role": Role.ADMIN})
        self.client.force_login(user)

    # ── counts ────────────────────────────────────────────────────────────
    def test_classification_counts(self):
        s = dept_stats.classification_stats(department=self.dept)
        self.assertEqual(s[self.pce.pk], {"assets": 40, "away": 10, "expired": 3, "soon": 5, "certs": 40})
        self.assertEqual(s[self.gears.pk], {"assets": 1, "away": 0, "expired": 0, "soon": 0, "certs": 0})

    def test_not_applicable_certificates_are_not_counted(self):
        AssetCert.objects.filter(asset=self.assets[0]).update(not_applicable=True)
        self.assertEqual(dept_stats.classification_stats(department=self.dept)[self.pce.pk]["expired"], 2)

    def test_department_counts(self):
        stats = dept_stats.department_stats([self.dept])[self.dept.pk]
        self.assertEqual((stats["classifications"], stats["assets"], stats["away"], stats["expired"], stats["soon"]),
                         (2, 41, 10, 3, 5))

    # ── pages ─────────────────────────────────────────────────────────────
    def test_department_list_shows_the_health_of_each_department(self):
        html = self.client.get(reverse("departments:list")).content.decode()
        self.assertIn("Wireline", html)
        self.assertIn("3 certs expired", html)
        self.assertIn("5 expiring soon", html)
        self.assertIn("out on jobs", html)

    def test_department_detail_tabs(self):
        assets_tab = self.client.get(reverse("departments:detail", args=[self.dept.pk]) + "?tab=assets").content.decode()
        self.assertIn("10 out on jobs", assets_tab)
        self.assertIn("3 expired", assets_tab)
        self.assertIn("no certificates recorded", assets_tab)    # Lifting Gears has none: unknown, not "OK"
        certs = self.client.get(
            reverse("departments:detail", args=[self.dept.pk]) + f"?tab=certificates&classification={self.pce.pk}"
        ).content.decode()
        self.assertIn("Pressure Test", certs)
        self.assertIn("P-000", certs)

    def test_classification_page_shows_locations_as_pills_and_the_toolbar(self):
        html = self.client.get(reverse("departments:classification_detail", args=[self.pce.pk])).content.decode()
        self.assertIn("loc-pill loc-workshop", html)
        self.assertIn("loc-pill loc-away", html)
        self.assertIn("at the workshop", html)
        self.assertIn("Configure fields", html)
        self.assertIn('id="asset-search"', html)
        self.assertIn('class="table-wrap"', html)               # scrolls inside its box, not the whole page
        self.assertIn("Main Field", html)

    def test_classifications_stay_in_their_set_order(self):
        Classification.objects.create(department=self.dept, name="Aardvark")           # sorts first by name
        html = self.client.get(reverse("departments:detail", args=[self.dept.pk]) + "?tab=assets").content.decode()
        names = ["Aardvark", "Lifting Gears", "PCE"]
        positions = [html.index(f">{n}</a></h3>") for n in names]
        self.assertEqual(positions, sorted(positions))

    def test_department_detail_link_back_and_sidebar(self):
        html = self.client.get(reverse("departments:classification_detail", args=[self.pce.pk])).content.decode()
        self.assertIn("crumbs", html)
        self.assertRegex(html, r'href="/departments/" class="active">Departments')

    # ── performance: a page must not run a query per asset / field / certificate ──
    def queries_for(self, url):
        with CaptureQueriesContext(connection) as ctx:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(ctx)

    def test_classification_page_query_count_does_not_grow_with_assets(self):
        url = reverse("departments:classification_detail", args=[self.pce.pk])
        before = self.queries_for(url)
        for i in range(40, 80):
            a = Asset.objects.create(classification=self.pce, name=f"P-{i:03d}")
            AssetFieldValue.objects.create(asset=a, field_definition=self.f1, text_value="x")
        after = self.queries_for(url)
        self.assertLessEqual(after, before + 1, f"query count grew with assets: {before} -> {after}")
        self.assertLess(after, 25)

    def test_certificates_tab_query_count_is_flat(self):
        url = reverse("departments:detail", args=[self.dept.pk]) + f"?tab=certificates&classification={self.pce.pk}"
        before = self.queries_for(url)
        for i in range(40, 80):
            a = Asset.objects.create(classification=self.pce, name=f"P-{i:03d}")
            AssetCert.objects.create(asset=a, cert_type=self.cert, expiry_date=days(50))
        after = self.queries_for(url)
        self.assertLessEqual(after, before + 1, f"query count grew with assets: {before} -> {after}")
        self.assertLess(after, 30)

    def test_certificates_tab_with_selected_columns_stays_flat(self):
        url = reverse("departments:detail", args=[self.dept.pk]) + \
            f"?tab=certificates&classification={self.pce.pk}&cols={self.f1.pk}&cols={self.f2.pk}"
        self.assertLess(self.queries_for(url), 30)
