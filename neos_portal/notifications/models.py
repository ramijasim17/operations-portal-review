from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    """One alert for one person. 'activity' = something was added / changed / uploaded / deleted
    (sent to everyone who has asked for every change); 'assigned' = something was given to you."""
    ACTIVITY, ASSIGNED, APPROVAL = "activity", "assigned", "approval"
    KIND_CHOICES = [(ACTIVITY, "Activity"), (ASSIGNED, "Assigned to you"), (APPROVAL, "Approvals")]

    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=ACTIVITY)
    message = models.CharField(max_length=400)
    link = models.CharField(max_length=300, blank=True, default="")
    # Used to fold a burst of the same change (e.g. an import of 40 rows) into one alert.
    model_label = models.CharField(max_length=100, blank=True, default="")
    verb = models.CharField(max_length=20, blank=True, default="")
    count = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        indexes = [models.Index(fields=["recipient", "read_at"])]

    def __str__(self):
        return self.message

    @property
    def is_unread(self):
        return self.read_at is None
