import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone

PRIORITY_CHOICES = [("High", "High"), ("Medium", "Medium"), ("Low", "Low")]
STATUS_CHOICES = [("Pending", "Pending"), ("In Progress", "In Progress"), ("Completed", "Completed")]
TYPE_CHOICES = [
    ("equipment", "Equipment"), ("personnel", "Personnel"), ("procedure", "Procedure / HSE"),
    ("documentation", "Documentation"), ("commercial", "Commercial"), ("other", "Other"),
]
TYPE_LABELS = dict(TYPE_CHOICES)
INTERNAL = "ABC internal"
PRIORITY_RANK = {"High": 0, "Medium": 1, "Low": 2}


class Action(models.Model):
    action_item = models.CharField(max_length=255)
    assignee = models.CharField(max_length=255, blank=True, default="")
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default="Medium")
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="Pending")
    comments = models.TextField(blank=True, default="")
    # Who raised it, what kind it is, and when -- for the insights page. Actions created before
    # these existed have no raised/closed dates, so they are left out of the timing figures.
    client = models.CharField(max_length=255, blank=True, default="", help_text="Blank = not recorded; 'ABC internal' for our own.")
    action_type = models.CharField(max_length=20, choices=TYPE_CHOICES, blank=True, default="")
    raised_date = models.DateField(null=True, blank=True)
    closed_date = models.DateField(null=True, blank=True)
    department = models.ForeignKey("departments.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   help_text="Who sees it: Level 1 and 2 see only their own department's actions.")
    job_report = models.ForeignKey("job_reports.JobReport", null=True, blank=True, on_delete=models.SET_NULL, related_name="actions")

    class Meta:
        ordering = ["due_date", "id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded_status = self.status
        self._loaded_assignee = self.assignee

    def derive_department_id(self):
        """The department of the person it is assigned to, when their Personnel record names one."""
        from departments.models import Department
        from personnel.models import Personnel

        name = (self.assignee or "").strip()
        if not name:
            return None
        written = Personnel.objects.filter(name=name).exclude(department="").values_list("department", flat=True).first()
        return Department.objects.filter(name__iexact=written).values_list("pk", flat=True).first() if written else None

    def save(self, *args, **kwargs):
        """Stamp the closed date when the status flips to Completed, clear it on reopening.
        Only on a change, so editing an old completed action doesn't invent a closed date."""
        if self.department_id is None and self._state.adding:
            self.department_id = self.derive_department_id()
        if self.status != self._loaded_status or (self._state.adding and self.status == "Completed"):
            self.closed_date = timezone.localdate() if self.status == "Completed" else None
            fields = kwargs.get("update_fields")
            if fields is not None:
                kwargs["update_fields"] = set(fields) | {"closed_date"}
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    @property
    def days_to_close(self):
        if self.raised_date and self.closed_date and not self.is_open:
            return max((self.closed_date - self.raised_date).days, 0)
        return None

    @property
    def age_days(self):
        """How long it has been open (open actions with a known raised date)."""
        if self.raised_date and self.is_open:
            return max((timezone.localdate() - self.raised_date).days, 0)
        return None

    def __str__(self):
        return self.action_item

    @property
    def is_open(self):
        return self.status != "Completed"

    @property
    def days_left(self):
        return (self.due_date - timezone.localdate()).days if self.due_date else None

    @property
    def is_overdue(self):
        return self.is_open and self.due_date is not None and self.due_date < timezone.localdate()

    @property
    def due_state(self):
        """CSS class for the due-date pill."""
        if not self.is_open:
            return "valid"
        if self.days_left is None:
            return ""
        if self.days_left < 0:
            return "expired"
        return "soon" if self.days_left <= 3 else ""

    def add_note(self, text):
        """Append a dated line to the notes, e.g. '[11 Oct 2026 14:32] Step closed: ...'."""
        stamp = timezone.localtime().strftime("%d %b %Y %H:%M")
        line = f"[{stamp}] {text}"
        self.comments = f"{self.comments.rstrip()}\n{line}" if self.comments.strip() else line
        self.save(update_fields=["comments"])

    def steps_progress(self):
        total = self.steps.count()
        done = self.steps.filter(is_completed=True).count()
        return done, total

    def sync_status_from_steps(self):
        """With a checklist, the status follows it: none ticked = Pending,
        all ticked = Completed, otherwise In Progress."""
        done, total = self.steps_progress()
        if total:
            self.status = "Pending" if done == 0 else "Completed" if done == total else "In Progress"
            self.save(update_fields=["status"])

    @staticmethod
    def urgency_key(action):
        """Open actions first, most urgent at the top: overdue, then by due
        date (undated last), then by priority. Completed ones go last."""
        return (
            not action.is_open,
            action.due_date is None,
            action.due_date or datetime.date.max,
            PRIORITY_RANK.get(action.priority, 1),
            action.pk,
        )


class ActionStep(models.Model):
    action = models.ForeignKey(Action, on_delete=models.CASCADE, related_name="steps")
    step_description = models.CharField(max_length=255)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                     on_delete=models.SET_NULL, related_name="closed_steps")

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.step_description

    def close(self, user=None):
        """Tick the step: record when and by whom, and write it into the action's notes."""
        if self.is_completed:
            return
        self.is_completed = True
        self.completed_at = timezone.now()
        self.completed_by = user if getattr(user, "pk", None) else None
        self.save()
        who = f" by {user.username}" if getattr(user, "pk", None) else ""
        self.action.add_note(f"Step closed: {self.step_description}{who}")

    def reopen(self, user=None):
        if not self.is_completed:
            return
        self.is_completed, self.completed_at, self.completed_by = False, None, None
        self.save()
        who = f" by {user.username}" if getattr(user, "pk", None) else ""
        self.action.add_note(f"Step reopened: {self.step_description}{who}")
