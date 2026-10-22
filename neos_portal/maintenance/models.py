import calendar
import datetime

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.utils import timezone

from departments.models import Asset

UNIT_CHOICES = [("day", "day(s)"), ("week", "week(s)"), ("month", "month(s)"), ("year", "year(s)")]
UNIT_DAYS = {"day": 1, "week": 7, "month": 30.4375, "year": 365.25}
SOON_DAYS = 30


def add_months(start, months):
    """`start` plus whole calendar months, clamping the day (31 Jan + 1 month = 28/29 Feb)."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    return datetime.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def add_interval(start, times, count, unit):
    """The next date after `start` for "`times` times every `count` `unit`".

    once per N months/years steps by calendar months; anything else (twice a
    week, 3 times a month...) steps by an even number of days."""
    times, count = max(times or 1, 1), max(count or 1, 1)
    if times == 1 and unit in ("month", "year"):
        return add_months(start, count * (12 if unit == "year" else 1))
    return start + datetime.timedelta(days=max(round(count * UNIT_DAYS[unit] / times), 1))


def describe_interval(times, count, unit):
    """'Every 3 months', 'Every week', '2 times per week', '3 times every 2 months'."""
    times, count = times or 1, count or 1
    unit_word = unit if count == 1 else f"{unit}s"
    if times == 1:
        return f"Every {unit}" if count == 1 else f"Every {count} {unit_word}"
    return f"{times} times per {unit}" if count == 1 else f"{times} times every {count} {unit_word}"


class MaintenanceLevel(models.Model):
    level_name = models.CharField(max_length=100, unique=True)
    interval_count = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text="How often this level is done, e.g. 3 (with unit 'month') for a 3-monthly service.",
    )
    interval_unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default="month")

    class Meta:
        ordering = ["level_name"]

    def __str__(self):
        return self.level_name

    @property
    def interval_text(self):
        return describe_interval(1, self.interval_count, self.interval_unit) if self.interval_count else ""


class MaintenanceRecord(models.Model):
    MAINTENANCE, PRESSURE_TEST = "maintenance", "pressure_test"
    ACTIVITY_CHOICES = [(MAINTENANCE, "Maintenance"), (PRESSURE_TEST, "Pressure test")]

    LEVELLED, REPEATING, ONE_OFF, ON_FAILURE = "levelled", "repeating", "one_off", "on_failure"
    SCHEDULE_CHOICES = [
        (LEVELLED, "By level (Level 1 / 2 / 3 …)"),
        (REPEATING, "Repeats on a schedule (no level)"),
        (ONE_OFF, "Once, on a specific date"),
        (ON_FAILURE, "Only when it fails"),
    ]
    YARD, LOCATION = "yard", "location"
    PLACE_CHOICES = [(YARD, "In the yard / workshop"), (LOCATION, "At a location (on site)")]

    asset_serial = models.CharField(
        max_length=150, help_text="Must match an Asset No / Serial No in Departments"
    )
    activity = models.CharField(max_length=20, choices=ACTIVITY_CHOICES, default=MAINTENANCE)
    schedule = models.CharField(max_length=20, choices=SCHEDULE_CHOICES, default=ONE_OFF)
    maintenance_level = models.ForeignKey(MaintenanceLevel, on_delete=models.SET_NULL, null=True, blank=True)

    times_per = models.PositiveSmallIntegerField(
        default=1, help_text="How many times per period, e.g. 2 for 'twice per week'.")
    every_count = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="The period length, e.g. 1 week, 1 month, 6 months.")
    every_unit = models.CharField(max_length=10, choices=UNIT_CHOICES, default="month")

    place = models.CharField(max_length=10, choices=PLACE_CHOICES, default=YARD)
    location_name = models.CharField(max_length=150, blank=True, default="")

    due_date = models.DateField(null=True, blank=True)
    last_done = models.DateField(null=True, blank=True)
    last_result = models.CharField(max_length=10, blank=True, default="")   # "Pass" / "Fail" (pressure tests)
    is_active = models.BooleanField(default=True, help_text="Off once a one-off job has been done.")

    file = models.FileField(upload_to="maintenance_files/", blank=True, null=True)
    notes = models.TextField(blank=True, default="")
    uploaded_date = models.DateField(auto_now_add=True)
    actions = models.ManyToManyField("action_tracker.Action", blank=True, related_name="maintenance_records")

    class Meta:
        ordering = ["due_date"]

    def __str__(self):
        return f"{self.asset_serial} — {self.get_activity_display()} ({self.schedule_text})"

    def asset(self):
        return Asset.objects.filter(name=self.asset_serial).select_related(
            "classification", "classification__department"
        ).first()

    def raise_follow_up(self, reason, due_in_days=3):
        """A failure or a failed test needs fixing: put a High action in the Action Tracker, once.
        Returns the action, or None when one is already open for this job."""
        from action_tracker.models import Action

        if self.actions.exclude(status="Completed").exists():
            return None
        today = timezone.localdate()
        from core.dept_scope import asset_department_map

        action = Action.objects.create(
            department_id=asset_department_map([self.asset_serial]).get(self.asset_serial),
            action_item=f"{reason}: {self.asset_serial} ({self.get_activity_display().lower()})"[:255],
            priority="High", action_type="equipment", raised_date=today,
            due_date=today + datetime.timedelta(days=due_in_days),
            comments=f"Raised automatically from Maintenance for {self.asset_serial}.")
        self.actions.add(action)
        return action

    # ── schedule ─────────────────────────────────────────────────────────
    @property
    def interval(self):
        """(times, count, unit) that drives the repeat, or None if it doesn't repeat."""
        if self.schedule == self.REPEATING and self.every_count:
            return self.times_per, self.every_count, self.every_unit
        if self.schedule == self.LEVELLED:
            if self.every_count:                       # explicit override on this asset
                return self.times_per, self.every_count, self.every_unit
            level = self.maintenance_level
            if level and level.interval_count:
                return 1, level.interval_count, level.interval_unit
        return None

    @property
    def is_recurring(self):
        return self.interval is not None

    @property
    def schedule_text(self):
        if self.schedule == self.ONE_OFF:
            return f"Once, on {self.due_date:%d %b %Y}" if self.due_date else "Once"
        if self.schedule == self.ON_FAILURE:
            return "Only when it fails"
        text = describe_interval(*self.interval) if self.interval else "No interval set"
        if self.schedule == self.LEVELLED and self.maintenance_level:
            return f"{self.maintenance_level.level_name} · {text}"
        return text

    @property
    def place_text(self):
        if self.place == self.LOCATION:
            return f"At {self.location_name}" if self.location_name else "At location"
        return "Yard / workshop"

    def next_due_after(self, done_on):
        return add_interval(done_on, *self.interval) if self.interval else None

    # ── status ───────────────────────────────────────────────────────────
    @property
    def status(self):
        if not self.is_active:
            return "done"
        if not self.due_date:
            return "standby" if self.schedule == self.ON_FAILURE else "missing"
        today = timezone.localdate()
        if self.due_date < today:
            return "expired"
        if self.due_date <= today + datetime.timedelta(days=SOON_DAYS):
            return "soon"
        return "valid"

    @property
    def days_left(self):
        return (self.due_date - timezone.localdate()).days if self.due_date else None

    def save(self, *args, **kwargs):
        # A repeating job with no date yet is first due one period from today.
        if self.is_active and not self.due_date and self.is_recurring:
            self.due_date = self.next_due_after(timezone.localdate())
        super().save(*args, **kwargs)

    # ── events ───────────────────────────────────────────────────────────
    def complete(self, done_on, user=None, notes="", file=None, result=""):
        """Record the work as done. A Level 3 / admin (or a script) moves the schedule on at once; a Level 2's
        report waits for approval and the schedule moves when it is approved."""
        from approvals import services as approvals

        log = MaintenanceLog.objects.create(
            record=self, event=MaintenanceLog.DONE, done_date=done_on, result=result,
            notes=notes, file=file, by=user if getattr(user, "pk", None) else None,
        )
        if getattr(user, "pk", None) and approvals.needs_approval(user):
            approvals.submit(log, "maintenance_log", user)
            return log
        self.advance_after(done_on, result)
        if getattr(user, "pk", None):
            approvals.submit(log, "maintenance_log", user)         # a Level 3's own report: approved, and on record
        return log

    def advance_after(self, done_on, result=""):
        """Move the schedule on after a completed job."""
        self.last_done, self.last_result = done_on, result
        if self.is_recurring:
            self.due_date = self.next_due_after(done_on)
        elif self.schedule == self.ON_FAILURE:
            self.due_date = None                        # back to standby
        else:                                            # one-off: finished
            self.due_date, self.is_active = None, False
        self.save()

    def report_failure(self, on_date, user=None, notes=""):
        """A failure makes the job due immediately."""
        log = MaintenanceLog.objects.create(
            record=self, event=MaintenanceLog.FAILURE, done_date=on_date, notes=notes,
            by=user if getattr(user, "pk", None) else None,
        )
        self.due_date, self.is_active = on_date, True
        self.save()
        return log


class MaintenanceLog(models.Model):
    """History: every time a job was done or a failure was reported."""
    DONE, FAILURE = "done", "failure"
    EVENT_CHOICES = [(DONE, "Done"), (FAILURE, "Failure reported")]

    record = models.ForeignKey(MaintenanceRecord, on_delete=models.CASCADE, related_name="logs")
    event = models.CharField(max_length=10, choices=EVENT_CHOICES, default=DONE)
    done_date = models.DateField()
    result = models.CharField(max_length=10, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    file = models.FileField(upload_to="maintenance_files/", blank=True, null=True)
    by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                           related_name="maintenance_logs")
    created_at = models.DateTimeField(auto_now_add=True)

    approvals = GenericRelation("approvals.Approval")      # waiting / approved / rejected, see approvals/

    class Meta:
        ordering = ["-done_date", "-id"]

    def __str__(self):
        return f"{self.record.asset_serial}: {self.get_event_display()} {self.done_date}"

    def apply_to_schedule(self):
        """Called when a Level 3 approves a done-report: now the schedule moves on."""
        if self.event == self.DONE:
            self.record.advance_after(self.done_date, self.result)
