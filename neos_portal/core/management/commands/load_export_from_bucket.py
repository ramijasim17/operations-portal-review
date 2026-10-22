"""
One-off: replace the database contents with a data export (dumpdata JSON) that was uploaded to the
private bucket, running next to the database so it takes a minute instead of an hour.

It does nothing unless BOTH variables are set on the service:
    IMPORT_EXPORT_KEY   the object name of the file in the bucket (e.g. "_import/data.json")
    IMPORT_ALLOW_FLUSH  the word "yes"  (the import first EMPTIES every table)
Remove both variables, and the file in the bucket, as soon as it has run.
"""
import os
import tempfile

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Empty the database and load an export from the bucket (guarded by IMPORT_EXPORT_KEY + IMPORT_ALLOW_FLUSH=yes)."

    def handle(self, *args, **options):
        key = os.environ.get("IMPORT_EXPORT_KEY", "").strip()
        if not key:
            self.stdout.write("IMPORT_EXPORT_KEY not set: nothing to import.")
            return
        if os.environ.get("IMPORT_ALLOW_FLUSH", "").strip().lower() != "yes":
            raise CommandError("IMPORT_EXPORT_KEY is set, but IMPORT_ALLOW_FLUSH is not 'yes': refusing to empty the database.")
        if not (settings.AWS_STORAGE_BUCKET_NAME and settings.AWS_S3_ENDPOINT_URL):
            raise CommandError("The bucket settings (AWS_*) are not configured.")

        import boto3
        client = boto3.client(
            "s3", endpoint_url=settings.AWS_S3_ENDPOINT_URL, region_name=settings.AWS_S3_REGION_NAME,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "data.json")
            client.download_file(settings.AWS_STORAGE_BUCKET_NAME, key, path)
            self.stdout.write(f"Downloaded {key} ({os.path.getsize(path)} bytes). Emptying the database…")
            call_command("flush", interactive=False, verbosity=0)
            call_command("loaddata", path, verbosity=1)
        self.stdout.write(self.style.SUCCESS("Import finished. Now remove IMPORT_EXPORT_KEY and IMPORT_ALLOW_FLUSH, and delete the file from the bucket."))
