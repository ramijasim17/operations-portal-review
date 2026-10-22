import datetime

from django.db import models
from django.utils import timezone


class WAFileType(models.Model):
    type_name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["type_name"]

    def __str__(self):
        return self.type_name


class WARecord(models.Model):
    file_type = models.ForeignKey(WAFileType, on_delete=models.CASCADE, related_name="records")
    expiry_date = models.DateField(null=True, blank=True)
    details = models.CharField(max_length=255, blank=True, default="")
    location_name = models.CharField(max_length=150, blank=True, default="")
    file = models.FileField(upload_to="wa_files/", blank=True, null=True)
    notes = models.TextField(blank=True, default="")
    uploaded_date = models.DateField(auto_now_add=True)

    class Meta:
        ordering = ["expiry_date"]

    STATUS_LABELS = {"expired": "Expired", "soon": "Expiring soon", "valid": "Valid",
                     "na": "No expiry date", "missing": "No file"}
    STATUS_RANK = {"expired": 0, "soon": 1, "valid": 2, "na": 3, "missing": 4}

    def __str__(self):
        return f"{self.file_type} \u2014 {self.details or self.uploaded_date}"

    @property
    def status_label(self):
        return self.STATUS_LABELS[self.status]

    @property
    def days_left(self):
        return (self.expiry_date - timezone.localdate()).days if self.expiry_date else None

    @property
    def needs_renewal(self):
        return self.status in ("expired", "soon")

    @staticmethod
    def urgency_key(record):
        """Expired first (longest overdue at the top), then expiring soon,
        then valid by date; no-date and no-file records last."""
        return (record.STATUS_RANK[record.status], record.expiry_date or datetime.date.max, record.pk)

    @property
    def status(self):
        if not self.file and not self.notes:
            return "missing"
        if not self.expiry_date:
            return "na"
        today = timezone.localdate()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + datetime.timedelta(days=30):
            return "soon"
        return "valid"
