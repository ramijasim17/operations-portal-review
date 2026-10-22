"""
accounts/models.py — extends Django's built-in User with a role.

Django already gives you: secure password hashing, login/logout, session
management, and password reset. We don't rebuild any of that — we add one
thing on top: a Role, so you can approve someone's account AND decide what
they're allowed to do, in one place.
"""
import datetime
import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class Role(models.TextChoices):
    """Three working levels plus an Admin who also manages accounts.
        Level 1  sees everything; cannot add, edit, download or approve.
        Level 2  sees everything; can add, edit and download; its work waits for approval.
        Level 3  everything Level 2 can, plus approves (manifests, assets, personnel, SGS reports,
                 maintenance reports) and removes records.
        Admin    Level 3, plus Access Control (accounts and roles)."""
    VIEWER = "viewer", "Level 1 \u2014 View"
    EDITOR = "editor", "Level 2 \u2014 Add & edit"
    APPROVER = "approver", "Level 3 \u2014 Approve"
    ADMIN = "admin", "Admin"


class Profile(models.Model):
    """One-to-one extension of Django's User model. Created automatically
    for every new user (see signals below) so `request.user.profile.role`
    is always safe to access."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.VIEWER)
    employee = models.OneToOneField(
        "personnel.Personnel", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="portal_profile",
        help_text="The Personnel record this account belongs to (set at sign-up from the Employee ID).",
    )
    notify_all = models.BooleanField(
        default=False, verbose_name="Gets every change alert",
        help_text="For managers: be notified of everything other people add, upload, change or delete.",
    )
    is_active_employee = models.BooleanField(
        default=True,
        help_text="Turn off to block login without deleting the account or its history.",
    )

    last_seen = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="Last time this person had the portal open (cleared when they sign out).",
    )

    # "Online" means the portal is open in their browser: pages check in about every 5 seconds, but browsers slow
    # background tabs down to roughly once a minute, so allow a few minutes.
    ONLINE_WINDOW = datetime.timedelta(minutes=3)
    SEEN_REFRESH = datetime.timedelta(seconds=60)          # don't write to the database more often than this

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    @property
    def is_online(self) -> bool:
        return bool(self.last_seen and timezone.now() - self.last_seen <= self.ONLINE_WINDOW)

    @property
    def seen_ago(self) -> str:
        """"22 hours", "4 minutes" ... only the biggest unit, so it fits in a table cell."""
        from django.utils.timesince import timesince
        return timesince(self.last_seen).split(",")[0] if self.last_seen else ""

    @property
    def display_name(self) -> str:
        if self.employee_id and self.employee:
            return self.employee.name
        return self.user.get_full_name() or self.user.username

    @classmethod
    def online_now(cls):
        """Everyone with the portal open right now, most recently active first."""
        cutoff = timezone.now() - cls.ONLINE_WINDOW
        return (cls.objects.filter(last_seen__gte=cutoff, is_active_employee=True, user__is_active=True)
                .select_related("user", "employee").order_by("-last_seen"))

    @property
    def can_edit(self) -> bool:
        return self.role in (Role.EDITOR, Role.APPROVER, Role.ADMIN)

    @property
    def can_download(self) -> bool:
        return self.role in (Role.EDITOR, Role.APPROVER, Role.ADMIN)

    @property
    def can_approve(self) -> bool:
        return self.role in (Role.APPROVER, Role.ADMIN)

    @property
    def can_delete(self) -> bool:
        """Remove a record for good. Level 2 can only ask for a removal (assets, personnel)."""
        return self.role in (Role.APPROVER, Role.ADMIN)

    @property
    def is_admin(self) -> bool:
        return self.role == Role.ADMIN


class EmailVerification(models.Model):
    """A short-lived code sent to a new signup's email address. The
    account exists (Django User, is_active=False) but can't log in until
    this code is confirmed -- one row per pending user, deleted once
    used or expired."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_verification"
    )
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)
    # Wrong guesses so far; the view discards the signup after too many,
    # so the 6-digit code can't be brute-forced.
    attempts = models.PositiveSmallIntegerField(default=0)

    MAX_ATTEMPTS = 5
    RESEND_COOLDOWN_SECONDS = 60

    def is_expired(self) -> bool:
        return timezone.now() > self.created_at + datetime.timedelta(minutes=15)

    def can_resend(self) -> bool:
        wait = datetime.timedelta(seconds=self.RESEND_COOLDOWN_SECONDS)
        return timezone.now() >= self.created_at + wait

    @staticmethod
    def generate_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    def __str__(self):
        return f"Pending verification for {self.user.username}"


class BackupLog(models.Model):
    """One finished backup download. The next "changes only" backup is measured from the last of these."""

    FULL, CHANGES, SYNC = "full", "changes", "sync"
    KIND_CHOICES = ((FULL, "Full"), (CHANGES, "Changes only"), (SYNC, "Folder update"))

    kind = models.CharField(max_length=10, choices=KIND_CHOICES)
    started_at = models.DateTimeField(help_text="Files changed after this moment go into the next changes-only backup.")
    finished_at = models.DateTimeField(default=timezone.now)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    files_in_storage = models.PositiveIntegerField(default=0)
    files_included = models.PositiveIntegerField(default=0)
    files_bytes = models.BigIntegerField(default=0)
    code_included = models.BooleanField(default=False)
    code_fingerprint = models.CharField(max_length=64, blank=True, default="")
    zip_bytes = models.BigIntegerField(default=0)
    stamp = models.CharField(max_length=20, blank=True, default="", help_text="The date and time in the file name, e.g. 20261009-0001.")
    token = models.CharField(max_length=24, blank=True, default="", help_text="Unique id, written inside the backup so a chain of backups can be checked.")
    based_on = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="followers",
                                 help_text="For a changes-only backup: the backup it was measured from.")

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.get_kind_display()} backup {self.label}"

    @property
    def label(self):
        """What to look for in the file name on the disk."""
        return self.stamp or f"{timezone.localtime(self.started_at):%Y%m%d-%H%M}"


class TrashBatch(models.Model):
    """Unused files that were moved aside (not deleted) in one clean-up. They can be restored, and are only
    deleted for good after a waiting period."""

    HOLD = datetime.timedelta(days=7)

    created_at = models.DateTimeField(default=timezone.now)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    items = models.JSONField(default=list, help_text='Files still in the trash: [{"name": ..., "size": ...}]')
    count = models.PositiveIntegerField(default=0)
    bytes = models.BigIntegerField(default=0)
    restored_at = models.DateTimeField(null=True, blank=True)
    purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def purge_allowed_from(self):
        return self.created_at + self.HOLD

    @property
    def can_purge(self):
        return bool(self.items) and not self.purged_at and timezone.now() >= self.purge_allowed_from

    @property
    def active(self):
        return bool(self.items) and not self.purged_at

    def __str__(self):
        return f"Trash batch {self.pk} ({self.count} files)"
