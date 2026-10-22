"""Security regression tests -- run with:  python manage.py test accounts
(settings: neos_portal.settings_test, which skips the static manifest)."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from personnel.models import Personnel

from .models import EmailVerification, Profile, Role


def make_user(username, role=Role.VIEWER, **extra):
    user = User.objects.create_user(username=username, password="correct-horse-battery", **extra)
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    return user


class SignupTests(TestCase):
    def setUp(self):
        self.person = Personnel.objects.create(employee_id="9004", name="Ali Naji Talip Mohammad", status="Active")

    def signup_data(self, **over):
        data = {"employee_id": "9004", "username": "newbie", "email": "new@company.com",
                "password": "a-Long-passphrase-42", "password_confirm": "a-Long-passphrase-42"}
        return data | over

    def post(self, **over):
        return self.client.post(reverse("accounts:signup"), self.signup_data(**over))

    def test_matching_employee_can_sign_up_and_is_linked(self):
        self.post()
        user = User.objects.get(username="newbie")
        self.assertFalse(user.is_active)
        self.assertEqual(user.profile.employee, self.person)
        self.assertEqual(user.profile.role, Role.VIEWER)
        self.assertEqual(len(mail.outbox), 1)

    def test_unknown_employee_id_is_refused(self):
        self.post(employee_id="1234")
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_employee_id_is_required(self):
        self.post(employee_id="")
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_no_name_is_needed_and_the_account_takes_the_name_on_record(self):
        self.post()
        user = User.objects.get(username="newbie")
        self.assertEqual((user.first_name, user.last_name), ("Ali", "Naji Talip Mohammad"))

    def test_inactive_employee_is_refused(self):
        Personnel.objects.filter(pk=self.person.pk).update(status="Inactive")
        self.post()
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_employee_id_cannot_register_twice(self):
        self.post()
        User.objects.update(is_active=True)  # first account was verified
        self.post(username="second", email="second@company.com")
        self.assertFalse(User.objects.filter(username="second").exists())

    @override_settings(SIGNUP_ALLOWED_DOMAINS=("company.com",))
    def test_optional_email_domain_limit(self):
        self.post(email="x@gmail.com")
        self.assertFalse(User.objects.filter(username="newbie").exists())
        self.post()
        self.assertTrue(User.objects.filter(username="newbie").exists())

    def test_weak_password_is_rejected(self):
        self.post(password="password123", password_confirm="password123")
        self.assertFalse(User.objects.filter(username="newbie").exists())

    def test_verification_code_locks_after_too_many_wrong_guesses(self):
        self.post()
        real = EmailVerification.objects.get().code
        wrong = "000000" if real != "000000" else "111111"
        for _ in range(EmailVerification.MAX_ATTEMPTS):
            self.client.post(reverse("accounts:verify"), {"code": wrong})
        self.assertFalse(User.objects.filter(username="newbie").exists())  # signup discarded
        self.assertEqual(self.client.post(reverse("accounts:verify"), {"code": real}).status_code, 302)

    def test_resend_has_a_cooldown(self):
        self.post()
        self.client.get(reverse("accounts:resend_code"))
        self.assertEqual(len(mail.outbox), 1)  # the immediate resend was refused

    def test_codes_are_six_digits(self):
        for _ in range(50):
            self.assertRegex(EmailVerification.generate_code(), r"^\d{6}$")


class LoginThrottleTests(TestCase):
    def setUp(self):
        cache.clear()
        make_user("alice")

    def test_lockout_after_repeated_failures(self):
        for _ in range(8):
            self.client.post(reverse("accounts:login"), {"username": "alice", "password": "wrong"})
        response = self.client.post(reverse("accounts:login"), {"username": "alice", "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 429)

    def test_normal_login_still_works(self):
        response = self.client.post(reverse("accounts:login"), {"username": "alice", "password": "correct-horse-battery"})
        self.assertEqual(response.status_code, 302)


class AdminRulesTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss", Role.ADMIN)
        self.client.force_login(self.admin)

    def test_last_admin_cannot_demote_themselves(self):
        self.client.post(reverse("accounts:access_control"), {"profile_id": self.admin.profile.pk, "role": Role.VIEWER})
        self.admin.profile.refresh_from_db()
        self.assertEqual(self.admin.profile.role, Role.ADMIN)

    def test_admin_can_deactivate_another_user_but_not_self(self):
        other = make_user("bob", Role.EDITOR)
        self.client.post(reverse("accounts:access_control"), {"profile_id": other.profile.pk, "toggle_active": "1"})
        other.profile.refresh_from_db()
        self.assertFalse(other.profile.is_active_employee)
        self.client.post(reverse("accounts:access_control"), {"profile_id": self.admin.profile.pk, "toggle_active": "1"})
        self.admin.profile.refresh_from_db()
        self.assertTrue(self.admin.profile.is_active_employee)

    def test_deactivated_staff_cannot_use_django_admin(self):
        staff = make_user("ex_staff", Role.ADMIN, is_staff=True, is_superuser=True)
        staff.profile.is_active_employee = False
        staff.profile.save()
        client = Client()
        client.force_login(staff)
        response = client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login", response["Location"])


class DeleteAndUploadTests(TestCase):
    def setUp(self):
        self.admin = make_user("boss", Role.ADMIN)
        self.editor = make_user("ed", Role.EDITOR)

    def test_delete_views_refuse_get(self):
        self.client.force_login(self.admin)
        for name in ("sgs_report:station_delete", "sgs_report:shift_delete", "sgs_report:entry_delete",
                     "sgs_report:image_delete", "sgs_report:gauge_delete", "sgs_report:battery_delete",
                     "departments:classification_delete", "departments:field_delete",
                     "departments:asset_delete", "departments:cert_type_delete", "sqb:document_delete"):
            with self.subTest(view=name):
                self.assertEqual(self.client.get(reverse(name, args=[1])).status_code, 405)

    def test_wire_management_deletes_and_evidence_are_restricted(self):
        self.client.force_login(self.editor)          # Level 2: may download evidence, may not delete
        for name in ("wire_management:delete", "wire_management:entry_delete", "wire_management:client_delete"):
            with self.subTest(view=name):
                self.assertEqual(self.client.post(reverse(name, args=[1])).status_code, 403)
        viewer = User.objects.create_user(username="lvl1", password="x")          # Level 1 (view only) may do neither
        self.client.force_login(viewer)
        for name in ("wire_management:drum_evidence_pdf", "wire_management:entry_evidence_pdf"):
            with self.subTest(view=name):
                self.assertEqual(self.client.post(reverse(name, args=[1])).status_code, 403)

    def test_dangerous_upload_types_are_refused(self):
        self.client.force_login(self.editor)
        bad = SimpleUploadedFile("evil.html", b"<script>alert(1)</script>")
        response = self.client.post(reverse("wire_management:drum_import_excel", args=[1]), {"excel_file": bad})
        self.assertEqual(response.status_code, 400)

    def test_oversized_upload_is_refused(self):
        self.client.force_login(self.editor)
        big = SimpleUploadedFile("big.pdf", b"x" * 2048)
        with override_settings(UPLOAD_MAX_BYTES=1024):
            response = self.client.post(reverse("wire_management:drum_import_excel", args=[1]), {"excel_file": big})
        self.assertEqual(response.status_code, 400)


class ChartJsonTests(TestCase):
    def test_script_breakout_is_escaped(self):
        import json
        from sgs_report.views import _json_for_script
        out = _json_for_script({"label": "Top </script><script>alert(1)</script> & co"})
        self.assertNotIn("</script>", out)
        self.assertNotIn("<", out)
        self.assertEqual(json.loads(out)["label"], "Top </script><script>alert(1)</script> & co")


class SignupHintTests(TestCase):
    def test_no_hint_on_the_real_portal_but_the_tester_hint_when_configured(self):
        self.assertNotContains(self.client.get(reverse("accounts:signup")), "made-up people")
        with override_settings(SIGNUP_HINT="This is a test site with made-up people."):
            self.assertContains(self.client.get(reverse("accounts:signup")), "made-up people")


class FlexibleLoginTests(TestCase):
    def setUp(self):
        cache.clear()
        person = Personnel.objects.create(employee_id="T011", name="Tester 11", status="Active")
        self.user = make_user("rami.j", email="rami@company.com")
        Profile.objects.filter(user=self.user).update(employee=person)

    def login(self, who, password="correct-horse-battery"):
        return self.client.post(reverse("accounts:login"), {"username": who, "password": password})

    def test_username_employee_id_and_email_all_work_and_ignore_case(self):
        for who in ("rami.j", "RAMI.J", "T011", "t011", "rami@company.com", "Rami@Company.com"):
            with self.subTest(who=who):
                self.assertEqual(self.login(who).status_code, 302)
                self.client.logout()

    def test_wrong_password_and_unknown_names_are_refused(self):
        self.assertEqual(self.login("T011", "wrong-password-here").status_code, 200)
        self.assertEqual(self.login("nobody").status_code, 200)

    def test_an_email_shared_by_two_accounts_is_not_guessed(self):
        make_user("other", email="rami@company.com")
        self.assertEqual(self.login("rami@company.com").status_code, 200)
        self.assertEqual(self.login("rami.j").status_code, 302)

    def test_unverified_account_still_cannot_sign_in(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self.login("T011").status_code, 200)


class MailFailureTests(TestCase):
    def test_a_refused_email_shows_a_message_and_leaves_no_half_account(self):
        from unittest import mock
        Personnel.objects.create(employee_id="9004", name="Ali Naji", status="Active")
        data = {"employee_id": "9004", "username": "newbie", "email": "new@company.com",
                "password": "a-Long-passphrase-42", "password_confirm": "a-Long-passphrase-42"}
        with mock.patch("accounts.views.send_mail", side_effect=OSError("smtp refused")):
            response = self.client.post(reverse("accounts:signup"), data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "could not send the verification email")
        self.assertFalse(User.objects.filter(username="newbie").exists())


class DeniedPageTests(TestCase):
    def test_a_view_only_user_can_sign_out_from_the_denied_page(self):
        viewer = User.objects.create_user("lvl1", password="x-long-enough-1")
        self.client.force_login(viewer)
        page = self.client.get(reverse("sgs_report:client_file", args=[1, "top"]))
        self.assertContains(page, "Access denied", status_code=403)
        self.assertContains(page, "Sign out", status_code=403)
        self.assertEqual(self.client.post(reverse("accounts:logout")).status_code, 302)


class BucketStorageViewerTests(TestCase):
    """On the live site files live in a bucket, MEDIA_URL is empty (Django turns that into "/"), and a view-only user
    must still be able to use every page that is not a file."""

    def test_a_view_only_user_can_open_pages_and_sign_out_when_media_url_is_empty(self):
        viewer = User.objects.create_user("lvl1", password="x-long-enough-1")
        self.client.force_login(viewer)
        with override_settings(MEDIA_URL=""):
            self.assertEqual(self.client.get(reverse("core:dashboard")).status_code, 200)
            self.assertEqual(self.client.get(reverse("notifications:poll")).status_code, 200)
            self.assertEqual(self.client.get(reverse("personnel:list")).status_code, 200)
            self.assertEqual(self.client.post(reverse("accounts:logout")).status_code, 302)

    def test_files_in_the_local_media_folder_still_need_download_access(self):
        viewer = User.objects.create_user("lvl1", password="x-long-enough-1")
        self.client.force_login(viewer)
        with override_settings(MEDIA_URL="/media/"):
            self.assertEqual(self.client.get("/media/asset_certs/x.pdf").status_code, 403)
