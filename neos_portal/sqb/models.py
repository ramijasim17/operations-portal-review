from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models


class SQBJob(models.Model):
    job_number = models.CharField(max_length=100, unique=True)
    job_name = models.CharField(max_length=255, blank=True, default="")
    created_date = models.DateField(null=True, blank=True)
    permit_number = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        ordering = ["-created_date"]

    def __str__(self):
        return f"{self.job_number} \u2014 {self.job_name}"


class SQBDocument(models.Model):
    job = models.ForeignKey(SQBJob, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=150)
    file = models.FileField(upload_to="sqb_docs/", blank=True, null=True)
    notes = models.TextField(blank=True, default="")
    uploaded_date = models.DateField(auto_now_add=True)

    def __str__(self):
        return f"{self.job.job_number} \u00b7 {self.doc_type}"


NPT_CATEGORY_CHOICES = [
    ("equipment", "Base equipment"),
    ("personnel", "Personnel"),
    ("documents", "Document approval"),
    ("other", "Other"),
]


class SQBTrackerEntry(models.Model):
    """One period of non-productive time (NPT) against a unit."""
    date = models.DateField(null=True, blank=True)
    unit = models.ForeignKey(
        "departments.Asset", null=True, blank=True, on_delete=models.SET_NULL, related_name="npt_entries",
        help_text="The unit (truck, skid, generator…) the time was lost on.",
    )
    job_report = models.ForeignKey(
        "job_reports.JobReport", null=True, blank=True, on_delete=models.SET_NULL, related_name="npt_entries",
        help_text="The job (from Job Reports) the NPT happened on. The client comes from its manifest.",
    )
    client = models.CharField(max_length=255, blank=True, default="")
    category = models.CharField(
        max_length=20, choices=NPT_CATEGORY_CHOICES, blank=True, default="",
        help_text="What caused it: base equipment, personnel, or document approval.",
    )
    job_ticket = models.CharField(max_length=100, blank=True, default="")
    npt_hours = models.FloatField(null=True, blank=True)
    description = models.TextField(blank=True, default="")
    rating = models.CharField(max_length=50, blank=True, default="")

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.job_ticket or self.unit or 'NPT'} \u2014 {self.date}"


class ClientNPTSetting(models.Model):
    """How NPT is worked out for one client: the basis that sets the hours NPT
    is measured against, and which units are on that client's contract."""
    MONTHLY_24, MONTHLY_12, CALLOUT = "monthly_24", "monthly_12", "callout"
    BASIS_CHOICES = [
        (MONTHLY_24, "Monthly \u2014 24 hours a day"),
        (MONTHLY_12, "Monthly \u2014 12 hours a day"),
        (CALLOUT, "Call-out \u2014 by the job's duration"),
    ]
    CALLOUT_HOURS_CHOICES = [(12, "12 hours a day"), (24, "24 hours a day")]

    client = models.CharField(max_length=255, unique=True)
    basis = models.CharField(max_length=12, choices=BASIS_CHOICES, default=MONTHLY_24)
    callout_hours = models.PositiveSmallIntegerField(
        choices=CALLOUT_HOURS_CHOICES, default=12,
        help_text="For call-outs: the hours counted for each day of the call-out.")
    units = models.ManyToManyField(
        "departments.Asset", blank=True, related_name="npt_clients",
        help_text="The units on this client's contract. Each has the full hours available every day.")

    class Meta:
        ordering = ["client"]

    def __str__(self):
        return f"{self.client} \u2014 {self.basis_text}"

    @property
    def hours_per_day(self):
        return {self.MONTHLY_24: 24, self.MONTHLY_12: 12}.get(self.basis, self.callout_hours)

    @property
    def basis_text(self):
        if self.basis == self.CALLOUT:
            return f"Call-out \u00b7 {self.callout_hours} h \u00d7 days"
        return dict(self.BASIS_CHOICES)[self.basis]


