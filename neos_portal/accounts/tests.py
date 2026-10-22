"""
Permission tests -- run with:  python manage.py test accounts

Two guarantees:
  1. COVERAGE  Every route in the portal has a rule in accounts/policy.py.
               Add a new page and forget to classify it, and this fails.
  2. ENFORCEMENT  Every route is tried as Level 1, Level 2, Level 3 and Admin (GET and
               POST) and must be refused (403) for exactly the roles that
               shouldn't reach it, and never for the roles that should.
"""
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import get_resolver, reverse
from django.urls.converters import IntConverter

from .models import Profile, Role
from .policy import (
    DELETE, DOWNLOAD, EDIT, INLINE_MEDIA_DIRS, POLICY, READ, UNMANAGED_NAMESPACES,
    required_permission,
)

# What each role is allowed to do.
ABILITIES = {
    Role.VIEWER: {"can_edit": False, "can_download": False, "can_delete": False, "can_approve": False},
    Role.EDITOR: {"can_edit": True, "can_download": True, "can_delete": False, "can_approve": False},
    Role.APPROVER: {"can_edit": True, "can_download": True, "can_delete": True, "can_approve": True},
    Role.ADMIN: {"can_edit": True, "can_download": True, "can_delete": True, "can_approve": True},
}


def walk_routes(patterns=None, namespace=""):
    """Yield (view_name, kwargs-to-reverse-it) for every named route."""
    for p in patterns if patterns is not None else get_resolver().url_patterns:
        if hasattr(p, "url_patterns"):
            yield from walk_routes(p.url_patterns, namespace + (p.namespace + ":" if p.namespace else ""))
        elif p.name:
            kwargs = {
                name: (1 if isinstance(conv, IntConverter) else "x")
                for name, conv in getattr(p.pattern, "converters", {}).items()
            }
            yield namespace + p.name, kwargs


def managed_routes():
    return {name: kw for name, kw in walk_routes() if name.split(":")[0] not in UNMANAGED_NAMESPACES}


class PolicyCoverageTests(TestCase):
    def test_every_route_is_classified(self):
        missing = sorted(set(managed_routes()) - set(POLICY))
        self.assertEqual(
            missing, [],
            "These routes have no rule in accounts/policy.py -- add each one as READ, EDIT, DOWNLOAD or DELETE: %s" % missing,
        )

    def test_no_misspelled_policy_entries(self):
        mounted_namespaces = {name.split(":")[0] for name in managed_routes()}
        stale = sorted(
            n for n in POLICY
            if n.split(":")[0] in mounted_namespaces and n not in managed_routes()
        )
        self.assertEqual(stale, [], "Policy entries that match no real route: %s" % stale)


class RoleEnforcementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.clients = {}
        for role in ABILITIES:
            user = User.objects.create_user(username=f"test_{role}", password="x")
            Profile.objects.update_or_create(user=user, defaults={"role": role, "is_active_employee": True})
            client = Client(raise_request_exception=False)
            client.force_login(user)
            cls.clients[role] = client

    def expected_denied(self, role, view_name, method):
        needed = required_permission(view_name, method)
        return bool(needed) and not ABILITIES[role][needed]

    def test_every_route_for_every_role(self):
        routes = {n: kw for n, kw in managed_routes().items() if n in POLICY}
        self.assertTrue(routes, "no routes found to test")
        for view_name, kwargs in sorted(routes.items()):
            url = reverse(view_name, kwargs=kwargs)
            for role, client in self.clients.items():
                for method in ("GET", "POST"):
                    with self.subTest(route=view_name, role=role, method=method):
                        response = client.generic(method, url)
                        if self.expected_denied(role, view_name, method):
                            self.assertEqual(response.status_code, 403, f"{role} {method} {url} should be refused")
                        else:
                            self.assertNotEqual(response.status_code, 403, f"{role} {method} {url} should be allowed through")

    def test_classification_is_sensible(self):
        """Doesn't trust the table: the route's own name and URL give away what
        it does, so a delete page filed as READ (or an export filed as EDIT)
        is caught here even though the sweep above would happily agree with it."""
        problems = []
        for name, kwargs in sorted(managed_routes().items()):
            kind = POLICY.get(name)
            if kind is None:
                continue
            # The app's own name is left out on purpose ("job_manager" contains "manage").
            # Same for the URL's first segment ("/wire-management/" also contains "manage").
            url_rest = reverse(name, kwargs=kwargs).lstrip("/").split("/", 1)[-1]
            clue = (name.split(":", 1)[1] + " " + url_rest).lower()
            if "delete" in clue or name.endswith("clear_completed"):
                if kind != DELETE:
                    problems.append(f"{name} looks like a delete but is {kind}")
            elif any(word in clue for word in ("export", "download", "generate", "zip")):
                if kind != DOWNLOAD:
                    problems.append(f"{name} looks like a download but is {kind}")
            elif any(word in clue for word in ("create", "edit", "import", "upload", "manage", "save", "fill", "close", "update", "add")):
                if kind == READ:
                    problems.append(f"{name} looks like it changes data but is READ")
        self.assertEqual(problems, [])

    def test_policy_matches_the_documented_rules(self):
        """Spot-check the rules themselves, independent of any one route."""
        self.assertIsNone(required_permission("personnel:list", "GET"))          # anyone can look
        self.assertEqual(required_permission("personnel:list", "POST"), "can_edit")  # writing needs Edit
        self.assertEqual(required_permission("personnel:edit", "GET"), "can_edit")
        self.assertEqual(required_permission("personnel:delete", "POST"), "can_delete")
        self.assertEqual(required_permission("sgs_report:export_excel", "GET"), "can_download")
        self.assertIsNone(required_permission("some_app:unclassified_page", "GET"))
        self.assertEqual(required_permission("some_app:unclassified_page", "POST"), "can_edit")

    def test_local_media_files_need_download_but_report_photos_do_not(self):
        media = settings.MEDIA_URL
        if not media.startswith("/"):
            self.skipTest("MEDIA_URL is not a local path (files are served from the bucket)")
        cert = media + "personnel_certs/anyone.pdf"
        photo = media + INLINE_MEDIA_DIRS[0] + "photo.png"
        self.assertEqual(self.clients[Role.VIEWER].get(cert).status_code, 403, "a Level 1 account opened a certificate")
        for role in (Role.VIEWER, Role.EDITOR):
            self.assertNotEqual(self.clients[role].get(photo).status_code, 403, f"{role} blocked from a report photo")
        for role in (Role.EDITOR, Role.APPROVER, Role.ADMIN):
            self.assertNotEqual(self.clients[role].get(cert).status_code, 403, f"{role} can download so can open certificates")

    def test_denied_page_explains_itself(self):
        response = self.clients[Role.VIEWER].get(reverse("personnel:create"))
        self.assertEqual(response.status_code, 403)
        self.assertIn(b"Access denied", response.content)
