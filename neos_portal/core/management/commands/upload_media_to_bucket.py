"""
Copy the local media/ folder into the S3/R2 bucket, keeping the same names the
database already stores (so "asset_certs/x.pdf" in a record is "asset_certs/x.pdf"
in the bucket). Safe to run again: files already in the bucket are skipped.

Usage (with the AWS_* variables set to the bucket's values):
    python manage.py upload_media_to_bucket --dry-run
    python manage.py upload_media_to_bucket
"""
import mimetypes

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Upload everything under MEDIA_ROOT to the configured bucket (skips files that are already there)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="List what would be uploaded; change nothing.")

    def handle(self, *args, dry_run=False, **options):
        import boto3
        from botocore.exceptions import ClientError

        if not (settings.AWS_STORAGE_BUCKET_NAME and settings.AWS_S3_ENDPOINT_URL
                and settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY):
            raise CommandError("Set AWS_STORAGE_BUCKET_NAME, AWS_S3_ENDPOINT_URL, AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY first.")
        root = settings.BASE_DIR / "media"
        if not root.is_dir():
            raise CommandError(f"No media folder at {root}")

        client = boto3.client(
            "s3", endpoint_url=settings.AWS_S3_ENDPOINT_URL, region_name=settings.AWS_S3_REGION_NAME,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY)
        bucket = settings.AWS_STORAGE_BUCKET_NAME

        sent = skipped = 0
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith(".")):
            key = path.relative_to(root).as_posix()
            try:
                client.head_object(Bucket=bucket, Key=key)
                skipped += 1
                continue
            except ClientError as exc:
                if exc.response["Error"]["Code"] not in ("404", "NoSuchKey", "NotFound"):
                    raise
            sent += 1
            self.stdout.write(f"{'would upload' if dry_run else 'uploading'}  {key}")
            if not dry_run:
                extra = {"ContentType": mimetypes.guess_type(key)[0] or "application/octet-stream"}
                client.upload_file(str(path), bucket, key, ExtraArgs=extra)
        self.stdout.write(self.style.SUCCESS(f"{'Would upload' if dry_run else 'Uploaded'} {sent} file(s); {skipped} already in the bucket."))
