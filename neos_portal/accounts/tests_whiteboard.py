import io
import json

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from pypdf import PdfReader

from accounts.models import Profile, Role
from whiteboard.models import Board


def png(color="white"):
    buf = io.BytesIO()
    Image.new("RGB", (400, 566), color).save(buf, format="PNG")
    return SimpleUploadedFile("p.png", buf.getvalue(), content_type="image/png")


class WhiteboardTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_superuser("rami", "r@x.com", "x")
        Profile.objects.update_or_create(user=self.owner, defaults={"role": Role.ADMIN})
        self.other = User.objects.create_user("staff", password="x")
        Profile.objects.update_or_create(user=self.other, defaults={"role": Role.ADMIN})
        self.board = Board.objects.create(owner=self.owner, title="Weekly")
        self.client.force_login(self.owner)

    def test_owner_sees_it_in_the_menu_and_can_open_it(self):
        self.assertContains(self.client.get(reverse("core:dashboard")), "Whiteboard")
        self.assertEqual(self.client.get(reverse("whiteboard:list")).status_code, 200)
        self.assertContains(self.client.get(reverse("whiteboard:edit", args=[self.board.pk])), "wb-app")

    def test_other_staff_never_see_it_even_admins(self):
        self.client.force_login(self.other)
        self.assertNotContains(self.client.get(reverse("core:dashboard")), "My Whiteboard")
        for name, args in (("list", []), ("edit", [self.board.pk]), ("save", [self.board.pk]), ("export_pdf", [self.board.pk])):
            self.assertEqual(self.client.get(reverse(f"whiteboard:{name}", args=args)).status_code, 404)

    @override_settings(WHITEBOARD_USERNAMES="staff")
    def test_username_setting_decides_who(self):
        self.assertEqual(self.client.get(reverse("whiteboard:list")).status_code, 404)    # superuser no longer listed
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse("whiteboard:list")).status_code, 200)

    def test_save_keeps_only_valid_content(self):
        data = {"bg": "grid", "pages": [{"items": [{"t": "text", "s": "hi"}, "junk"]}, {"items": []}]}
        r = self.client.post(reverse("whiteboard:save", args=[self.board.pk]),
                             json.dumps({"title": "New name", "data": data}), content_type="application/json")
        self.assertTrue(r.json()["ok"])
        self.board.refresh_from_db()
        self.assertEqual((self.board.title, self.board.data["bg"], self.board.page_count), ("New name", "grid", 2))
        self.assertEqual(self.board.data["pages"][0]["items"], [{"t": "text", "s": "hi"}])
        bad = self.client.post(reverse("whiteboard:save", args=[self.board.pk]), "nope", content_type="application/json")
        self.assertEqual(bad.status_code, 400)

    def test_export_makes_one_a4_page_per_image(self):
        r = self.client.post(reverse("whiteboard:export_pdf", args=[self.board.pk]), {"pages": [png(), png("#eeeeee")]})
        self.assertEqual(r["Content-Type"], "application/pdf")
        reader = PdfReader(io.BytesIO(r.content))
        self.assertEqual(len(reader.pages), 2)
        w, h = (float(reader.pages[0].mediabox.width), float(reader.pages[0].mediabox.height))
        self.assertAlmostEqual(w, 595, delta=2)
        self.assertAlmostEqual(h, 842, delta=2)
        self.assertEqual(self.client.post(reverse("whiteboard:export_pdf", args=[self.board.pk])).status_code, 400)

    def test_create_and_delete(self):
        r = self.client.post(reverse("whiteboard:list"), {"title": "Notes"})
        board = Board.objects.get(title="Notes")
        self.assertRedirects(r, reverse("whiteboard:edit", args=[board.pk]))
        self.client.post(reverse("whiteboard:delete", args=[board.pk]))
        self.assertFalse(Board.objects.filter(pk=board.pk).exists())
