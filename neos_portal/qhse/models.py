import datetime

from django.conf import settings
from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.utils import timezone

SEVERITY_CHOICES = [("Low", "Low"), ("Medium", "Medium"), ("High", "High"), ("Critical", "Critical")]
SEVERITY_RANK = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}


def _pill(severity):
    """CSS class of the coloured pill used across the portal."""
    return {"Critical": "expired", "High": "expired", "Medium": "soon"}.get(severity, "")


def _asset_department_id(serial):
    """The department an Asset No / Serial No belongs to (its classification's department), or None."""
    if not serial:
        return None
    from departments.models import Asset

    return Asset.objects.filter(name=serial).values_list("classification__department_id", flat=True).first()


def _next_reference(model, prefix, year):
    """INC-2026-001, INC-2026-002 ... numbering restarts each year."""
    stem = f"{prefix}-{year}-"
    last = (model.objects.filter(reference__startswith=stem).order_by("-reference")
            .values_list("reference", flat=True).first())
    n = int(last.rsplit("-", 1)[1]) + 1 if last and last.rsplit("-", 1)[1].isdigit() else 1
    return f"{stem}{n:03d}"


class Incident(models.Model):
    INCIDENT, NEAR_MISS, UNSAFE, ENVIRONMENTAL, QUALITY, INJURY = "incident", "near_miss", "unsafe", "environmental", "quality", "injury"
    TYPE_CHOICES = [
        (INCIDENT, "Incident"), (NEAR_MISS, "Near miss"), (UNSAFE, "Unsafe act / condition"),
        (ENVIRONMENTAL, "Environmental / spill"), (QUALITY, "Quality issue"), (INJURY, "Injury / first aid"),
    ]
    OPEN, INVESTIGATING, CLOSED = "Open", "Investigating", "Closed"
    STATUS_CHOICES = [(OPEN, "Open"), (INVESTIGATING, "Investigating"), (CLOSED, "Closed")]

    reference = models.CharField(max_length=20, unique=True, blank=True)
    department = models.ForeignKey("departments.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   help_text="Which department this belongs to. Taken from the equipment when you name one.")
    date = models.DateField(default=datetime.date.today)
    time = models.TimeField(null=True, blank=True)
    incident_type = models.CharField(max_length=20, choices=TYPE_CHOICES, default=INCIDENT)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="Low")
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    location = models.CharField(max_length=200, blank=True, default="", help_text="Well, rig, base or unit")
    asset_serial = models.CharField(max_length=150, blank=True, default="", db_index=True,
                                    help_text="Asset No / Serial No in Departments, when equipment was involved")
    client = models.CharField(max_length=255, blank=True, default="")
    people_involved = models.TextField(blank=True, default="")
    immediate_action = models.TextField(blank=True, default="", help_text="What was done straight away")
    root_cause = models.TextField(blank=True, default="")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=OPEN)
    assigned_to = models.CharField(max_length=255, blank=True, default="", help_text="Who investigates it")
    reported_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qhse_incidents")
    closed_date = models.DateField(null=True, blank=True)
    file = models.FileField(upload_to="qhse/incidents/", blank=True, null=True)
    actions = models.ManyToManyField("action_tracker.Action", blank=True, related_name="qhse_incidents")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded_status = self.status

    def __str__(self):
        return f"{self.reference} {self.title}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = _next_reference(Incident, "INC", (self.date or timezone.localdate()).year)
        if self.department_id is None:
            self.department_id = _asset_department_id(self.asset_serial)
        if self.status != self._loaded_status or (self._state.adding and self.status == self.CLOSED):
            self.closed_date = timezone.localdate() if self.status == self.CLOSED else None
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    @property
    def is_open(self):
        return self.status != self.CLOSED

    @property
    def age_days(self):
        end = self.closed_date or timezone.localdate()
        return max((end - self.date).days, 0)

    @property
    def severity_pill(self):
        return _pill(self.severity)

    @property
    def open_actions(self):
        return [a for a in self.actions.all() if a.is_open]


