import datetime

from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.utils import timezone


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    location = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Classification(models.Model):
    department = models.ForeignKey(Department, on_delete=models.CASCADE, related_name="classifications")
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)
    is_unit = models.BooleanField(
        default=False, verbose_name="These are units",
        help_text="Tick if the assets here are units (trucks, skids, generators, test units). "
                  "They can then be chosen when logging NPT, and their NPT is tracked against them.",
    )

    class Meta:
        ordering = ["order", "name"]
        unique_together = ("department", "name")

    def __str__(self):
        return f"{self.department.name} / {self.name}"


class FieldDefinition(models.Model):
    TEXT = "text"
    NUMBER = "number"
    DROPDOWN = "dropdown"
    ATTACHMENT = "attachment"
    SEARCHABLE = "searchable"
    VALUE_UNIT = "value_unit"
    FIELD_TYPES = [
        (TEXT, "Text"),
        (NUMBER, "Number"),
        (DROPDOWN, "Dropdown"),
        (ATTACHMENT, "Attachment"),
        (SEARCHABLE, "Searchable / filterable"),
        (VALUE_UNIT, "Value with unit"),
    ]

    classification = models.ForeignKey(Classification, on_delete=models.CASCADE, related_name="fields")
    label = models.CharField(max_length=100)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES)
    dropdown_options = models.CharField(
        max_length=500, blank=True,
        help_text=(
            "Comma-separated options. Used as the choice list for Dropdown and "
            "Searchable fields, or as the unit choices (e.g. 'psi, bbl, ft') for "
            "a Value with unit field."
        ),
    )
    is_required = models.BooleanField(default=False)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.label} ({self.get_field_type_display()})"

    def options_list(self):
        return [o.strip() for o in self.dropdown_options.split(",") if o.strip()]

    def field_name(self):
        return f"field_{self.id}"


class Asset(models.Model):
    classification = models.ForeignKey(Classification, on_delete=models.CASCADE, related_name="assets")
    name = models.CharField(max_length=150, unique=True, help_text="Asset No / Serial No — must be unique")
    description = models.CharField(
        max_length=255, default="",
        help_text="Required — a short description of this asset (e.g. 'Drum Lifter', 'BOP Frame').",
    )
    current_location = models.CharField(
        max_length=150, default="Workshop",
        help_text=(
            "Where this asset currently is. Defaults to Workshop; a Job Manager "
            "dispatch changes it to that job's location, and it must be manually "
            "returned to Workshop before it can be selected for another job."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    approvals = GenericRelation("approvals.Approval")      # waiting / approved / rejected, see approvals/

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def is_available(self):
        """An asset can only be picked for a new job manifest while it's
        sitting at the Workshop -- once dispatched, it's unavailable
        until manually returned."""
        return self.current_location == "Workshop"

    def values_by_field_id(self):
        return {v.field_definition_id: v.display_value() for v in self.values.all()}


class AssetFieldValue(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="values")
    field_definition = models.ForeignKey(FieldDefinition, on_delete=models.CASCADE, related_name="values")
    text_value = models.CharField(max_length=500, blank=True)
    number_value = models.FloatField(null=True, blank=True)
    unit_value = models.CharField(max_length=50, blank=True, default="")
    file_value = models.FileField(upload_to="asset_attachments/", blank=True, null=True)

    class Meta:
        unique_together = ("asset", "field_definition")

    def display_value(self):
        ft = self.field_definition.field_type
        if ft == FieldDefinition.NUMBER:
            return self.number_value
        if ft == FieldDefinition.VALUE_UNIT:
            if self.number_value is None:
                return None
            return f"{self.number_value} {self.unit_value}".strip()
        if ft == FieldDefinition.ATTACHMENT:
            return self.file_value
        return self.text_value


class CertType(models.Model):
    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class ClassificationCertRequirement(models.Model):
    classification = models.ForeignKey(Classification, on_delete=models.CASCADE, related_name="cert_requirements")
    cert_type = models.ForeignKey(CertType, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("classification", "cert_type")

    def __str__(self):
        return f"{self.classification} requires {self.cert_type}"


class AssetCert(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="certs")
    cert_type = models.ForeignKey(CertType, on_delete=models.CASCADE)
    cert_number = models.CharField(max_length=100, blank=True, default="")
    issue_date = models.DateField(null=True, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    not_applicable = models.BooleanField(default=False)
    certificate_file = models.FileField(upload_to="asset_certs/", blank=True, null=True)
    notes = models.TextField(
        blank=True, default="",
        help_text="Freeform notes \u2014 also used to preserve imported data (e.g. an old Drive link) that doesn't have its own field.",
    )

    class Meta:
        unique_together = ("asset", "cert_type")

    def __str__(self):
        return f"{self.asset} - {self.cert_type}"

    @property
    def status(self):
        if self.not_applicable:
            return "na"
        if not self.expiry_date:
            return "missing"
        today = timezone.localdate()
        if self.expiry_date < today:
            return "expired"
        if self.expiry_date <= today + datetime.timedelta(days=30):
            return "soon"
        return "valid"
