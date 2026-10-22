from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone


class Approval(models.Model):
    """One thing waiting for (or past) a Level 3 decision. A record that has no Approval row at all
    was there before approvals existed (or was made by a script) and counts as approved."""
    PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"
    STATUS_CHOICES = [(PENDING, "Pending approval"), (APPROVED, "Approved"), (REJECTED, "Rejected")]

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    kind = models.CharField(max_length=30)                       # see approvals/registry.py
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    label = models.CharField(max_length=250, blank=True, default="")      # what it was called when submitted
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=300, blank=True, default="")
    auto = models.BooleanField(default=False, help_text="Approved on the spot because a Level 3 made it.")

    class Meta:
        unique_together = ("content_type", "object_id", "kind")
        ordering = ["-submitted_at", "-id"]
        indexes = [models.Index(fields=["status", "kind"])]

    def __str__(self):
        return f"{self.kind} {self.label} ({self.status})"

    @property
    def is_pending(self):
        return self.status == self.PENDING


class ApprovalLog(models.Model):
    """The audit trail: who submitted, approved, rejected or asked for a removal, and when."""
    content_type = models.ForeignKey(ContentType, null=True, on_delete=models.SET_NULL)
    object_id = models.PositiveIntegerField(null=True)
    kind = models.CharField(max_length=30)
    label = models.CharField(max_length=250, blank=True, default="")
    action = models.CharField(max_length=20)       # submitted / approved / rejected / resubmitted / auto
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        ordering = ["-at", "-id"]
