"""The Backup page -- run with:  python manage.py test accounts.tests_backup"""
import datetime
import io
import json
import os
import shutil
import tempfile
import zipfile
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from personnel.models import Personnel

from . import backup as backup_tools
from .models import BackupLog, Profile, Role


def make_user(username, role):
    user = User.objects.create_user(username=username, password="correct-horse-battery")
    Profile.objects.update_or_create(user=user, defaults={"role": role})
    return User.objects.get(pk=user.pk)


def read_zip(response):
    return zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content)))


class _FakeBody:
    def __init__(self, data):
        self.data = data

    def iter_chunks(self, size):
        yield self.data


class _FakeS3:
    objects = {"asset_certs/91D.pdf": b"%PDF certificate", "_import/data.json": b"secret staff data", "sgs_reports/log.txt": b"gauge log"}

    def get_paginator(self, name):
        objects = self.objects

        class Paginator:
            def paginate(self, Bucket):
                yield {"Contents": [{"Key": key, "Size": len(data), "LastModified": datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc)}
                                         for key, data in objects.items()]}
        return Paginator()

    def get_object(self, Bucket, Key):
        return {"Body": _FakeBody(self.objects[Key])}


class BackupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.media = tempfile.mkdtemp(prefix="abc-backup-media-")
        os.makedirs(os.path.join(cls.media, "asset_certs"))
        with open(os.path.join(cls.media, "asset_certs", "EXP_91D.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4 a certificate")
        cls.boss = make_user("boss", Role.ADMIN)
        Personnel.objects.create(employee_id="9004", name="Ali Naji", status="Active")

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media, ignore_errors=True)

    def setUp(self):
        self.settings = override_settings(MEDIA_ROOT=self.media, USE_S3=False)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.client.force_login(self.boss)

    def test_only_admins_can_open_the_page_or_download(self):
        for role in (Role.VIEWER, Role.EDITOR, Role.APPROVER):
            with self.subTest(role=role):
                self.client.force_login(make_user(f"u-{role}", role))
                self.assertEqual(self.client.get(reverse("accounts:backup")).status_code, 403)
                self.assertEqual(self.client.get(reverse("accounts:backup_download")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("accounts:backup_download")).status_code, 302)

    def test_the_page_describes_the_backup_and_is_in_the_admin_menu(self):
        page = self.client.get(reverse("accounts:backup"))
        self.assertContains(page, "Download full backup")
        self.assertContains(page, "external hard disk")
        self.assertContains(page, 'href="/accounts/backup/"')

    def test_other_roles_do_not_see_it_in_the_menu(self):
        self.client.force_login(make_user("ed", Role.EDITOR))
        self.assertNotContains(self.client.get(reverse("core:dashboard")), 'href="/accounts/backup/"')

    def test_the_full_backup_is_one_valid_zip_with_database_files_and_code(self):
        response = self.client.get(reverse("accounts:backup_download") + "?part=all")
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("abc-backup-full-", response["Content-Disposition"])
        archive = read_zip(response)
        self.assertIsNone(archive.testzip())
        names = archive.namelist()
        self.assertIn("README.txt", names)
        self.assertIn("database.json", names)
        self.assertIn("files/asset_certs/EXP_91D.pdf", names)
        self.assertIn("code/neos_portal/manage.py", names)
        self.assertEqual(archive.read("files/asset_certs/EXP_91D.pdf"), b"%PDF-1.4 a certificate")
        records = json.loads(archive.read("database.json"))
        self.assertTrue(any(r["model"] == "personnel.personnel" and r["fields"]["name"] == "Ali Naji" for r in records))

    def test_the_code_part_never_contains_secrets_or_data(self):
        names = read_zip(self.client.get(reverse("accounts:backup_download") + "?part=code")).namelist()
        self.assertNotIn("code/neos_portal/.env", names)
        for name in names:
            self.assertFalse(name.endswith((".sqlite3", ".pyc")), name)
            self.assertNotIn("/venv/", name)
            self.assertNotIn("/export_for_render/", name)
            self.assertNotIn("/tester_copy/", name)
            self.assertNotIn("/media/", name)

    def test_one_part_at_a_time(self):
        names = read_zip(self.client.get(reverse("accounts:backup_download") + "?part=database")).namelist()
        self.assertEqual(sorted(names), ["README.txt", "database.json", "manifest.json"])
        names = read_zip(self.client.get(reverse("accounts:backup_download") + "?part=files")).namelist()
        self.assertEqual(sorted(names), ["README.txt", "files/asset_certs/EXP_91D.pdf", "manifest.json"])

    def test_an_unknown_part_is_refused(self):
        self.assertEqual(self.client.get(reverse("accounts:backup_download") + "?part=everything-else").status_code, 403)

    def test_files_come_from_the_bucket_when_the_site_uses_one(self):
        with override_settings(USE_S3=True, AWS_STORAGE_BUCKET_NAME="b"), mock.patch("accounts.backup._s3_client", return_value=_FakeS3()):
            archive = read_zip(self.client.get(reverse("accounts:backup_download") + "?part=files"))
        names = archive.namelist()
        self.assertIn("files/asset_certs/91D.pdf", names)
        self.assertIn("files/sgs_reports/log.txt", names)
        self.assertEqual(archive.read("files/asset_certs/91D.pdf"), b"%PDF certificate")
        self.assertFalse([n for n in names if "_import" in n])        # the one-off import file is never part of a backup


class ChangesOnlyTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="abc-backup-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.old_file = self.put("asset_certs/old.pdf", b"old certificate", age_days=10)
        self.settings = override_settings(MEDIA_ROOT=self.media, USE_S3=False)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.boss = make_user("boss", Role.ADMIN)
        self.client.force_login(self.boss)

    def put(self, name, data, age_days=0):
        path = os.path.join(self.media, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        moment = (datetime.datetime.now() - datetime.timedelta(days=age_days)).timestamp()
        os.utime(path, (moment, moment))
        return path

    def download(self, query):
        return self.client.get(reverse("accounts:backup_download") + query)

    def test_a_finished_full_backup_is_recorded_and_becomes_the_baseline(self):
        zipfile_ = read_zip(self.download("?mode=full"))
        self.assertIn("files/asset_certs/old.pdf", zipfile_.namelist())
        log = BackupLog.objects.get()
        self.assertEqual((log.kind, log.by, log.files_in_storage, log.files_included), ("full", self.boss, 1, 1))
        self.assertTrue(log.code_included)
        self.assertEqual(len(log.code_fingerprint), 64)

    def test_a_single_part_or_a_cancelled_download_is_not_recorded(self):
        read_zip(self.download("?part=database"))
        self.assertEqual(BackupLog.objects.count(), 0)
        pieces = backup_tools.stream(backup_tools.PARTS, user=self.boss)
        next(pieces)
        pieces.close()                                   # the browser gave up half way
        self.assertEqual(BackupLog.objects.count(), 0)

    def test_changes_only_needs_an_earlier_backup(self):
        response = self.download("?mode=changes")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("accounts:backup"))

    def test_changes_only_contains_just_the_new_and_changed_files(self):
        read_zip(self.download("?mode=full"))
        self.put("asset_certs/new.pdf", b"a new certificate")
        self.put("sgs_reports/log.txt", b"a new log")
        archive = read_zip(self.download("?mode=changes"))
        names = archive.namelist()
        self.assertIn("files/asset_certs/new.pdf", names)
        self.assertIn("files/sgs_reports/log.txt", names)
        self.assertNotIn("files/asset_certs/old.pdf", names)              # unchanged: left out
        self.assertIn("database.json", names)                             # always complete
        self.assertNotIn("code/neos_portal/manage.py", names)             # code unchanged since the full backup
        manifest = json.loads(archive.read("manifest.json"))
        self.assertEqual(manifest["kind"], "changes")
        self.assertEqual(sorted(f["name"] for f in manifest["files_in_storage"]),
                         ["asset_certs/new.pdf", "asset_certs/old.pdf", "sgs_reports/log.txt"])
        self.assertEqual(manifest["files_included"], 2)
        self.assertEqual(BackupLog.objects.first().kind, "changes")
        self.assertIn("abc-backup-changes-", self.download("?mode=changes")["Content-Disposition"])

    def test_a_changed_file_is_picked_up_and_a_deleted_one_disappears_from_the_manifest(self):
        read_zip(self.download("?mode=full"))
        self.put("asset_certs/old.pdf", b"old certificate, re-issued")        # same name, new content, new time
        archive = read_zip(self.download("?mode=changes"))
        self.assertEqual(archive.read("files/asset_certs/old.pdf"), b"old certificate, re-issued")
        os.remove(self.old_file)
        manifest = json.loads(read_zip(self.download("?mode=changes")).read("manifest.json"))
        self.assertEqual(manifest["files_in_storage"], [])

    def test_the_code_is_included_again_when_it_changed(self):
        read_zip(self.download("?mode=full"))
        BackupLog.objects.update(code_fingerprint="0" * 64)               # as if the code had been different then
        names = read_zip(self.download("?mode=changes")).namelist()
        self.assertIn("code/neos_portal/manage.py", names)

    def test_the_page_shows_what_a_changes_only_backup_would_hold_and_the_history(self):
        read_zip(self.download("?mode=full"))
        self.put("asset_certs/new.pdf", b"a new certificate")
        page = self.client.get(reverse("accounts:backup"))
        self.assertContains(page, "<b>1</b> new or changed file")
        self.assertContains(page, "Download changes only")
        self.assertContains(page, "Recent backups")
        self.assertContains(page, "unchanged, so it is left out")

    def test_before_any_backup_the_page_asks_for_a_full_one_first(self):
        page = self.client.get(reverse("accounts:backup"))
        self.assertNotContains(page, "Download changes only")
        self.assertContains(page, "full backup</b> first")


class VerifyBackupTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="neos-verify-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        os.makedirs(os.path.join(self.media, "asset_certs"))
        with open(os.path.join(self.media, "asset_certs", "a.pdf"), "wb") as handle:
            handle.write(b"%PDF-1.4 " + os.urandom(5000))
        self.settings = override_settings(MEDIA_ROOT=self.media, USE_S3=False)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.path = os.path.join(self.media, "backup.zip")
        with open(self.path, "wb") as out:
            for piece in backup_tools.stream(backup_tools.PARTS):
                out.write(piece)

    def run_check(self):
        from django.core.management import call_command
        out = io.StringIO()
        call_command("verify_backup", self.path, stdout=out)
        return out.getvalue()

    def test_a_good_backup_passes(self):
        self.assertIn("OK:", self.run_check())

    def test_damage_inside_a_file_is_caught(self):
        from django.core.management.base import CommandError
        blob = bytearray(open(self.path, "rb").read())
        at = blob.find(b"%PDF-1.4 ") + 2000                  # flip some bytes in the middle of the stored certificate
        blob[at:at + 8] = b"\x00\xff\x00\xff\x00\xff\x00\xff"
        open(self.path, "wb").write(bytes(blob))
        with self.assertRaisesMessage(CommandError, "DAMAGED"):
            self.run_check()

    def test_a_truncated_download_is_caught(self):
        from django.core.management.base import CommandError
        data = open(self.path, "rb").read()
        open(self.path, "wb").write(data[: len(data) // 2])
        with self.assertRaises(CommandError):
            self.run_check()


class MergeBackupTests(TestCase):
    """Old full backup + changes-only backup(s) == a fresh full backup."""

    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="neos-merge-media-")
        self.out = tempfile.mkdtemp(prefix="neos-merge-out-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)
        self.settings = override_settings(MEDIA_ROOT=self.media, USE_S3=False)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.boss = make_user("boss", Role.ADMIN)
        self.client.force_login(self.boss)
        self.put("asset_certs/keep.pdf", b"unchanged", age_days=10)
        self.put("asset_certs/edit.pdf", b"first version", age_days=10)
        self.put("asset_certs/gone.pdf", b"will be deleted", age_days=10)

    def put(self, name, data, age_days=0):
        path = os.path.join(self.media, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        moment = (datetime.datetime.now() - datetime.timedelta(days=age_days)).timestamp()
        os.utime(path, (moment, moment))

    def save(self, query, name):
        path = os.path.join(self.out, name)
        with open(path, "wb") as handle:
            for piece in self.client.get(reverse("accounts:backup_download") + query).streaming_content:
                handle.write(piece)
        return path

    def merge(self, *paths, output="merged.zip"):
        from django.core.management import call_command
        target = os.path.join(self.out, output)
        call_command("merge_backup", *paths, output=target, stdout=io.StringIO())
        return target

    def test_the_merged_backup_equals_a_fresh_full_backup(self):
        full = self.save("?mode=full", "full.zip")
        base = BackupLog.objects.first()
        self.put("asset_certs/edit.pdf", b"second version")                  # changed
        self.put("sgs_reports/new.txt", b"brand new")                        # added
        os.remove(os.path.join(self.media, "asset_certs", "gone.pdf"))       # deleted
        header = self.client.get(reverse("accounts:backup_download") + f"?mode=changes&base={base.pk}")["Content-Disposition"]
        self.assertIn(f"-on-{base.label}", header)                           # the file name says what it sits on top of
        BackupLog.objects.exclude(pk=base.pk).delete()                       # that request only checked the name
        changes = self.save(f"?mode=changes&base={base.pk}", "changes.zip")
        merged = self.merge(full, changes)
        fresh = self.save("?mode=full", "fresh.zip")

        def files(path):
            with zipfile.ZipFile(path) as archive:
                return {n: archive.read(n) for n in archive.namelist() if n.startswith(("files/", "database.json"))}
        self.assertEqual({k: v for k, v in files(merged).items() if k != "database.json"},
                         {k: v for k, v in files(fresh).items() if k != "database.json"})
        with zipfile.ZipFile(merged) as archive:
            self.assertEqual(archive.read("files/asset_certs/edit.pdf"), b"second version")
            self.assertNotIn("files/asset_certs/gone.pdf", archive.namelist())
            self.assertIn("code/neos_portal/manage.py", archive.namelist())       # code carried over from the full backup
            self.assertEqual(json.loads(archive.read("manifest.json"))["kind"], "full")
            self.assertIsNone(archive.testzip())

    def test_two_changes_files_in_order_work_and_skipping_one_is_refused(self):
        from django.core.management.base import CommandError
        full = self.save("?mode=full", "full.zip")
        first = BackupLog.objects.first()
        self.put("asset_certs/edit.pdf", b"second version")
        c1 = self.save(f"?mode=changes&base={first.pk}", "c1.zip")
        second = BackupLog.objects.first()
        self.put("asset_certs/edit.pdf", b"third version")
        c2 = self.save(f"?mode=changes&base={second.pk}", "c2.zip")
        merged = self.merge(full, c1, c2)
        with zipfile.ZipFile(merged) as archive:
            self.assertEqual(archive.read("files/asset_certs/edit.pdf"), b"third version")
        with self.assertRaisesMessage(CommandError, "Add the missing changes file"):
            self.merge(full, c2, output="skipped.zip")

    def test_replace_overwrites_the_old_backup_only_after_it_checks_out(self):
        from django.core.management import call_command
        full = self.save("?mode=full", "full.zip")
        base = BackupLog.objects.first()
        self.put("asset_certs/edit.pdf", b"second version")
        changes = self.save(f"?mode=changes&base={base.pk}", "changes.zip")
        call_command("merge_backup", full, changes, replace=True, stdout=io.StringIO())
        with zipfile.ZipFile(full) as archive:
            self.assertEqual(archive.read("files/asset_certs/edit.pdf"), b"second version")
        self.assertFalse(os.path.exists(full + ".merging"))

    def test_you_can_pick_an_older_backup_as_the_starting_point(self):
        self.save("?mode=full", "full.zip")
        first = BackupLog.objects.first()
        self.put("asset_certs/edit.pdf", b"second version")
        self.save(f"?mode=changes&base={first.pk}", "c1.zip")
        later = self.save(f"?mode=changes&base={first.pk}", "c2.zip")          # measured from the FIRST backup again
        with zipfile.ZipFile(later) as archive:
            self.assertIn("files/asset_certs/edit.pdf", archive.namelist())
            self.assertEqual(json.loads(archive.read("manifest.json"))["based_on"], first.label)

    def test_an_unknown_starting_point_is_refused_and_the_preview_follows_the_pick(self):
        self.save("?mode=full", "full.zip")
        self.assertEqual(self.client.get(reverse("accounts:backup_download") + "?mode=changes&base=99999")["Location"], reverse("accounts:backup"))
        first = BackupLog.objects.first()
        self.put("asset_certs/new.pdf", b"new")
        data = self.client.get(reverse("accounts:backup_preview") + f"?base={first.pk}").json()
        self.assertEqual(data["files_changed"], 1)
        self.assertEqual(self.client.get(reverse("accounts:backup_preview") + "?base=99999").status_code, 404)
        self.client.force_login(make_user("ed", Role.EDITOR))
        self.assertEqual(self.client.get(reverse("accounts:backup_preview") + f"?base={first.pk}").status_code, 403)


class FolderUpdateServerTests(TestCase):
    """The endpoints the browser uses to update a backup folder."""

    def setUp(self):
        self.media = tempfile.mkdtemp(prefix="neos-sync-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        os.makedirs(os.path.join(self.media, "asset_certs"))
        with open(os.path.join(self.media, "asset_certs", "91D.pdf"), "wb") as handle:
            handle.write(b"%PDF certificate")
        with open(os.path.join(os.path.dirname(self.media), "outside-secret.txt"), "w") as handle:
            handle.write("not part of the media folder")
        self.addCleanup(lambda: os.path.exists(os.path.join(os.path.dirname(self.media), "outside-secret.txt")) and os.remove(os.path.join(os.path.dirname(self.media), "outside-secret.txt")))
        self.settings = override_settings(MEDIA_ROOT=self.media, USE_S3=False)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.boss = make_user("boss", Role.ADMIN)
        self.client.force_login(self.boss)

    def test_only_admins_can_use_any_of_the_endpoints(self):
        names = ("backup_sync_manifest", "backup_sync_file", "backup_sync_database", "backup_sync_code", "backup_sync_done")
        for role in (Role.VIEWER, Role.EDITOR, Role.APPROVER):
            self.client.force_login(make_user(f"u-{role}", role))
            for name in names:
                with self.subTest(role=role, name=name):
                    self.assertEqual(self.client.get(reverse(f"accounts:{name}") + "?name=asset_certs/91D.pdf").status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("accounts:backup_sync_manifest")).status_code, 302)

    def test_the_manifest_lists_every_file_with_a_signature_and_the_code_fingerprint(self):
        data = self.client.get(reverse("accounts:backup_sync_manifest")).json()
        self.assertEqual([f["name"] for f in data["files"]], ["asset_certs/91D.pdf"])
        self.assertEqual(data["files"][0]["size"], len(b"%PDF certificate"))
        self.assertTrue(data["files"][0]["sig"].startswith(f"{len(b'%PDF certificate')}:"))
        self.assertEqual(len(data["code_fingerprint"]), 64)
        self.assertRegex(data["stamp"], r"^\d{8}-\d{4}$")

    def test_a_file_comes_back_whole_with_its_length(self):
        response = self.client.get(reverse("accounts:backup_sync_file") + "?name=asset_certs/91D.pdf")
        self.assertEqual(b"".join(response.streaming_content), b"%PDF certificate")
        self.assertEqual(response["Content-Length"], str(len(b"%PDF certificate")))

    def test_names_that_leave_the_media_folder_or_do_not_exist_are_refused(self):
        for name in ("../outside-secret.txt", "asset_certs/../../outside-secret.txt", "/etc/passwd", "_import/data.json",
                     "asset_certs\\..\\x", "missing.pdf", "", "asset_certs"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse("accounts:backup_sync_file"), {"name": name}).status_code, 404)

    def test_the_database_and_the_code_can_be_fetched(self):
        database = self.client.get(reverse("accounts:backup_sync_database"))
        self.assertEqual(database["Content-Type"], "application/json")
        self.assertTrue(any(r["model"] == "auth.user" for r in json.loads(database.content)))
        archive = read_zip(self.client.get(reverse("accounts:backup_sync_code")))
        self.assertIn("code/neos_portal/manage.py", archive.namelist())
        self.assertEqual(BackupLog.objects.count(), 0)                     # fetching parts is not a recorded backup

    def test_a_finished_update_is_recorded_and_shown_but_is_not_a_starting_point_for_zip_changes(self):
        report = {"total": 40, "downloaded": 3, "downloaded_bytes": 5000, "removed": 1, "code_updated": True, "code_fingerprint": "ab" * 32}
        response = self.client.post(reverse("accounts:backup_sync_done"), json.dumps(report), content_type="application/json")
        self.assertEqual(response.status_code, 200)
        log = BackupLog.objects.get()
        self.assertEqual((log.kind, log.files_included, log.files_in_storage, log.by), ("sync", 3, 40, self.boss))
        page = self.client.get(reverse("accounts:backup"))
        self.assertContains(page, "Last update:")
        self.assertContains(page, "Update my backup folder")
        self.assertNotContains(page, "Download changes only")              # no zip backup yet to measure from
        self.assertEqual(self.client.get(reverse("accounts:backup_download") + "?mode=changes")["Location"], reverse("accounts:backup"))

    def test_a_bad_report_is_refused_and_get_is_not_allowed(self):
        self.assertEqual(self.client.post(reverse("accounts:backup_sync_done"), "not json", content_type="application/json").status_code, 400)
        self.assertEqual(self.client.get(reverse("accounts:backup_sync_done")).status_code, 405)

    def test_the_page_loads_the_browser_script(self):
        page = self.client.get(reverse("accounts:backup"))
        self.assertContains(page, "core/js/backup_sync.js")
        self.assertContains(page, "Advanced: zip backups")

    def test_a_missing_bucket_file_is_a_404_not_a_crash(self):
        from botocore.exceptions import ClientError

        class Missing:
            def head_object(self, **kw):
                raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        with override_settings(USE_S3=True, AWS_STORAGE_BUCKET_NAME="b"), mock.patch("accounts.backup._s3_client", return_value=Missing()):
            self.assertEqual(self.client.get(reverse("accounts:backup_sync_file"), {"name": "nope.pdf"}).status_code, 404)


class BrowserEngineTests(TestCase):
    """Runs the Node tests of backup_sync.js (an in-memory folder), when Node is installed."""

    def test_the_browser_engine_passes_its_tests(self):
        import shutil as _shutil
        import subprocess
        from pathlib import Path
        node = _shutil.which("node")
        if not node:
            self.skipTest("Node.js is not installed")
        for name in ("backup_sync.test.mjs", "theme.test.mjs"):
            test_file = Path(__file__).parent / "js_tests" / name
            result = subprocess.run([node, "--test", str(test_file)], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, name + "\n" + result.stdout[-1500:] + result.stderr[-500:])


class OldVersionsTests(TestCase):
    """The portal never deletes a file when a record is deleted or its file replaced; the manifest says which are still in use."""

    def setUp(self):
        from departments.models import Asset, AssetCert, CertType, Classification, Department
        self.media = tempfile.mkdtemp(prefix="neos-old-media-")
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        self.settings = override_settings(MEDIA_ROOT=self.media, USE_S3=False)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        dept = Department.objects.create(name="Wireline", location="Site A")
        gears = Classification.objects.create(department=dept, name="Lifting Gears")
        self.cert_type = CertType.objects.create(name="LOLER")
        self.asset = Asset.objects.create(classification=gears, name="91D")
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.cert = AssetCert.objects.create(asset=self.asset, cert_type=self.cert_type,
                                             certificate_file=SimpleUploadedFile("C3125A.pdf", b"first certificate"))
        self.first_name = self.cert.certificate_file.name
        self.boss = make_user("boss", Role.ADMIN)
        self.client.force_login(self.boss)

    def manifest(self):
        return {f["name"]: f for f in self.client.get(reverse("accounts:backup_sync_manifest")).json()["files"]}

    def test_a_replaced_certificate_file_is_reported_as_an_old_version(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.assertTrue(self.manifest()[self.first_name]["current"])
        self.cert.certificate_file = SimpleUploadedFile("C3125A.pdf", b"second certificate")        # same name: stored under a new one
        self.cert.save()
        files = self.manifest()
        self.assertNotEqual(self.cert.certificate_file.name, self.first_name)
        self.assertTrue(files[self.cert.certificate_file.name]["current"])
        self.assertFalse(files[self.first_name]["current"])                                           # still in storage, no longer used

    def test_a_file_whose_record_was_deleted_is_an_old_version_and_the_totals_are_given(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        from departments.models import Asset, AssetCert
        other = Asset.objects.create(classification=self.asset.classification, name="97C")
        AssetCert.objects.create(asset=other, cert_type=self.cert_type, certificate_file=SimpleUploadedFile("other.pdf", b"still in use"))
        self.cert.delete()
        data = self.client.get(reverse("accounts:backup_sync_manifest")).json()
        self.assertFalse({f["name"]: f for f in data["files"]}[self.first_name]["current"])
        self.assertEqual(data["old_count"], 1)
        self.assertEqual(data["old_bytes"], len(b"first certificate"))

    def test_the_page_offers_to_keep_old_versions_and_defaults_to_off(self):
        page = self.client.get(reverse("accounts:backup"))
        self.assertContains(page, "Also keep old versions")
        self.assertContains(page, '<input type="checkbox" id="sync-old"')
        self.assertNotContains(page, 'id="sync-old" checked')

    def test_when_no_record_points_to_any_file_everything_counts_as_in_use(self):
        """An empty or wrong database must never make a backup skip every file."""
        from departments.models import AssetCert
        AssetCert.objects.all().delete()
        data = self.client.get(reverse("accounts:backup_sync_manifest")).json()
        self.assertTrue(data["files"])
        self.assertTrue(all(f["current"] for f in data["files"]))
        self.assertEqual(data["old_count"], 0)
