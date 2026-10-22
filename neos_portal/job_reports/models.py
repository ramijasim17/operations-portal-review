import datetime

from django.core.exceptions import ValidationError
from django.db import models

from job_manager.models import JobManifest

DOCUMENT_TYPES = [
    ("daily_report", "Daily Report"),
    ("job_program", "Job Program"),
    ("calculation_report", "Calculation Report"),
    ("other", "Other"),
]

ALLOWED_DOCUMENT_EXTENSIONS = (".pdf", ".xlsx", ".xls", ".doc", ".docx")


def validate_document_extension(value):
    """Restricts uploads to PDF, Excel, or Word files, matching what
    was explicitly asked for."""
    name = str(value).lower()
    if not name.endswith(ALLOWED_DOCUMENT_EXTENSIONS):
        raise ValidationError(
            "Only PDF, Excel (.xlsx/.xls), or Word (.doc/.docx) files are allowed."
        )


SURVEY_REPORT_CHOICES = [("SGS", "SGS"), ("FGS", "FGS"), ("PBU", "PBU")]


class JobReport(models.Model):
    manifest = models.ForeignKey(
        JobManifest, on_delete=models.PROTECT, related_name="job_reports",
        help_text="Which manifest (by job number) this job belongs to.",
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    needs_survey_report = models.BooleanField(
        default=False, verbose_name="Needs an SGS / FGS / PBU report",
    )
    survey_report_type = models.CharField(
        max_length=10, choices=SURVEY_REPORT_CHOICES, blank=True,
        help_text="Required if 'Needs an SGS / FGS / PBU report' is checked.",
    )
    linked_sgs_report = models.ForeignKey(
        "sgs_report.SGSReport", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="job_reports",
    )
    callout_start = models.DateField(
        null=True, blank=True, verbose_name="Call-out starts",
        help_text="For call-out work: the first day. Used to work out the hours NPT is measured against.")
    callout_end = models.DateField(null=True, blank=True, verbose_name="Call-out ends")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def _assignments(self):
        return list(self.assignments.all())      # prefetch-friendly: callers prefetch assignments__person

    @property
    def supervisors(self):
        """Who is on the job today: [(shift label, person)], one entry per current assignment."""
        today = datetime.date.today()
        return [(a.get_shift_display(), a.person) for a in self._assignments() if a.is_current(today)]

    @property
    def former_supervisors(self):
        """Shown when nobody is on the job right now: the people who were."""
        today = datetime.date.today()
        rows = self._assignments()
        if any(a.is_current(today) for a in rows):
            return []
        return [(a.get_shift_display(), a.person) for a in rows if a.end_date and a.end_date < today]

    @property
    def callout_days(self):
        """Whole days of the call-out, counting both the first and the last day."""
        if self.callout_start and self.callout_end and self.callout_end >= self.callout_start:
            return (self.callout_end - self.callout_start).days + 1
        return None

    def __str__(self):
        return f"{self.name} ({self.manifest.job_number})"


class JobReportDocument(models.Model):
    job_report = models.ForeignKey(JobReport, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=30, choices=DOCUMENT_TYPES, default="other")
    title = models.CharField(max_length=255, blank=True, default="")
    file = models.FileField(upload_to="job_report_documents/", validators=[validate_document_extension])
    notes = models.TextField(blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.title or self.file.name


SHIFT_CHOICES = [("day", "Day shift"), ("night", "Night shift"), ("both", "Day & night")]


class JobSupervisor(models.Model):
    """A supervisor on a job for a stretch of days. A long job can change hands: one row per
    person per shift, with the dates they covered (no end date = still on it)."""
    job_report = models.ForeignKey(JobReport, on_delete=models.CASCADE, related_name="assignments")
    person = models.ForeignKey("personnel.Personnel", on_delete=models.CASCADE, related_name="job_assignments")
    shift = models.CharField(max_length=5, choices=SHIFT_CHOICES, default="day")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True, help_text="Leave empty while they are still on the job.")

    class Meta:
        ordering = ["start_date", "shift", "id"]

    def __str__(self):
        return f"{self.person} ({self.get_shift_display()}) on {self.job_report}"

    @property
    def days(self):
        """Days covered, counting the first and the last (only known once they have left)."""
        return (self.end_date - self.start_date).days + 1 if self.end_date else None

    def is_current(self, today=None):
        today = today or datetime.date.today()
        return self.start_date <= today and (self.end_date is None or self.end_date >= today)

    def overlaps(self, other):
        """Same stretch of days and shifts that cover the same hours."""
        clash_shift = self.shift == other.shift or "both" in (self.shift, other.shift)
        far = datetime.date.max
        return clash_shift and self.start_date <= (other.end_date or far) and other.start_date <= (self.end_date or far)
