"""
A backup of the portal as ONE zip that is streamed to the browser (so the server never holds the whole file):

    database.json   every record (users, personnel, assets, SGS results ...), loadable with `manage.py loaddata`
    files/...       the uploaded files (certificates, SGS logs, documents), from the bucket or the media folder
    code/...        the application code that is running (never passwords or .env files)
    manifest.json   every file that exists right now (so deletions show up) and the code fingerprint
    README.txt      how to restore

Two kinds of backup:
    full     everything.
    changes  the database (always whole -- it is small and its records carry no reliable "changed on" date),
             only the files that are new or changed since the last backup that was recorded, and the code only if
             it differs from the last backup.

A backup is recorded (BackupLog) only when the download finishes, so a cancelled one never becomes the baseline.
The admin saves the download wherever they like, for example straight onto an external hard disk.
"""
import datetime
import hashlib
import io
import json
import os
import secrets
import zipfile

from django.conf import settings
from django.core.management import call_command
from django.utils import timezone

PARTS = ("database", "files", "code")
FULL, CHANGES, SYNC = "full", "changes", "sync"

# Never put these in a backup of the code.
SKIP_DIRS = {"venv", ".venv", "__pycache__", "staticfiles", "media", "backups", "export_for_render",
             "tester_copy", ".git", "node_modules", ".idea", ".vscode"}
SKIP_SUFFIXES = (".pyc", ".sqlite3", ".log")
SKIP_NAMES = {".DS_Store", ".env"}

# Already-compressed formats are stored as they are; everything else is compressed.
STORED_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".zip", ".docx", ".xlsx")

CHUNK = 1024 * 1024

README = """ABC Operations Portal -- {kind} backup made {when}
Parts in this file: {parts}
{since}
database.json  -- all records, always complete.  Restore into an EMPTY, migrated database:
                    python manage.py migrate
                    python manage.py loaddata database.json
                  It contains staff details and password hashes: keep this file private.
files/         -- uploaded files, same names the database uses.  Put them in the media folder (or the bucket)
                  and run   python manage.py upload_media_to_bucket
manifest.json  -- every file that existed when this backup was made.  Any file you hold that is NOT listed
                  there was deleted since, and can be removed.
code/          -- the application code that was running.  Its passwords and keys are NOT included; they live
                  in the host's environment variables.

Restoring from "changes only" backups: start from the last FULL backup, then apply each changes-only backup
in date order (copy its files over the top), and use the NEWEST database.json and manifest.json.
"""


class _Sink:
    """What the zip writes into: remembers the bytes until the generator hands them to the browser."""

    def __init__(self):
        self.chunks = []
        self.size = 0

    def write(self, data):
        self.chunks.append(bytes(data))
        self.size += len(data)
        return len(data)

    def tell(self):
        return self.size

    def flush(self):
        pass

    def drain(self):
        data, self.chunks = b"".join(self.chunks), []
        return data


def filename(parts, kind=FULL, now=None, base=None):
    """e.g. abc-backup-full-20261009-0001.zip, or abc-backup-changes-20261012-0900-on-20261009-0001.zip"""
    now = now or timezone.localtime()
    label = kind if set(parts) == set(PARTS) else "-".join(parts)
    on = f"-on-{base.label}" if base is not None and kind == CHANGES else ""
    return f"abc-backup-{label}-{now:%Y%m%d-%H%M}{on}.zip"


def _info(name, now, stored):
    info = zipfile.ZipInfo(name, date_time=now.timetuple()[:6])
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def _database_json():
    out = io.StringIO()
    call_command("dumpdata", use_natural_foreign_keys=True, use_natural_primary_keys=True, indent=1,
                 exclude=["contenttypes", "auth.permission", "sessions", "admin.logentry"], stdout=out)
    return out.getvalue().encode("utf-8")


def _s3_client():
    import boto3
    return boto3.client(
        "s3", endpoint_url=settings.AWS_S3_ENDPOINT_URL, region_name=settings.AWS_S3_REGION_NAME,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY)


def _read_chunks(path):
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                return
            yield chunk