class NPTKPI(models.Model):
    """An NPT target. With no unit / client / cause chosen it covers all NPT;
    each one chosen narrows it. The target is either a number of hours a month
    or a percentage of the hours available (see ClientNPTSetting)."""
    HOURS, PERCENT = "hours", "percent"
    METRIC_CHOICES = [(HOURS, "Hours per month"), (PERCENT, "% of available hours")]

    name = models.CharField(max_length=120)
    metric = models.CharField(max_length=10, choices=METRIC_CHOICES, default=HOURS)
    target = models.DecimalField(
        max_digits=7, decimal_places=2,
        help_text="The most NPT allowed: hours in a month, or % of the hours available.")
    unit = models.ForeignKey(
        "departments.Asset", null=True, blank=True, on_delete=models.CASCADE, related_name="npt_kpis")
    client = models.CharField(max_length=255, blank=True, default="")
    category = models.CharField(max_length=20, choices=NPT_CATEGORY_CHOICES, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} (\u2264 {self.target_text})"

    @property
    def is_percent(self):
        return self.metric == self.PERCENT

    @property
    def target_text(self):
        value = f"{self.target:f}".rstrip("0").rstrip(".") if "." in f"{self.target:f}" else f"{self.target}"
        return f"{value}%" if self.is_percent else f"{value} h / month"

    @property
    def scope_text(self):
        parts = []
        if self.unit_id:
            parts.append(f"unit {self.unit.name}")
        if self.client:
            parts.append(f"client {self.client}")
        if self.category:
            parts.append(self.get_category_display().lower())
        return ", ".join(parts) if parts else "all NPT"


# ── New job-linked SQB package workflow ──────────────────────────────────
# Every job (Job Reports) that doesn't have an SQBPackage yet shows up
# as "Missing SQB". Creating one picks a Type (e.g. "Client A"), which brings
# in that type's own list of required Parts (e.g. JSA, Risk Assessment).
# Each part gets a PDF uploaded individually; once every part has a file
# and a supervisor closes the package, all parts are merged into one PDF.

class SQBType(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SQBPartDefinition(models.Model):
    sqb_type = models.ForeignKey(SQBType, on_delete=models.CASCADE, related_name="parts")
    name = models.CharField(max_length=150)
    order = models.PositiveIntegerField(default=0)
    digital_form_key = models.CharField(
        max_length=50, blank=True, default="",
        help_text="If set, the supervisor fills this in on screen instead of uploading a scanned copy \u2014 see sqb/digital_forms.py",
    )

    class Meta:
        ordering = ["order", "id"]
        unique_together = ("sqb_type", "name")

    def __str__(self):
        return f"{self.sqb_type.name} \u00b7 {self.name}"


class SQBPackage(models.Model):
    STATUS_CHOICES = [("Open", "Open"), ("Closed", "Closed")]

    job_name = models.CharField(max_length=255, help_text="Label of the job this SQB belongs to")
    job_report = models.ForeignKey(
        "job_reports.JobReport", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="sqb_packages",
        help_text="The job (Job Reports) this SQB was created for. Empty on SQBs made before jobs were linked.",
    )
    sqb_type = models.ForeignKey(SQBType, on_delete=models.PROTECT, related_name="packages")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Open")
    created_date = models.DateField(auto_now_add=True)
    closed_date = models.DateField(null=True, blank=True)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sqb_packages_closed",
    )
    combined_file = models.FileField(upload_to="sqb_combined/", blank=True, null=True)

    class Meta:
        unique_together = ("job_name", "sqb_type")
        ordering = ["-created_date"]

    def __str__(self):
        return f"{self.job_name} \u2014 {self.sqb_type.name}"

    def all_parts_uploaded(self):
        return self.parts.filter(file="").count() == 0 and self.parts.exists()


class SQBPartUpload(models.Model):
    package = models.ForeignKey(SQBPackage, on_delete=models.CASCADE, related_name="parts")
    part_definition = models.ForeignKey(SQBPartDefinition, on_delete=models.CASCADE)
    file = models.FileField(
        upload_to="sqb_parts/", blank=True, null=True,
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
    )
    uploaded_date = models.DateField(null=True, blank=True)

    class Meta:
        unique_together = ("package", "part_definition")
        ordering = ["part_definition__order"]

    def __str__(self):
        return f"{self.package} \u00b7 {self.part_definition.name}"


class DigitalFormEntry(models.Model):
    """The supervisor's filled-in answers for a digitized checklist \u2014
    stored as structured data so the form can be re-opened/edited, and
    used to generate the completed PDF that becomes this part's file."""
    part_upload = models.OneToOneField(SQBPartUpload, on_delete=models.CASCADE, related_name="digital_form")
    form_key = models.CharField(max_length=50)
    header_data = models.JSONField(default=dict, blank=True)
    items_data = models.JSONField(default=dict, blank=True)
    comments = models.TextField(blank=True, default="")
    filled_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.part_upload} \u00b7 {self.form_key}"
