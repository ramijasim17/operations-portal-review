"""Who is online -- run with:  python manage.py test accounts.tests_online"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from personnel.models import Personnel

from .models import Profile, Role


def make_user(username, role=Role.VIEWER, **extra):
    user = User.objects.create_user(username=username, password="correct-horse-battery", **extra)
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    return User.objects.get(pk=user.pk)


def seen(user):
    return Profile.objects.get(user=user).last_seen


class LastSeenTests(TestCase):
    def test_a_page_visit_marks_the_person_online(self):
        user = make_user("ali")
        self.assertIsNone(seen(user))
        self.client.force_login(user)
        self.client.get(reverse("core:dashboard"))
        self.assertTrue(Profile.objects.get(user=user).is_online)

    def test_the_notification_check_alone_keeps_a_person_online(self):
        user = make_user("ali")
        self.client.force_login(user)
        self.client.get(reverse("notifications:poll"))
        self.assertIn(user.profile, list(Profile.online_now()))

    def test_it_is_not_written_more_than_once_a_minute(self):
        user = make_user("ali")
        self.client.force_login(user)
        self.client.get(reverse("core:dashboard"))
        first = seen(user)
        self.client.get(reverse("core:dashboard"))
        self.assertEqual(seen(user), first)
        Profile.objects.filter(user=user).update(last_seen=timezone.now() - datetime.timedelta(seconds=90))
        self.client.get(reverse("core:dashboard"))
        self.assertGreater(seen(user), first - datetime.timedelta(seconds=1))
        self.assertLess(timezone.now() - seen(user), datetime.timedelta(seconds=30))

    def test_somebody_who_has_gone_quiet_is_no_longer_online(self):
        user = make_user("ali")
        Profile.objects.filter(user=user).update(last_seen=timezone.now() - datetime.timedelta(minutes=10))
        self.assertFalse(Profile.objects.get(user=user).is_online)
        self.assertEqual(list(Profile.online_now()), [])

    def test_signing_out_means_offline_straight_away(self):
        user = make_user("ali")
        self.client.force_login(user)
        self.client.get(reverse("core:dashboard"))
        self.client.post(reverse("accounts:logout"))
        self.assertIsNone(seen(user))
        self.assertEqual(list(Profile.online_now()), [])

    def test_blocked_accounts_are_never_listed(self):
        user = make_user("ali")
        Profile.objects.filter(user=user).update(last_seen=timezone.now(), is_active_employee=False)
        self.assertEqual(list(Profile.online_now()), [])

    def test_the_name_comes_from_the_personnel_record_when_there_is_one(self):
        person = Personnel.objects.create(employee_id="9004", name="Ali Naji Talip", status="Active")
        user = make_user("ali")
        Profile.objects.filter(user=user).update(employee=person)
        self.assertEqual(Profile.objects.get(user=user).display_name, "Ali Naji Talip")
        self.assertEqual(make_user("sam").profile.display_name, "sam")


class OnlineScreensTests(TestCase):
    def setUp(self):
        self.boss = make_user("boss", Role.ADMIN)
        self.zain = make_user("zain", first_name="Zain", last_name="Shalhom")
        Profile.objects.filter(user=self.zain).update(last_seen=timezone.now())
        self.old = make_user("old")
        Profile.objects.filter(user=self.old).update(last_seen=timezone.now() - datetime.timedelta(hours=3))

    def test_the_dashboard_lists_who_is_online_to_every_role(self):
        for who in (self.boss, self.zain):
            with self.subTest(who=who.username):
                self.client.force_login(who)
                page = self.client.get(reverse("core:dashboard"))
                self.assertContains(page, "Online now")
                self.assertContains(page, "Zain Shalhom")
                self.assertNotContains(page, "old</span>")

    def test_access_control_shows_online_and_last_seen(self):
        self.client.force_login(self.boss)
        page = self.client.get(reverse("accounts:access_control"))
        self.assertContains(page, "online now")
        self.assertContains(page, "Online")
        self.assertContains(page, "seen 3 hours ago")