def _file_sources():
    """(name, chunk-opener, size, modified) for every uploaded file."""
    if settings.USE_S3:
        client = _s3_client()
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("/") or key.startswith(("_import/", "_trash/")):
                    continue
                yield (key, (lambda key=key: client.get_object(Bucket=bucket, Key=key)["Body"].iter_chunks(CHUNK)),
                       obj["Size"], obj["LastModified"])
    else:
        root = str(settings.MEDIA_ROOT)
        for folder, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if not (folder == root and d == "_trash"))
            for name in sorted(names):
                if name.startswith("."):
                    continue
                path = os.path.join(folder, name)
                modified = datetime.datetime.fromtimestamp(os.path.getmtime(path), tz=datetime.timezone.utc)
                yield (os.path.relpath(path, root).replace(os.sep, "/"), (lambda path=path: _read_chunks(path)),
                       os.path.getsize(path), modified)


def _code_sources():
    base = str(settings.BASE_DIR)
    for folder, dirs, names in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES) or (name.startswith(".env") and name != ".env.example"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, base).replace(os.sep, "/")
            yield f"code/neos_portal/{rel}", (lambda path=path: _read_chunks(path)), os.path.getsize(path)
    top = os.path.dirname(base)
    for name in ("render.yaml",):
        path = os.path.join(top, name)
        if os.path.isfile(path):
            yield f"code/{name}", (lambda path=path: _read_chunks(path)), os.path.getsize(path)


def code_fingerprint():
    """A short fingerprint of the code that is running: changes whenever any code file changes."""
    digest = hashlib.sha256()
    for name, open_chunks, _size in _code_sources():
        digest.update(name.encode("utf-8"))
        for chunk in open_chunks():
            digest.update(chunk)
    return digest.hexdigest()


def describe():
    """Counts and sizes for the Backup page (cheap: a listing only)."""
    files = [size for _, _, size, _ in _file_sources()]
    code = [size for _, _, size in _code_sources()]
    return {"files_count": len(files), "files_bytes": sum(files), "code_count": len(code), "code_bytes": sum(code)}


def changes_preview(previous):
    """What a changes-only backup would contain right now, measured from the previous recorded backup."""
    total = changed = changed_bytes = 0
    for _name, _open, size, modified in _file_sources():
        total += 1
        if modified > previous.started_at:
            changed += 1
            changed_bytes += size
    return {"files_total": total, "files_changed": changed, "files_changed_bytes": changed_bytes,
            "code_changed": code_fingerprint() != previous.code_fingerprint}


