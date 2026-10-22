"""Equipment reservation across manifests, and transfers."""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from departments.models import Asset, Classification, Department
from job_manager.models import EquipmentTransfer, JobManifest
from job_manager.transfers import TransferError, transfer_serials

from .models import Profile, Role


class EquipmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ed", password="x")
        Profile.objects.update_or_create(user=self.user, defaults={"role": Role.EDITOR})
        self.client.force_login(self.user)
        dept = Department.objects.create(name="Wireline", location="Site A")
        cls = Classification.objects.create(department=dept, name="PCE")
        self.a1 = Asset.objects.create(classification=cls, name="A-1", description="one")
        self.a2 = Asset.objects.create(classification=cls, name="A-2", description="two")
        self.a3 = Asset.objects.create(classification=cls, name="A-3", description="three")
        self.m1 = JobManifest.objects.create(job_name="Job One", department=dept, location="Site X", status="Draft")
        self.m2 = JobManifest.objects.create(job_name="Job Two", department=dept, location="Site Y", status="Draft")

    def add(self, manifest, *serials):
        return self.client.post(reverse("job_manager:detail", args=[manifest.pk]),
                                {"add_serials": "1", "add_serial": list(serials)})

    def serials(self, manifest):
        manifest.refresh_from_db()
        return manifest.serial_list()

    # ── reservation ─────────────────────────────────────────────────────
    def test_item_on_a_draft_manifest_is_hidden_from_other_manifests(self):
        self.add(self.m1, "A-1")
        page = self.client.get(reverse("job_manager:detail", args=[self.m2.pk]) + "?tab=add")
        html = page.content.decode()
        self.assertNotIn('value="A-1"', html)       # not offered for picking
        self.assertIn('value="A-2"', html)          # others still are

    def test_item_cannot_be_added_to_a_second_open_manifest(self):
        self.add(self.m1, "A-1")
        response = self.add(self.m2, "A-1", "A-2")
        self.assertEqual(self.serials(self.m2), ["A-2"])        # A-1 refused, A-2 accepted
        self.assertContains(self.client.get(response["Location"]), "already on manifest")

    def test_item_is_free_again_when_the_first_manifest_is_completed(self):
        self.add(self.m1, "A-1")
        JobManifest.objects.filter(pk=self.m1.pk).update(status="Completed")
        self.add(self.m2, "A-1")
        self.assertEqual(self.serials(self.m2), ["A-1"])

    def test_item_is_free_again_when_removed_from_the_first_manifest(self):
        self.add(self.m1, "A-1")
        self.client.post(reverse("job_manager:detail", args=[self.m1.pk]), {"remove_serial": "A-1"})
        self.add(self.m2, "A-1")
        self.assertEqual(self.serials(self.m2), ["A-1"])

    # ── transfers ───────────────────────────────────────────────────────
    def post_transfer(self, source, serials, destination, note=""):
        return self.client.post(reverse("job_manager:detail", args=[source.pk]), {
            "transfer_serials": "1", "transfer_serial": serials, "destination": destination, "note": note})

    def test_transfer_between_manifests(self):
        self.add(self.m1, "A-1", "A-2")
        self.post_transfer(self.m1, ["A-1"], self.m2.pk, "needed at site Y")
        self.assertEqual(self.serials(self.m1), ["A-2"])
        self.assertEqual(self.serials(self.m2), ["A-1"])
        log = EquipmentTransfer.objects.get()
        self.assertEqual((log.asset_name, log.moved_by, log.note), ("A-1", self.user, "needed at site Y"))
        self.assertEqual(log.to_manifest, self.m2)

    def test_transfer_to_an_active_manifest_dispatches_to_its_location(self):
        JobManifest.objects.filter(pk=self.m2.pk).update(status="Active")
        self.add(self.m1, "A-1")
        self.post_transfer(self.m1, ["A-1"], self.m2.pk)
        self.a1.refresh_from_db()
        self.assertEqual(self.a1.current_location, "Site Y")

    def test_transfer_to_workshop_returns_the_item(self):
        self.add(self.m1, "A-1")
        Asset.objects.filter(pk=self.a1.pk).update(current_location="Site X")
        self.post_transfer(self.m1, ["A-1"], "workshop")
        self.a1.refresh_from_db()
        self.assertEqual(self.a1.current_location, "Workshop")
        self.assertEqual(self.serials(self.m1), [])
        self.assertEqual(EquipmentTransfer.objects.get().to_label, "Workshop")

    def test_cannot_transfer_to_a_completed_manifest_or_to_itself(self):
        self.add(self.m1, "A-1")
        JobManifest.objects.filter(pk=self.m2.pk).update(status="Completed")
        with self.assertRaises(TransferError):
            transfer_serials(["A-1"], self.m1, JobManifest.objects.get(pk=self.m2.pk))
        with self.assertRaises(TransferError):
            transfer_serials(["A-1"], self.m1, self.m1)
        self.assertEqual(self.serials(self.m1), ["A-1"])

    def test_only_items_on_the_source_manifest_can_be_moved(self):
        self.add(self.m1, "A-1")
        with self.assertRaises(TransferError):
            transfer_serials(["A-3"], self.m1, self.m2)

    def test_viewer_cannot_transfer(self):
        viewer = User.objects.create_user(username="v", password="x")
        Profile.objects.update_or_create(user=viewer, defaults={"role": Role.VIEWER})
        self.add(self.m1, "A-1")
        self.client.force_login(viewer)
        self.assertEqual(self.post_transfer(self.m1, ["A-1"], self.m2.pk).status_code, 403)
        self.assertEqual(self.serials(self.m1), ["A-1"])

    def test_history_shows_on_both_manifests(self):
        self.add(self.m1, "A-1")
        self.post_transfer(self.m1, ["A-1"], self.m2.pk)
        for m in (self.m1, self.m2):
            self.assertContains(self.client.get(reverse("job_manager:detail", args=[m.pk])), "Transfer history")

    def test_manifest_tab_shows_summary_and_add_tab_stays_after_adding(self):
        response = self.add(self.m1, "A-1", "A-2")
        self.assertIn("tab=add", response["Location"])
        html = self.client.get(reverse("job_manager:detail", args=[self.m1.pk])).content.decode()
        self.assertIn("items on manifest", html)
        self.assertIn("PCE", html)  # classification chip
