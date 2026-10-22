"""Fixing who an account belongs to -- run with:  python manage.py test accounts.tests_access_control"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from personnel.models import Personnel

from .models import Profile, Role


def make_user(username, role=Role.VIEWER, **extra):
    user = User.objects.create_user(username=username, password="correct-horse-battery", **extra)
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    return User.objects.get(pk=user.pk)


class RelinkTests(TestCase):
    def setUp(self):
        self.boss = make_user("boss", Role.ADMIN)
        self.client.force_login(self.boss)
        self.aziz = Personnel.objects.create(employee_id="9004", name="Aziz Mezhel", status="Active")
        self.bambang = Personnel.objects.create(employee_id="9100", name="Bambang Ardhianto", status="Active")
        self.imposter = make_user("Bino77", email="bambang@yahoo.com")
        Profile.objects.filter(user=self.imposter).update(employee=self.aziz)

    def link(self, user, person):
        return self.client.post(reverse("accounts:access_control"), {
            "profile_id": user.profile.pk, "link_employee": "1", "employee": person.pk if person else ""}, follow=True)

    def test_an_admin_can_move_an_account_to_the_right_employee(self):
        self.link(self.imposter, self.bambang)
        self.assertEqual(Profile.objects.get(user=self.imposter).employee, self.bambang)

    def test_an_admin_can_unlink_an_account(self):
        self.link(self.imposter, None)
        self.assertIsNone(Profile.objects.get(user=self.imposter).employee)

    def test_the_error_names_the_account_that_holds_the_employee(self):
        other = make_user("someone")
        page = self.link(other, self.aziz)
        self.assertContains(page, "already linked to @Bino77")
        self.assertIsNone(Profile.objects.get(user=other).employee)

    def test_after_unlinking_the_real_person_can_register_with_their_id(self):
        self.link(self.imposter, None)
        User.objects.filter(pk=self.imposter.pk).update(is_active=False)
        self.client.logout()
        self.client.post(reverse("accounts:signup"), {
            "employee_id": "9004", "username": "aziz", "email": "aziz@company.com",
            "password": "a-Long-passphrase-42", "password_confirm": "a-Long-passphrase-42"})
        self.assertEqual(User.objects.get(username="aziz").profile.employee, self.aziz)

    def test_the_dropdown_shows_the_employee_id_next_to_the_name(self):
        page = self.client.get(reverse("accounts:access_control"))
        self.assertContains(page, "Aziz Mezhel — ID 9004")
        self.assertContains(page, "Change employee")
