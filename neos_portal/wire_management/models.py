from django.db import models
from django.db.models import Sum


class Client(models.Model):
    """Managed list so Client is picked from a dropdown instead of typed
    freely -- add new ones from the Wire Management page as needed."""
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class FieldWell(models.Model):
    """Managed list for the Field/Well dropdown, same pattern as Client."""
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Location(models.Model):
    """Managed list of job locations, shared by every page that asks for a
    location (Job Manager, ...) -- same pattern as Client and FieldWell."""
    name = models.CharField(max_length=150, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class WireDrum(models.Model):
    """One physical spool of slickline wire, matching the identity fields
    at the top of the Slickline Wire Log Book (NE-SL-SOP-10)."""
    doc_code = models.CharField(max_length=50, blank=True, default="")
    wire_number = models.CharField(max_length=50, help_text="e.g. #1")
    diameter = models.CharField(max_length=50, help_text='Wire diameter, e.g. 0.108 in')
    manufacturer = models.CharField(max_length=150, blank=True, default="")
    total_length_ft = models.DecimalField(max_digits=10, decimal_places=1,
                                           help_text="Original/full length of wire on this drum, in feet")
    asset_serial_number = models.CharField(max_length=100, blank=True, default="")
    drum_number = models.CharField(max_length=50, unique=True)
    grade = models.CharField(max_length=100, blank=True, default="")
    purchase_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["drum_number"]

    def __str__(self):
        return f"{self.drum_number} ({self.wire_number})"

    @property
    def total_cut_ft(self):
        return self.log_entries.aggregate(total=Sum("cut_length_ft"))["total"] or 0

    @property
    def remaining_length_ft(self):
        return self.total_length_ft - self.total_cut_ft

    @property
    def percent_remaining(self):
        if not self.total_length_ft:
            return 0
        return round((self.remaining_length_ft / self.total_length_ft) * 100)


class WireLogEntry(models.Model):
    """One row of the Wire Log Book -- one per job the wire was used on.
    A drum can accumulate 20+ of these over its life. Client, Field/Well
    and Supervisor are all dropdowns rather than free text: Client and
    Field/Well from their own managed lists, Supervisor straight from
    the Personnel app so it's always a real, current employee."""
    drum = models.ForeignKey(WireDrum, on_delete=models.CASCADE, related_name="log_entries")
    date = models.DateField()
    client = models.ForeignKey(Client, on_delete=models.PROTECT, null=True, blank=True, related_name="wire_log_entries")
    field_well = models.ForeignKey(FieldWell, on_delete=models.PROTECT, null=True, blank=True, related_name="wire_log_entries")
    sour_environment = models.CharField(max_length=50, blank=True, default="", help_text="PPM or %")
    wire_test_type = models.CharField(max_length=100, blank=True, default="")
    wire_test_results = models.CharField(max_length=150, blank=True, default="")
    max_depth = models.CharField(max_length=50, blank=True, default="")
    max_tension = models.CharField(max_length=50, blank=True, default="")
    jarring_event_depth = models.CharField(max_length=50, blank=True, default="")
    jarring_event_tension = models.CharField(max_length=50, blank=True, default="")
    jarring_event_duration = models.CharField(max_length=50, blank=True, default="")
    run_number = models.CharField(max_length=50, blank=True, default="")
    cut_length_ft = models.DecimalField(max_digits=10, decimal_places=1, default=0,
                                         help_text="Length cut off the working end after this job, if any")
    remarks = models.TextField(blank=True, default="")
    supervisor = models.ForeignKey(
        "personnel.Personnel", on_delete=models.PROTECT, null=True, blank=True,
        related_name="wire_log_entries",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.drum.drum_number} \u2014 {self.date} \u2014 Run {self.run_number or '\u2014'}"

    @property
    def remaining_length_ft(self):
        """Remaining length AS OF this entry -- total length minus every
        cut recorded on or before this entry's date, so the log reads
        correctly even if entries are added out of chronological order
        (e.g. backfilling old jobs)."""
        cuts_to_date = self.drum.log_entries.filter(date__lte=self.date).aggregate(
            total=Sum("cut_length_ft"))["total"] or 0
        return self.drum.total_length_ft - cuts_to_date


class WireLogEvidence(models.Model):
    """A job entry can have several pieces of evidence (a wire test
    photo, a job report, ...), each stored and downloadable on its own,
    and combinable into one PDF via the drum's export."""
    log_entry = models.ForeignKey(WireLogEntry, on_delete=models.CASCADE, related_name="evidence_files")
    file = models.FileField(upload_to="wire_management/evidence/")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Evidence for {self.log_entry}"
