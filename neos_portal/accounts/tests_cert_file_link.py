"""The certificate table links to the attached file -- run with:  python manage.py test accounts.tests_cert_file_link"""
import datetime
import shutil
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from departments.models import Asset, AssetCert, CertType, Classification, ClassificationCertRequirement, Department

from .models import Profile, Role

MEDIA = tempfile.mkdtemp(prefix="neos-test-media-")


def make_user(username, role):
    user = User.objects.create_user(username=username, password="correct-horse-battery")
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    return User.objects.get(pk=user.pk)


@override_settings(MEDIA_ROOT=MEDIA)
class CertFileLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.dept = Department.objects.create(name="Wireline", location="Site A")
        cls.gears = Classification.objects.create(department=cls.dept, name="Lifting Gears")
        cls.cert_type = CertType.objects.create(name="LOLER")
        ClassificationCertRequirement.objects.create(classification=cls.gears, cert_type=cls.cert_type)
        cls.with_file = Asset.objects.create(classification=cls.gears, name="91D", description="Shackle")
        cls.without_file = Asset.objects.create(classification=cls.gears, name="97C", description="Shackle")
        soon = timezone.localdate() + datetime.timedelta(days=100)
        AssetCert.objects.create(asset=cls.with_file, cert_type=cls.cert_type, expiry_date=soon,
                                 certificate_file=SimpleUploadedFile("EXP_91D.pdf", b"%PDF-1.4 test"))
        AssetCert.objects.create(asset=cls.without_file, cert_type=cls.cert_type, expiry_date=soon)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def page(self, user):
        self.client.force_login(user)
        url = reverse("departments:detail", args=[self.dept.pk]) + f"?tab=certificates&classification={self.gears.pk}"
        return self.client.get(url).content.decode()

    def test_people_who_may_download_get_a_link_to_the_file_only_where_there_is_one(self):
        html = self.page(make_user("editor", Role.EDITOR))
        self.assertEqual(html.count('title="Open the certificate file"'), 1)
        self.assertIn("EXP_91D", html)

    def test_view_only_people_do_not_get_the_link(self):
        html = self.page(make_user("viewer", Role.VIEWER))
        self.assertNotIn('title="Open the certificate file"', html)
        self.assertNotIn("EXP_91D", html)
