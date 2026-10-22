"""Clean up unused files -- run with:  python manage.py test accounts.tests_cleanup"""
import datetime
import os
import shutil
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from departments.models import Asset, AssetCert, CertType, Classification, Department

from . import backup as backup_tools
from . import storage_cleanup
from .models import Profile, Role, TrashBatch


def make_user(username, role):
    user = User.objects.create_user(username=username, password="correct-horse-battery")
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    return User.objects.get(pk=user.pk)


class CleanupBase(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="neos-cleanup-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.settings = override_settings(MEDIA_ROOT=self.media, USE_S3=False)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        dept = Department.objects.create(name="Wireline", location="Site A")
        gears = Classification.objects.create(department=dept, name="Lifting Gears")
        asset = Asset.objects.create(classification=gears, name="91D")
        self.cert = AssetCert.objects.create(asset=asset, cert_type=CertType.objects.create(name="LOLER"),
                                             certificate_file=SimpleUploadedFile("in_use.pdf", b"a certificate in use"))
        self.in_use = self.cert.certificate_file.name
        self.put(self.in_use, b"a certificate in use", age_days=3)                       # in use, and old
        self.put("asset_certs/old_version.pdf", b"replaced long ago", age_days=3)
        self.put("wa_files/leftover.pdf", b"record deleted", age_days=5)
        self.put("wa_files/young.pdf", b"just stored", age_days=0)
        self.put("_import/data.json", b"never touch", age_days=9)
        self.boss = make_user("boss", Role.ADMIN)
        self.client.force_login(self.boss)

    def put(self, name, data, age_days=0):
        path = os.path.join(self.media, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        moment = (datetime.datetime.now() - datetime.timedelta(days=age_days)).timestamp()
        os.utime(path, (moment, moment))

    def exists(self, name):
        return os.path.isfile(os.path.join(self.media, name))

    def trash(self, *names, follow=True):
        return self.client.post(reverse("accounts:cleanup_trash"), {"name": list(names)}, follow=follow)


class CleanupListTests(CleanupBase):
    def test_only_old_unused_files_are_listed(self):
        names = {row["name"] for row in storage_cleanup.unused_files()}
        self.assertEqual(names, {"asset_certs/old_version.pdf", "wa_files/leftover.pdf"})      # not in use, not young, not _import

    def test_the_page_groups_them_with_sizes_and_links_from_the_backup_page(self):
        page = self.client.get(reverse("accounts:cleanup"))
        self.assertContains(page, "wa_files")
        self.assertContains(page, "asset_certs/old_version.pdf")
        self.assertNotContains(page, "young.pdf")
        self.assertNotContains(page, "data.json")
        self.assertContains(self.client.get(reverse("accounts:backup")), reverse("accounts:cleanup"))

    def test_only_admins_can_see_or_use_it(self):
        names = [("accounts:cleanup", []), ("accounts:cleanup_trash", []), ("accounts:cleanup_restore", [1]), ("accounts:cleanup_purge", [1])]
        for role in (Role.VIEWER, Role.EDITOR, Role.APPROVER):
            self.client.force_login(make_user(f"u-{role}", role))
            for name, args in names:
                with self.subTest(role=role, name=name):
                    self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
                    self.assertEqual(self.client.post(reverse(name, args=args), {"name": ["wa_files/leftover.pdf"]}).status_code, 403)
        self.assertTrue(self.exists("wa_files/leftover.pdf"))

    def test_when_nothing_points_to_any_file_nothing_can_be_told_or_moved(self):
        AssetCert.objects.all().delete()
        self.assertIsNone(storage_cleanup.unused_files())
        self.assertContains(self.client.get(reverse("accounts:cleanup")), "cannot tell which files are unused")
        self.assertContains(self.trash("wa_files/leftover.pdf"), "cannot tell")
        self.assertTrue(self.exists("wa_files/leftover.pdf"))
        self.assertEqual(TrashBatch.objects.count(), 0)


class CleanupMoveTests(CleanupBase):
    def test_files_are_moved_to_the_trash_not_deleted(self):
        page = self.trash("asset_certs/old_version.pdf", "wa_files/leftover.pdf")
        self.assertContains(page, "2 files")
        self.assertNotContains(page, "Skipped")
        batch = TrashBatch.objects.get()
        self.assertEqual((batch.count, batch.by), (2, self.boss))
        self.assertEqual(batch.bytes, len(b"replaced long ago") + len(b"record deleted"))
        self.assertFalse(self.exists("wa_files/leftover.pdf"))
        self.assertTrue(self.exists(f"_trash/{batch.pk}/wa_files/leftover.pdf"))
        self.assertEqual(open(os.path.join(self.media, "_trash", str(batch.pk), "wa_files", "leftover.pdf"), "rb").read(), b"record deleted")
        self.assertTrue(self.exists(self.in_use))

    def test_a_forged_request_cannot_move_a_file_that_is_in_use_or_new_or_protected(self):
        page = self.trash(self.in_use, "wa_files/young.pdf", "_import/data.json", "../outside.txt", "/etc/passwd", "missing.pdf")
        self.assertContains(page, "Nothing was moved")
        self.assertEqual(TrashBatch.objects.count(), 0)
        for name in (self.in_use, "wa_files/young.pdf", "_import/data.json"):
            self.assertTrue(self.exists(name), name)

    def test_a_mixed_request_moves_only_what_is_allowed_and_says_what_it_skipped(self):
        page = self.trash("wa_files/leftover.pdf", self.in_use, "wa_files/young.pdf")
        self.assertContains(page, "1 file")
        self.assertContains(page, "1 in use")
        self.assertContains(page, "1 too recent")
        self.assertTrue(self.exists(self.in_use))
        self.assertTrue(self.exists("wa_files/young.pdf"))

    def test_get_is_refused_and_an_empty_selection_is_explained(self):
        self.assertEqual(self.client.get(reverse("accounts:cleanup_trash")).status_code, 403)
        self.assertContains(self.client.post(reverse("accounts:cleanup_trash"), {}, follow=True), "Tick at least one file")

    def test_too_many_files_at_once_are_refused(self):
        with self.assertRaises(storage_cleanup.CleanupError):
            storage_cleanup.move_to_trash([f"wa_files/f{i}.pdf" for i in range(storage_cleanup.MAX_PER_REQUEST + 1)])

    def test_trashed_files_are_gone_from_backups_downloads_and_the_list(self):
        self.trash("wa_files/leftover.pdf")
        manifest = self.client.get(reverse("accounts:backup_sync_manifest")).json()
        names = [f["name"] for f in manifest["files"]]
        self.assertNotIn("wa_files/leftover.pdf", names)
        self.assertFalse([n for n in names if n.startswith("_trash/")])
        batch = TrashBatch.objects.get()
        self.assertEqual(self.client.get(reverse("accounts:backup_sync_file"), {"name": f"_trash/{batch.pk}/wa_files/leftover.pdf"}).status_code, 404)
        self.assertNotIn("_trash", " ".join(r["name"] for r in storage_cleanup.unused_files()))


class CleanupRestoreAndPurgeTests(CleanupBase):
    def setUp(self):
        super().setUp()
        self.trash("wa_files/leftover.pdf", "asset_certs/old_version.pdf")
        self.batch = TrashBatch.objects.get()

    def test_restore_puts_the_files_back(self):
        page = self.client.post(reverse("accounts:cleanup_restore", args=[self.batch.pk]), follow=True)
        self.assertContains(page, "2 files restored")
        self.assertTrue(self.exists("wa_files/leftover.pdf"))
        self.assertFalse(self.exists(f"_trash/{self.batch.pk}/wa_files/leftover.pdf"))
        self.batch.refresh_from_db()
        self.assertIsNotNone(self.batch.restored_at)
        self.assertEqual((self.batch.count, self.batch.bytes), (2, len(b"replaced long ago") + len(b"record deleted")))
        self.assertNotContains(self.client.get(reverse("accounts:cleanup")), "Skipped: 0")

    def test_restore_never_overwrites_a_file_that_exists_again(self):
        self.put("wa_files/leftover.pdf", b"a newer upload with the same name")
        page = self.client.post(reverse("accounts:cleanup_restore", args=[self.batch.pk]), follow=True)
        self.assertContains(page, "1 restored")
        self.assertEqual(open(os.path.join(self.media, "wa_files", "leftover.pdf"), "rb").read(), b"a newer upload with the same name")
        self.assertTrue(self.exists(f"_trash/{self.batch.pk}/wa_files/leftover.pdf"))              # still safe in the trash
        self.batch.refresh_from_db()
        self.assertEqual((len(self.batch.items), self.batch.count), (1, 2))                       # the record keeps its original total
        self.assertContains(self.client.get(reverse("accounts:cleanup")), "1 of 2 still in the trash")

    def test_nothing_can_be_deleted_for_good_before_the_waiting_period_is_over(self):
        page = self.client.post(reverse("accounts:cleanup_purge", args=[self.batch.pk]), follow=True)
        self.assertContains(page, "kept until")
        self.assertTrue(self.exists(f"_trash/{self.batch.pk}/wa_files/leftover.pdf"))
        self.batch.refresh_from_db()
        self.assertIsNone(self.batch.purged_at)

    def test_after_the_waiting_period_the_files_can_be_deleted_for_good(self):
        TrashBatch.objects.filter(pk=self.batch.pk).update(created_at=timezone.now() - datetime.timedelta(days=8))
        self.assertTrue(self.client.get(reverse("accounts:cleanup")).context["trash"][0].can_purge)
        page = self.client.post(reverse("accounts:cleanup_purge", args=[self.batch.pk]), follow=True)
        self.assertContains(page, "2 files deleted for good")
        self.assertFalse(self.exists(f"_trash/{self.batch.pk}/wa_files/leftover.pdf"))
        self.batch.refresh_from_db()
        self.assertIsNotNone(self.batch.purged_at)
        self.assertContains(self.client.post(reverse("accounts:cleanup_purge", args=[self.batch.pk]), follow=True), "Nothing left")
        self.assertContains(self.client.post(reverse("accounts:cleanup_restore", args=[self.batch.pk]), follow=True), "cannot be restored")


class _FakeBucket:
    """Just enough of an S3/R2 client for the clean-up."""

    def __init__(self, objects):
        self.objects = dict(objects)

    def get_paginator(self, name):
        outer = self

        class Paginator:
            def paginate(self, Bucket):
                yield {"Contents": [{"Key": k, "Size": len(v[0]), "LastModified": v[1]} for k, v in outer.objects.items()]}
        return Paginator()

    def head_object(self, Bucket, Key):
        from botocore.exceptions import ClientError
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {"ContentLength": len(self.objects[Key][0])}

    def copy_object(self, Bucket, Key, CopySource):
        self.objects[Key] = self.objects[CopySource["Key"]]

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)


class CleanupBucketTests(CleanupBase):
    def test_moves_restores_and_purges_work_on_a_bucket(self):
        old = timezone.now() - datetime.timedelta(days=4)
        bucket = _FakeBucket({self.in_use: (b"in use", old), "wa_files/leftover.pdf": (b"leftover", old), "wa_files/young.pdf": (b"new", timezone.now())})
        with override_settings(USE_S3=True, AWS_STORAGE_BUCKET_NAME="b"), mock.patch("accounts.backup._s3_client", return_value=bucket):
            self.assertEqual([r["name"] for r in storage_cleanup.unused_files()], ["wa_files/leftover.pdf"])
            batch, skipped = storage_cleanup.move_to_trash(["wa_files/leftover.pdf", self.in_use])
            self.assertEqual((batch.count, skipped["in use"]), (1, 1))
            self.assertNotIn("wa_files/leftover.pdf", bucket.objects)
            self.assertEqual(bucket.objects[f"_trash/{batch.pk}/wa_files/leftover.pdf"][0], b"leftover")
            self.assertIn(self.in_use, bucket.objects)
            storage_cleanup.restore(batch)
            self.assertIn("wa_files/leftover.pdf", bucket.objects)
            batch2, _ = storage_cleanup.move_to_trash(["wa_files/leftover.pdf"])
            with self.assertRaises(storage_cleanup.CleanupError):
                storage_cleanup.purge(batch2)
            storage_cleanup.purge(batch2, now=timezone.now() + datetime.timedelta(days=8))
            self.assertFalse([k for k in bucket.objects if k.startswith("_trash/")])
            self.assertIn(self.in_use, bucket.objects)