def stream(parts, user=None, kind=FULL, previous=None, now=None):
    """Yield the zip file in pieces. Records a BackupLog when (and only when) it is read to the end."""
    from .models import BackupLog

    parts = [p for p in PARTS if p in parts]
    incremental = kind == CHANGES and previous is not None
    kind = CHANGES if incremental else FULL
    now = now or timezone.localtime()
    started_at = timezone.now()
    token = secrets.token_hex(6)
    sink = _Sink()
    fingerprint = code_fingerprint() if "code" in parts else ""
    include_code = "code" in parts and (not incremental or fingerprint != previous.code_fingerprint)
    in_storage = included = included_bytes = 0
    manifest_files = []

    since = (f"Changes on top of the backup {previous.label} (made {timezone.localtime(previous.started_at):%Y-%m-%d %H:%M}).\n"
             f"To apply them:  python manage.py merge_backup <that backup>.zip <this file>.zip --output <new>.zip\n") if incremental else ""
    with zipfile.ZipFile(sink, "w", allowZip64=True) as archive:
        archive.writestr(_info("README.txt", now, False),
                         README.format(kind=kind, when=f"{now:%Y-%m-%d %H:%M}", parts=", ".join(parts), since=since))
        if "database" in parts:
            archive.writestr(_info("database.json", now, False), _database_json())
            yield sink.drain()
        if "files" in parts:
            for name, open_chunks, size, modified in _file_sources():
                in_storage += 1
                manifest_files.append({"name": name, "size": size})
                if incremental and modified <= previous.started_at:
                    continue
                with archive.open(_info(f"files/{name}", now, name.lower().endswith(STORED_SUFFIXES)), "w", force_zip64=True) as member:
                    for chunk in open_chunks():
                        member.write(chunk)
                        data = sink.drain()
                        if data:
                            yield data
                included += 1
                included_bytes += size
                yield sink.drain()
        if include_code:
            for name, open_chunks, _size in _code_sources():
                with archive.open(_info(name, now, name.lower().endswith(STORED_SUFFIXES)), "w", force_zip64=True) as member:
                    for chunk in open_chunks():
                        member.write(chunk)
                        data = sink.drain()
                        if data:
                            yield data
                yield sink.drain()
        archive.writestr(_info("manifest.json", now, False), json.dumps({
            "made": now.isoformat(), "kind": kind, "parts": parts,
            "changes_since": timezone.localtime(previous.started_at).isoformat() if incremental else None,
            "based_on": previous.label if incremental else None, "stamp": f"{now:%Y%m%d-%H%M}",
            "token": token, "based_on_token": previous.token if incremental else None,
            "files_in_storage": manifest_files, "files_included": included,
            "code_included": include_code, "code_fingerprint": fingerprint}, indent=1))
    yield sink.drain()

    if set(parts) == set(PARTS):                  # a single-part download never becomes the baseline
        BackupLog.objects.create(
            kind=kind, started_at=started_at, by=user if getattr(user, "is_authenticated", False) else None,
            files_in_storage=in_storage, files_included=included, files_bytes=included_bytes,
            code_included=include_code, code_fingerprint=fingerprint, zip_bytes=sink.size,
            stamp=f"{now:%Y%m%d-%H%M}", token=token, based_on=previous if incremental else None)


def referenced_names():
    """Names of the files that some record in the database points to, i.e. the files the portal uses right now.

    The portal never deletes a file when a record is deleted or its file is replaced, so storage also holds old versions."""
    from django.apps import apps
    from django.db import models

    names = set()
    for model in apps.get_models():
        for field in model._meta.get_fields():
            if isinstance(field, models.FileField):
                rows = model._default_manager.exclude(**{field.name: ""}).exclude(**{f"{field.name}__isnull": True})
                names.update(value for value in rows.values_list(field.name, flat=True) if value)
    return names


def sync_manifest(now=None):
    """Everything the browser needs to bring a backup folder up to date: every file with a signature (and whether the
    portal still uses it), and the code fingerprint."""
    now = now or timezone.localtime()
    in_use = referenced_names()
    everything = [(name, size, modified) for name, _open, size, modified in _file_sources()]
    # If no record points to any file at all, we cannot tell what is in use (an empty or wrong database): treat every
    # file as in use, so a backup can never silently skip them.
    can_tell = bool(in_use)
    files = [{"name": name, "size": size, "sig": f"{size}:{modified.isoformat()}", "current": (name in in_use) if can_tell else True}
             for name, size, modified in everything]
    old = [f for f in files if not f["current"]]
    return {"made": now.isoformat(), "stamp": f"{now:%Y%m%d-%H%M}", "files": files, "code_fingerprint": code_fingerprint(),
            "old_count": len(old), "old_bytes": sum(f["size"] for f in old)}


def open_file(name):
    """(size, chunk iterator) for ONE uploaded file, or None when there is no such file. The name is checked, never trusted."""
    if not name or name.startswith(("/", "_import/", "_trash/")) or ".." in name.split("/") or "\\" in name or "\x00" in name:
        return None
    if settings.USE_S3:
        from botocore.exceptions import ClientError
        client = _s3_client()
        try:
            size = client.head_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=name)["ContentLength"]
        except ClientError:
            return None
        return size, client.get_object(Bucket=settings.AWS_STORAGE_BUCKET_NAME, Key=name)["Body"].iter_chunks(CHUNK)
    root = os.path.realpath(str(settings.MEDIA_ROOT))
    path = os.path.realpath(os.path.join(root, name))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None
    return os.path.getsize(path), _read_chunks(path)


def database_bytes():
    return _database_json()


def code_zip(now=None):
    """The code part alone, as a zip, streamed."""
    return stream(("code",), now=now)
