"""
Apply one or more "changes only" backups on top of an older backup, giving ONE updated full backup.

    python manage.py merge_backup OLD.zip CHANGES.zip [MORE-CHANGES.zip ...] --output UPDATED.zip
    python manage.py merge_backup OLD.zip CHANGES.zip --replace          # overwrite OLD once the result is checked

What it does, in date order:
  * files in a changes backup replace (or add to) the files of the old one
  * files that the newest manifest no longer lists were deleted, so they are left out
  * database.json, manifest.json and README.txt come from the newest backup
  * code/ comes from the newest backup that included code (changes backups leave it out when it did not change)
The result is read back and checked before it is allowed to replace anything.
"""
import json
import os
import re
import shutil
import zipfile

from django.core.management.base import BaseCommand, CommandError

TOP_LEVEL = ("database.json", "manifest.json", "README.txt")


def _manifest(archive, path):
    try:
        return json.loads(archive.read("manifest.json"))
    except KeyError:
        raise CommandError(f"{path} has no manifest.json: it is not a backup made by the portal.")


class Command(BaseCommand):
    help = "Apply changes-only backups on top of an older backup and write one updated full backup."

    def add_arguments(self, parser):
        parser.add_argument("old", help="The older backup (full, or an already merged one).")
        parser.add_argument("changes", nargs="+", help="One or more changes-only backups, oldest first.")
        parser.add_argument("--output", help="Where to write the updated full backup.")
        parser.add_argument("--replace", action="store_true", help="Overwrite the old backup with the updated one (after checking it).")

    def handle(self, *args, old, changes, output=None, replace=False, **options):
        if bool(output) == bool(replace):
            raise CommandError("Give exactly one of --output FILE or --replace.")
        target = old if replace else output
        if not replace and os.path.exists(output):
            raise CommandError(f"{output} already exists. Choose another name, or use --replace.")
        temp = f"{target}.merging"

        chain = []
        try:
            for path in (old, *changes):
                chain.append((path, zipfile.ZipFile(path)))
        except (OSError, zipfile.BadZipFile) as exc:
            for _, opened in chain:
                opened.close()
            raise CommandError(f"Cannot read a backup file: {exc}")
        try:
            self._check_order(chain)
            self._merge(chain, temp)
            self._check(temp)
            os.replace(temp, target)
        except Exception:
            if os.path.exists(temp):
                os.remove(temp)
            raise
        finally:
            for _, opened in chain:
                opened.close()
        self.stdout.write(self.style.SUCCESS(f"Updated backup written to {target}"))

    @staticmethod
    def _label(path, manifest):
        """The stamp a backup carries: from its manifest, or (for older backups) from its file name."""
        found = manifest.get("stamp") or (re.search(r"(\d{8}-\d{4})", os.path.basename(path)) or [None, ""])[1]
        return found

    def _check_order(self, chain):
        """Each changes file must sit directly on top of the one before it, or some changed files would be missed."""
        for (before_path, before), (path, archive) in zip(chain, chain[1:]):
            manifest = _manifest(archive, path)
            if manifest.get("kind") != "changes":
                raise CommandError(f"{path} is a full backup, not a changes-only one. Use it as the OLD backup instead.")
            before_manifest = _manifest(before, before_path)
            if manifest.get("based_on_token") and before_manifest.get("token"):      # exact check
                in_order = manifest["based_on_token"] == before_manifest["token"]
            else:                                                                     # older backups: compare the file-name stamps
                wanted_label, held_label = manifest.get("based_on"), self._label(before_path, before_manifest)
                in_order = not (wanted_label and held_label and wanted_label != held_label)
            expected, wanted = self._label(before_path, before_manifest), manifest.get("based_on")
            if not in_order:
                raise CommandError(
                    f"{os.path.basename(path)} was made on top of backup {wanted}, but the file before it in your list is {expected}. "
                    f"Add the missing changes file(s), in date order, or start from backup {wanted}.")

    def _merge(self, chain, temp):
        newest_path, newest = chain[-1]
        manifest = _manifest(newest, newest_path)
        still_there = {f["name"] for f in manifest.get("files_in_storage", [])}

        # For every wanted entry, which zip holds the newest version of it.
        source = {}
        for path, archive in chain:
            for info in archive.infolist():
                name = info.filename
                if name in TOP_LEVEL or name.endswith("/"):
                    continue
                if name.startswith("files/") and name[len("files/"):] not in still_there:
                    continue                                                    # deleted since
                source[name] = (path, archive, info)

        for name in TOP_LEVEL:                                                  # always from the newest backup
            if name in newest.namelist():
                source[name] = (newest_path, newest, newest.getinfo(name))

        merged_manifest = dict(manifest, kind="full", changes_since=None, based_on=None,
                               files_included=len(still_there), code_included=any(n.startswith("code/") for n in source))
        with zipfile.ZipFile(temp, "w", allowZip64=True) as out:
            for name in sorted(source):
                _path, archive, info = source[name]
                if name == "manifest.json":
                    out.writestr(self._copy_info(info), json.dumps(merged_manifest, indent=1))
                    continue
                if name == "README.txt":
                    text = archive.read(name).decode("utf-8")
                    out.writestr(self._copy_info(info), text + "\n(This file was produced by merge_backup: older backup plus changes.)\n")
                    continue
                with archive.open(info) as read, out.open(self._copy_info(info), "w", force_zip64=True) as write:
                    shutil.copyfileobj(read, write, 1024 * 1024)

    @staticmethod
    def _copy_info(info):
        new = zipfile.ZipInfo(info.filename, date_time=info.date_time)
        new.compress_type = info.compress_type
        new.external_attr = info.external_attr
        return new

    def _check(self, path):
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise CommandError(f"The merged file failed its own check ('{bad}'). Nothing was replaced.")
            names = set(archive.namelist())
            manifest = json.loads(archive.read("manifest.json"))
            missing = [f["name"] for f in manifest["files_in_storage"] if f"files/{f['name']}" not in names]
            if missing:
                raise CommandError(f"The merged backup would be missing {len(missing)} file(s), e.g. {missing[0]}. "
                                   f"Is a changes file missing from the list? Nothing was replaced.")
