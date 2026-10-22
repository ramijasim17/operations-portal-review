"""Tests for the shared Client/Field/Location lists, popup-form redirects and
the Certificates column chooser."""
import glob

from django.contrib.auth.models import User
from django.template.loader import get_template
from django.test import TestCase
from django.urls import reverse

from departments.models import (
    Asset, CertType, Classification, ClassificationCertRequirement, Department, FieldDefinition,
)
from job_manager.forms import JobManifestForm
from job_manager.models import JobManifest
from sgs_report.models import SGSReport
from wire_management.models import Client, FieldWell, Location

from .models import Profile, Role


def login(client, role):
    user = User.objects.create_user(username=f"u_{role}", password="x")
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    client.force_login(user)
    return user


class SharedListTests(TestCase):
    def test_add_rename_cascades_and_delete(self):
        login(self.client, Role.ADMIN)
        self.client.post(reverse("wire_management:lists"), {"kind": "client", "name": "  Acme   Oil "})
        acme = Client.objects.get(name="Acme Oil")
        job = JobManifest.objects.create(job_name="J1", client="Acme Oil", location="Site A")
        SGSReport.objects.create(well_name="W-1", client="Acme Oil", field="F1")

        self.client.post(reverse("wire_management:list_edit", args=["client", acme.pk]), {"name": "Acme Energy"})
        job.refresh_from_db()
        self.assertEqual(job.client, "Acme Energy")                       # renamed everywhere
        self.assertEqual(SGSReport.objects.get().client, "Acme Energy")

        self.client.post(reverse("wire_management:list_delete", args=["client", acme.pk]))
        self.assertFalse(Client.objects.filter(pk=acme.pk).exists())

    def test_duplicate_names_are_refused_case_insensitively(self):
        login(self.client, Role.EDITOR)
        FieldWell.objects.create(name="Main Field")
        self.client.post(reverse("wire_management:lists"), {"kind": "field", "name": "main field"})
        self.assertEqual(FieldWell.objects.count(), 1)

    def test_viewer_can_see_but_not_change_lists(self):
        login(self.client, Role.VIEWER)
        self.assertEqual(self.client.get(reverse("wire_management:lists")).status_code, 200)
        self.client.post(reverse("wire_management:lists"), {"kind": "client", "name": "Nope"})
        self.assertFalse(Client.objects.filter(name="Nope").exists())

    def test_only_admin_can_delete_list_entries(self):
        obj = Location.objects.create(name="Site A")
        login(self.client, Role.EDITOR)
        self.assertEqual(self.client.post(reverse("wire_management:list_delete", args=["location", obj.pk])).status_code, 403)

    def test_forms_offer_the_shared_lists_and_keep_old_values(self):
        Client.objects.create(name="Client A")
        Location.objects.create(name="Main Field")
        form = JobManifestForm()
        self.assertEqual([c[0] for c in form.fields["client"].choices], ["", "Client A"])
        old = JobManifest.objects.create(job_name="Old", client="Legacy Client", location="Main Field")
        edit = JobManifestForm(instance=old)
        self.assertIn("Legacy Client", [c[0] for c in edit.fields["client"].choices])  # not blanked

    def test_job_form_saves_the_chosen_name(self):
        Client.objects.create(name="Client A")
        form = JobManifestForm({"job_name": "J", "client": "Client A", "status": "Draft"})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().client, "Client A")
        self.assertFalse(JobManifestForm({"job_name": "J", "client": "Made Up", "status": "Draft"}).is_valid())


class PopupAndTemplateTests(TestCase):
    def test_popup_form_redirect_comes_back_as_json_and_keeps_the_message(self):
        login(self.client, Role.EDITOR)
        response = self.client.post(reverse("wire_management:lists"), {"kind": "client", "name": "Pop"},
                                    HTTP_X_MODAL="1")
        self.assertEqual(response.status_code, 200)
        self.assertIn("redirect", response.json())
        # The success message must still be waiting for the page the browser really loads.
        page = self.client.get(response.json()["redirect"])
        self.assertContains(page, "added to Clients")

    def test_every_template_still_compiles(self):
        for path in glob.glob("**/templates/**/*.html", recursive=True):
            if path.startswith("venv"):
                continue
            get_template(path.split("/templates/", 1)[1])

    def test_no_browser_confirm_boxes_remain(self):
        for path in glob.glob("**/templates/**/*.html", recursive=True):
            if path.startswith("venv"):
                continue
            with open(path) as fh:
                self.assertNotIn("return confirm(", fh.read(), path)


class CertificateColumnTests(TestCase):
    def setUp(self):
        login(self.client, Role.VIEWER)
        dept = Department.objects.create(name="D", location="L")
        cls = Classification.objects.create(department=dept, name="PCE")
        self.f1 = FieldDefinition.objects.create(classification=cls, label="Working pressure", field_type="text")
        self.f2 = FieldDefinition.objects.create(classification=cls, label="Manufacturer", field_type="text")
        ClassificationCertRequirement.objects.create(classification=cls, cert_type=CertType.objects.create(name="Pressure Test"))
        Asset.objects.create(classification=cls, name="A-1", description="Test asset")
        self.url = reverse("departments:detail", args=[dept.pk]) + "?tab=certificates"

    def test_asset_fields_are_hidden_by_default(self):
        html = self.client.get(self.url).content.decode()
        self.assertIn("A-1", html)
        self.assertNotIn("<th>Working pressure</th>", html)

    def test_picked_columns_appear(self):
        html = self.client.get(self.url + f"&cols={self.f2.pk}").content.decode()
        self.assertIn("<th>Manufacturer</th>", html)
        self.assertNotIn("<th>Working pressure</th>", html)
