"""
Check that a downloaded backup zip is complete and undamaged.

Usage:
    python manage.py verify_backup "/path/to/abc-backup-full-20261009-0001.zip"
"""
import json
import zipfile

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check a backup zip: every file must read back with a matching checksum, and the manifest must agree."

    def add_arguments(self, parser):
        parser.add_argument("path")

    def handle(self, *args, path, **options):
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise CommandError(f"Not a readable zip file: {exc}")
        with archive:
            bad = archive.testzip()
            if bad:
                raise CommandError(f"DAMAGED: '{bad}' does not match its checksum. Download the backup again.")
            names = set(archive.namelist())
            problems = []
            if "manifest.json" in names:
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("kind") == "full":
                    held = {n[len("files/"):] for n in names if n.startswith("files/")}
                    problems = [f["name"] for f in manifest["files_in_storage"] if f["name"] not in held]
            if problems:
                raise CommandError(f"INCOMPLETE: {len(problems)} file(s) listed in the manifest are missing, e.g. {problems[0]}")
            self.stdout.write(self.style.SUCCESS(
                f"OK: {len(names)} entries read back with matching checksums"
                f"{' and the manifest agrees' if 'manifest.json' in names else ''}."))