class Inspection(models.Model):
    INSPECTION, AUDIT, DRILL, TOOLBOX = "inspection", "audit", "drill", "toolbox"
    KIND_CHOICES = [(INSPECTION, "Inspection"), (AUDIT, "Audit"), (DRILL, "Drill"), (TOOLBOX, "Toolbox talk / HSE meeting")]
    PASS, IMPROVE, FAIL = "Pass", "Needs improvement", "Fail"
    RESULT_CHOICES = [(PASS, "Pass"), (IMPROVE, "Needs improvement"), (FAIL, "Fail")]

    INTERNAL, EXTERNAL = "internal", "external"
    SCOPE_CHOICES = [(INTERNAL, "Internal"), (EXTERNAL, "External")]

    reference = models.CharField(max_length=20, unique=True, blank=True)
    department = models.ForeignKey("departments.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   help_text="Which department this belongs to. Taken from the equipment when you name one.")
    title = models.CharField(max_length=200)
    kind = models.CharField(max_length=15, choices=KIND_CHOICES, default=INSPECTION)
    template = models.ForeignKey("qhse.AuditTemplate", null=True, blank=True, on_delete=models.SET_NULL, related_name="inspections",
                                 help_text="The checklist to work through. Leave empty for a simple inspection with no checklist.")
    scope = models.CharField("Internal / external", max_length=10, choices=SCOPE_CHOICES, default=INTERNAL)
    auditor_company = models.CharField("External auditor / company", max_length=200, blank=True, default="")
    score = models.PositiveSmallIntegerField(null=True, blank=True, help_text="% of the checked points that were compliant")
    asset_serial = models.CharField(max_length=150, blank=True, default="", db_index=True,
                                    help_text="Asset No / Serial No in Departments, when a piece of equipment is inspected")
    area = models.CharField("Unit / location", max_length=200, blank=True, default="")
    inspector = models.CharField("Done by", max_length=255, blank=True, default="")
    due_date = models.DateField(null=True, blank=True)
    done_date = models.DateField(null=True, blank=True)
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, blank=True, default="")
    findings = models.TextField(blank=True, default="")
    repeat_months = models.PositiveSmallIntegerField("Repeat every (months)", null=True, blank=True,
                                                     help_text="Leave empty for a one-off. When it is done, the next one is scheduled.")
    file = models.FileField(upload_to="qhse/inspections/", blank=True, null=True)
    actions = models.ManyToManyField("action_tracker.Action", blank=True, related_name="qhse_inspections")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-done_date", "due_date", "-id"]

    def __str__(self):
        return f"{self.reference} {self.title}"

    def save(self, *args, **kwargs):
        if not self.reference:
            year = (self.due_date or self.done_date or timezone.localdate()).year
            self.reference = _next_reference(Inspection, "INS", year)
        if self.department_id is None:
            self.department_id = _asset_department_id(self.asset_serial) or (self.template.department_id if self.template_id else None)
        super().save(*args, **kwargs)

    @property
    def is_done(self):
        return self.done_date is not None

    @property
    def days_left(self):
        return (self.due_date - timezone.localdate()).days if self.due_date and not self.is_done else None

    @property
    def status(self):
        if self.is_done:
            return "done"
        if self.days_left is None:
            return "unscheduled"
        if self.days_left < 0:
            return "overdue"
        return "soon" if self.days_left <= 7 else "scheduled"

    @property
    def status_pill(self):
        return {"done": "valid", "overdue": "expired", "soon": "soon"}.get(self.status, "")

    @property
    def status_text(self):
        d = self.days_left
        if self.status == "done":
            return "Done"
        if self.status == "overdue":
            return f"{-d} day{'s' if d != -1 else ''} overdue"
        if self.status == "soon":
            return "today" if d == 0 else f"in {d} day{'s' if d != 1 else ''}"
        return "Scheduled" if self.due_date else "No date"

    @property
    def result_pill(self):
        return {"Pass": "valid", "Needs improvement": "soon", "Fail": "expired"}.get(self.result, "")

    @property
    def open_actions(self):
        return [a for a in self.actions.all() if a.is_open]

    def complete(self, done_on, result="", findings="", inspector="", file=None):
        """Record it as done. A repeating one schedules its successor."""
        self.done_date, self.result = done_on, result
        if findings:
            self.findings = findings
        if inspector:
            self.inspector = inspector
        if file:
            self.file = file
        self.save()
        if self.repeat_months:
            month = done_on.month - 1 + self.repeat_months
            year, month = done_on.year + month // 12, month % 12 + 1
            day = min(done_on.day, [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
            return Inspection.objects.create(
                title=self.title, kind=self.kind, area=self.area, inspector=self.inspector,
                due_date=datetime.date(year, month, day), repeat_months=self.repeat_months)
        return None


def limit_text(answer_type, unit, min_value, max_value):
    """'3000 to 3300 psi', 'at least 2.5 mm' ... for a measured point."""
    if answer_type != "number":
        return ""

    def num(v):
        if v is None:
            return ""
        text = format(v.normalize(), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text

    lo, hi = num(min_value), num(max_value)
    rng = f"{lo} to {hi}" if lo and hi else (f"at least {lo}" if lo else (f"at most {hi}" if hi else ""))
    return f"{rng} {unit}".strip()


class AuditTemplate(models.Model):
    """A reusable checklist: parts (sections), each with the points to audit or inspect."""
    name = models.CharField(max_length=200, unique=True)
    department = models.ForeignKey("departments.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   help_text="Leave empty if every department can use this template.")
    kind = models.CharField(max_length=15, choices=Inspection.KIND_CHOICES, default=Inspection.AUDIT)
    description = models.TextField(blank=True, default="")
    pass_mark = models.PositiveSmallIntegerField(default=80, help_text="% of points that must be compliant to pass (a critical point that fails is always a Fail).")
    is_active = models.BooleanField(default=True, help_text="Untick to stop offering it for new audits.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def point_count(self):
        return AuditPoint.objects.filter(part__template=self).count()


class AuditPart(models.Model):
    template = models.ForeignKey(AuditTemplate, on_delete=models.CASCADE, related_name="parts")
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class AuditPoint(models.Model):
    """One thing to check. It is either answered Compliant / Non-compliant / N/A, or measured against a range."""
    OK, NUMBER = "ok", "number"
    TYPE_CHOICES = [(OK, "Compliant / Non-compliant / N/A"), (NUMBER, "A measured value with a limit")]

    part = models.ForeignKey(AuditPart, on_delete=models.CASCADE, related_name="points")
    text = models.CharField("Point to check", max_length=300)
    guidance = models.TextField("What to look for", blank=True, default="", help_text="The requirement or standard, shown to the person doing the check.")
    answer_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=OK)
    unit = models.CharField(max_length=30, blank=True, default="", help_text="For a measured value, e.g. psi, mm, bar.")
    min_value = models.DecimalField("Lowest allowed", max_digits=12, decimal_places=3, null=True, blank=True)
    max_value = models.DecimalField("Highest allowed", max_digits=12, decimal_places=3, null=True, blank=True)
    critical = models.BooleanField(default=False, help_text="If this fails, the whole audit fails.")
    note_required = models.BooleanField("Reason needed if it fails", default=True,
                                        help_text="The person must write what was wrong before they can finish.")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.text

    @property
    def limit_text(self):
        return limit_text(self.answer_type, self.unit, self.min_value, self.max_value)


class InspectionAnswer(models.Model):
    """The result for one point of one audit. The point's wording is copied here, so changing the template later
    never rewrites an audit that was already done."""
    COMPLIANT, NON_COMPLIANT, NA = "compliant", "non_compliant", "na"
    OUTCOME_CHOICES = [(COMPLIANT, "Compliant"), (NON_COMPLIANT, "Non-compliant"), (NA, "N/A")]

    inspection = models.ForeignKey(Inspection, on_delete=models.CASCADE, related_name="answers")
    point = models.ForeignKey(AuditPoint, null=True, blank=True, on_delete=models.SET_NULL, related_name="answers")
    part_title = models.CharField(max_length=200)
    point_text = models.CharField(max_length=300)
    guidance = models.TextField(blank=True, default="")
    answer_type = models.CharField(max_length=10, default=AuditPoint.OK)
    unit = models.CharField(max_length=30, blank=True, default="")
    min_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    max_value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    critical = models.BooleanField(default=False)
    note_required = models.BooleanField(default=True)
    outcome = models.CharField(max_length=15, choices=OUTCOME_CHOICES, blank=True, default="")
    value = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    file = models.FileField(upload_to="qhse/audit_points/", blank=True, null=True)
    action = models.ForeignKey("action_tracker.Action", null=True, blank=True, on_delete=models.SET_NULL, related_name="qhse_answers")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    @property
    def limit_text(self):
        return limit_text(self.answer_type, self.unit, self.min_value, self.max_value)

    @property
    def outcome_pill(self):
        return {"compliant": "valid", "non_compliant": "expired"}.get(self.outcome, "")

    def judge_value(self):
        """For a measured point: inside the limits is compliant."""
        if self.answer_type != AuditPoint.NUMBER or self.value is None:
            return self.outcome
        low_ok = self.min_value is None or self.value >= self.min_value
        high_ok = self.max_value is None or self.value <= self.max_value
        return self.COMPLIANT if low_ok and high_ok else self.NON_COMPLIANT


class Investigation(models.Model):
    """Looking into why something failed or ran late: what happened, the cause, and what will be done about it."""
    FAILED_TEST, FAILURE, OVERDUE_JOB, OVERDUE_ACTION, INCIDENT, NPT, OTHER = (
        "failed_test", "failure", "overdue_job", "overdue_action", "incident", "npt", "other")
    TRIGGER_CHOICES = [
        (FAILED_TEST, "Failed test"), (FAILURE, "Equipment failure"), (OVERDUE_JOB, "Overdue maintenance"),
        (OVERDUE_ACTION, "Overdue action"), (INCIDENT, "Incident"), (NPT, "Non-productive time (NPT)"), (OTHER, "Other"),
    ]
    OPEN, INVESTIGATING, CLOSED = "Open", "Investigating", "Closed"
    STATUS_CHOICES = [(OPEN, "Open"), (INVESTIGATING, "Investigating"), (CLOSED, "Closed")]

    reference = models.CharField(max_length=20, unique=True, blank=True)
    department = models.ForeignKey("departments.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   help_text="Which department this belongs to. Taken from the equipment when you name one.")
    title = models.CharField(max_length=200)
    trigger = models.CharField(max_length=20, choices=TRIGGER_CHOICES, default=OTHER)
    maintenance_record = models.ForeignKey("maintenance.MaintenanceRecord", null=True, blank=True, on_delete=models.SET_NULL, related_name="investigations")
    action = models.ForeignKey("action_tracker.Action", null=True, blank=True, on_delete=models.SET_NULL, related_name="investigations")
    incident = models.ForeignKey(Incident, null=True, blank=True, on_delete=models.SET_NULL, related_name="investigations")
    npt_entry = models.ForeignKey("sqb.SQBTrackerEntry", null=True, blank=True, on_delete=models.SET_NULL, related_name="investigations")
    asset_serial = models.CharField(max_length=150, blank=True, default="", db_index=True)
    opened_date = models.DateField(default=datetime.date.today)
    what_happened = models.TextField(blank=True, default="")
    findings = models.TextField(blank=True, default="")
    root_cause = models.TextField(blank=True, default="")
    owner = models.CharField("Investigated by", max_length=255, blank=True, default="")
    due_date = models.DateField("Finish by", null=True, blank=True)
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default=OPEN)
    closed_date = models.DateField(null=True, blank=True)
    actions = models.ManyToManyField("action_tracker.Action", blank=True, related_name="qhse_investigations")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qhse_investigations")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-opened_date", "-id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._loaded_status = self.status

    def __str__(self):
        return f"{self.reference} {self.title}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = _next_reference(Investigation, "INV", (self.opened_date or timezone.localdate()).year)
        if self.department_id is None:
            self.department_id = _asset_department_id(self.asset_serial) or (self.incident.department_id if self.incident_id else None)
        if self.status != self._loaded_status or (self._state.adding and self.status == self.CLOSED):
            self.closed_date = timezone.localdate() if self.status == self.CLOSED else None
        super().save(*args, **kwargs)
        self._loaded_status = self.status

    @property
    def is_open(self):
        return self.status != self.CLOSED

    @property
    def is_overdue(self):
        return self.is_open and self.due_date is not None and self.due_date < timezone.localdate()

    @property
    def age_days(self):
        return max(((self.closed_date or timezone.localdate()) - self.opened_date).days, 0)

    @property
    def open_actions(self):
        return [a for a in self.actions.all() if a.is_open]


class SiteVisit(models.Model):
    """A report on a visit to a site. It is a draft until submitted; a Level 3 approves it, and then it is locked."""
    reference = models.CharField(max_length=20, unique=True, blank=True)
    department = models.ForeignKey("departments.Department", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
                                   help_text="Which department this belongs to. Taken from the equipment when you name one.")
    date = models.DateField(default=datetime.date.today)
    site = models.CharField("Site / location", max_length=200)
    client = models.CharField(max_length=255, blank=True, default="")
    purpose = models.CharField("Purpose of the visit", max_length=300, blank=True, default="")
    visited_by = models.CharField("Visited by", max_length=255, blank=True, default="")
    attendees = models.TextField("Others present", blank=True, default="")
    summary = models.TextField(blank=True, default="")
    next_steps = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="qhse_site_visits")
    created_at = models.DateTimeField(auto_now_add=True)
    approvals = GenericRelation("approvals.Approval")

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.reference} {self.site}"

    def save(self, *args, **kwargs):
        if not self.reference:
            self.reference = _next_reference(SiteVisit, "SV", (self.date or timezone.localdate()).year)
        super().save(*args, **kwargs)

    @property
    def state(self):
        """Draft until submitted, then Submitted / Approved / Rejected by its approval."""
        from approvals import services

        approval = services.get(self, "site_visit")
        if approval is None:
            return "Draft"
        return {"pending": "Submitted", "approved": "Approved", "rejected": "Rejected"}.get(approval.status, "Draft")

    @property
    def is_locked(self):
        return self.state in ("Submitted", "Approved")

    @property
    def state_pill(self):
        return {"Approved": "valid", "Submitted": "soon", "Rejected": "expired"}.get(self.state, "")


class SiteVisitItem(models.Model):
    GOOD, OBSERVATION, FINDING = "good", "observation", "finding"
    KIND_CHOICES = [(GOOD, "Good practice"), (OBSERVATION, "Observation"), (FINDING, "Finding (needs action)")]

    visit = models.ForeignKey(SiteVisit, on_delete=models.CASCADE, related_name="items")
    area = models.CharField("Area", max_length=200, blank=True, default="", help_text="e.g. Wellhead, Camp, Workshop")
    kind = models.CharField("Type", max_length=15, choices=KIND_CHOICES, default=OBSERVATION)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, blank=True, default="")
    text = models.TextField("What was seen")
    file = models.FileField("Photo", upload_to="qhse/visits/", blank=True, null=True)
    action = models.ForeignKey("action_tracker.Action", null=True, blank=True, on_delete=models.SET_NULL, related_name="qhse_visit_items")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    @property
    def kind_pill(self):
        return {"good": "valid", "finding": "expired"}.get(self.kind, "")
