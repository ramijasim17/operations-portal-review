"""
Clean up the files that no record uses any more (old versions of replaced certificates, leftovers of deleted records).

The portal never deletes a file when a record is deleted or its file is replaced, so storage slowly fills with them.
Cleaning up is deliberately cautious:

  * files are MOVED to _trash/<batch>/ first, and can be restored; they are deleted for good only after a waiting period
  * a file is only moved if, at that very moment, no record uses it (the list on the screen is never trusted)
  * files stored in the last 24 hours are never touched: some forms store a file a moment before saving its record
  * if no record points to any file at all (an empty or wrong database), nothing can be told, so nothing is moved
"""
import datetime
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings
from django.utils import timezone

from . import backup as backup_tools

TRASH = "_trash/"
PROTECTED = (TRASH, "_import/")
GRACE = datetime.timedelta(hours=24)
MAX_PER_REQUEST = 1000


class CleanupError(Exception):
    pass


# ── storage: the bucket, or the media folder when working on a computer ─────────────────────────────────────────
class _Bucket:
    def __init__(self):
        self.client = backup_tools._s3_client()
        self.bucket = settings.AWS_STORAGE_BUCKET_NAME

    def entries(self):
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket):
            for obj in page.get("Contents", []):
                if not obj["Key"].endswith("/"):
                    yield obj["Key"], obj["Size"], obj["LastModified"]

    def exists(self, key):
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError:
            return False

    def move(self, source, target):
        self.client.copy_object(Bucket=self.bucket, Key=target, CopySource={"Bucket": self.bucket, "Key": source})
        self.client.delete_object(Bucket=self.bucket, Key=source)

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


class _Folder:
    def __init__(self):
        self.root = os.path.realpath(str(settings.MEDIA_ROOT))

    def _path(self, key):
        path = os.path.realpath(os.path.join(self.root, key))
        if not path.startswith(self.root + os.sep):
            raise CleanupError("Bad file name.")
        return path

    def entries(self):
        for folder, _dirs, names in os.walk(self.root):
            for name in sorted(names):
                if name.startswith("."):
                    continue
                path = os.path.join(folder, name)
                yield (os.path.relpath(path, self.root).replace(os.sep, "/"), os.path.getsize(path),
                       datetime.datetime.fromtimestamp(os.path.getmtime(path), tz=datetime.timezone.utc))

    def exists(self, key):
        return os.path.isfile(self._path(key))

    def move(self, source, target):
        target_path = self._path(target)
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        os.rename(self._path(source), target_path)

    def delete(self, key):
        try:
            os.remove(self._path(key))
        except FileNotFoundError:
            pass


def _store():
    return _Bucket() if settings.USE_S3 else _Folder()


# ── what can be cleaned up ──────────────────────────────────────────────────────────────────────────────────────
def unused_files(now=None):
    """Files no record uses, older than the grace period, newest folder first. None when nothing can be told."""
    now = now or timezone.now()
    in_use = backup_tools.referenced_names()
    if not in_use:
        return None
    rows = []
    for name, size, modified in _store().entries():
        if name.startswith(PROTECTED) or name in in_use or now - modified < GRACE:
            continue
        rows.append({"name": name, "size": size, "modified": modified, "folder": name.split("/")[0] if "/" in name else "(top level)"})
    return rows


def grouped(rows):
    groups = {}
    for row in rows:
        group = groups.setdefault(row["folder"], {"folder": row["folder"], "files": [], "bytes": 0})
        group["files"].append(row)
        group["bytes"] += row["size"]
    for group in groups.values():
        group["files"].sort(key=lambda r: r["name"])
    return sorted(groups.values(), key=lambda g: -g["bytes"])


# ── doing it ────────────────────────────────────────────────────────────────────────────────────────────────────
def move_to_trash(names, user=None, now=None):
    """Move the chosen files to the trash. Returns (batch or None, Counter of what was skipped and why)."""
    from .models import TrashBatch

    now = now or timezone.now()
    names = list(dict.fromkeys(n for n in names if n))
    if len(names) > MAX_PER_REQUEST:
        raise CleanupError(f"At most {MAX_PER_REQUEST} files at a time. Do the rest in another round.")
    in_use = backup_tools.referenced_names()
    if not in_use:
        raise CleanupError("No record points to any file, so the portal cannot tell which files are unused. Nothing was moved.")
    store = _store()
    stored = {name: (size, modified) for name, size, modified in store.entries()}
    skipped, chosen = Counter(), []
    for name in names:
        if name.startswith(PROTECTED) or ".." in name.split("/") or name.startswith("/"):
            skipped["protected"] += 1
        elif name not in stored:
            skipped["gone"] += 1
        elif name in in_use:
            skipped["in use"] += 1
        elif now - stored[name][1] < GRACE:
            skipped["too recent"] += 1
        else:
            chosen.append(name)
    if not chosen:
        return None, skipped

    batch = TrashBatch.objects.create(by=user if getattr(user, "is_authenticated", False) else None, created_at=now)

    def move(name):
        try:
            store.move(name, f"{TRASH}{batch.pk}/{name}")
            return name, None
        except Exception as error:                      # one failing file must not stop the rest
            return name, str(error)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(move, chosen))
    moved = [{"name": name, "size": stored[name][0]} for name, error in results if error is None]
    failed = sum(1 for _name, error in results if error is not None)
    if failed:
        skipped["failed"] += failed
    if not moved:
        batch.delete()
        return None, skipped
    batch.items, batch.count, batch.bytes = moved, len(moved), sum(i["size"] for i in moved)
    batch.save()
    return batch, skipped


def restore(batch):
    """Put a batch's files back where they were. Returns (restored, kept) -- kept = a file with that name exists again."""
    if batch.purged_at:
        raise CleanupError("These files were deleted for good, so they cannot be restored.")
    store = _store()
    restored, kept = 0, []
    for item in list(batch.items):
        name = item["name"]
        if store.exists(name):
            kept.append(item)
            continue
        try:
            store.move(f"{TRASH}{batch.pk}/{name}", name)
            restored += 1
        except Exception:
            kept.append(item)
    batch.items = kept                              # count and bytes keep the original totals, as a record
    if not kept:
        batch.restored_at = timezone.now()
    batch.save()
    return restored, kept


def purge(batch, now=None):
    """Delete a batch's files for good -- only once the waiting period is over."""
    now = now or timezone.now()
    if now < batch.purge_allowed_from:
        raise CleanupError(f"These files are kept until {timezone.localtime(batch.purge_allowed_from):%d %b %Y, %H:%M}, in case you need them back.")
    if batch.purged_at or not batch.items:
        raise CleanupError("Nothing left in this batch.")
    store = _store()
    for item in batch.items:
        store.delete(f"{TRASH}{batch.pk}/{item['name']}")
    batch.purged_at = now
    batch.save()
    return len(batch.items)
