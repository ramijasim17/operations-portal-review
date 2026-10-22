from django import forms

from wire_management.lists import SharedListFieldsMixin

from .digital_forms import DIGITAL_FORM_CHOICES
from .models import (
    NPT_CATEGORY_CHOICES,
    NPTKPI,
    ClientNPTSetting,
    SQBDocument,
    SQBJob,
    SQBPackage,
    SQBPartDefinition,
    SQBPartUpload,
    SQBTrackerEntry,
    SQBType,
)


class SQBJobForm(forms.ModelForm):
    class Meta:
        model = SQBJob
        fields = ["job_number", "job_name", "created_date", "permit_number"]
        widgets = {"created_date": forms.DateInput(attrs={"type": "date"})}


class SQBDocumentForm(forms.ModelForm):
    class Meta:
        model = SQBDocument
        fields = ["doc_type", "file", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


def unit_queryset():
    """The units NPT is tracked against: every asset in a classification that
    Departments marks as "These are units" (in any department). If nothing is
    marked yet, every asset is offered so the form is never empty."""
    from departments.models import Asset

    units = (Asset.objects.filter(classification__is_unit=True)
             .select_related("classification__department").order_by("name"))
    return units if units.exists() else Asset.objects.select_related("classification__department").order_by("name")


class UnitChoiceField(forms.ModelChoiceField):
    """'A 84828 — MAN Truck', plus the department when units come from several."""
    show_department = False

    def label_from_instance(self, obj):
        text = f"{obj.name} \u2014 {obj.description}" if obj.description else obj.name
        return f"{text} ({obj.classification.department.name})" if self.show_department else text

    def set_queryset(self, queryset):
        self.queryset = queryset
        self.show_department = len({u.classification.department_id for u in queryset}) > 1


class JobChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        client = obj.manifest.client or "no client"
        when = f" \u00b7 {obj.manifest.date:%d %b %Y}" if obj.manifest.date else ""
        return f"{obj.name} \u2014 {client}{when}"


def job_queryset():
    from job_reports.models import JobReport

    return JobReport.objects.select_related("manifest").order_by("-created_at")


class SQBTrackerEntryForm(SharedListFieldsMixin, forms.ModelForm):
    shared_list_fields = {"client": "client"}
    job_report = JobChoiceField(queryset=None, required=True, label="Job", empty_label="\u2014 select the job \u2014")
    unit = UnitChoiceField(queryset=None, required=True, label="Unit", empty_label="\u2014 select the unit \u2014")

    class Meta:
        model = SQBTrackerEntry
        fields = ["date", "job_report", "unit", "category", "npt_hours", "client", "description", "rating"]
        labels = {"category": "Cause of the NPT", "npt_hours": "NPT (hours)"}
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "npt_hours": forms.NumberInput(attrs={"step": "0.25", "min": "0"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["job_report"].queryset = job_queryset()
        self.fields["unit"].set_queryset(unit_queryset())
        self.fields["date"].required = True
        self.fields["npt_hours"].required = True
        self.fields["category"].required = True
        self.fields["category"].choices = [("", "\u2014 select the cause \u2014")] + list(NPT_CATEGORY_CHOICES)
        self.fields["client"].label = "Client (only if the job has none)"

    def clean_npt_hours(self):
        hours = self.cleaned_data["npt_hours"]
        if hours is not None and hours < 0:
            raise forms.ValidationError("NPT hours can't be negative.")
        return hours

    def clean_date(self):
        import datetime

        date = self.cleaned_data["date"]
        if date and date > datetime.date.today():
            raise forms.ValidationError("NPT can't be logged for a date that hasn't happened yet.")
        return date

    def clean(self):
        cleaned = super().clean()
        job = cleaned.get("job_report")
        if job:
            # The client always follows the job's manifest, so each client's NPT
            # is worked out from the jobs that really belong to it.
            cleaned["client"] = job.manifest.client or cleaned.get("client", "")
            if not cleaned["client"]:
                self.add_error("client", "This job's manifest has no client \u2014 pick the client here.")
        return cleaned

    def save(self, commit=True):
        entry = super().save(commit=False)
        entry.client = self.cleaned_data.get("client", "")
        if entry.job_report_id:
            entry.job_ticket = entry.job_report.name
        if commit:
            entry.save()
        return entry


class NPTKPIForm(SharedListFieldsMixin, forms.ModelForm):
    shared_list_fields = {"client": "client"}
    unit = UnitChoiceField(queryset=None, required=False, label="Only this unit", empty_label="\u2014 all units \u2014")

    class Meta:
        model = NPTKPI
        fields = ["name", "metric", "target", "unit", "client", "category", "is_active"]
        labels = {"metric": "Measured as", "target": "Target (the most allowed)", "client": "Only this client",
                  "category": "Only this cause", "is_active": "In use"}
        widgets = {"target": forms.NumberInput(attrs={"step": "0.1", "min": "0"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["unit"].set_queryset(unit_queryset())
        self.fields["client"].label = "Only this client"
        self.fields["category"].choices = [("", "\u2014 all causes \u2014")] + list(NPT_CATEGORY_CHOICES)
        self.fields["category"].required = False

    def clean(self):
        cleaned = super().clean()
        target, metric = cleaned.get("target"), cleaned.get("metric")
        if target is not None:
            if target <= 0:
                self.add_error("target", "The target must be more than zero.")
            elif metric == NPTKPI.PERCENT and target > 100:
                self.add_error("target", "A percentage can't be more than 100.")
        return cleaned


class ClientNPTSettingForm(forms.ModelForm):
    """One client's NPT basis and the units on its contract."""
    units = forms.ModelMultipleChoiceField(
        queryset=None, required=False, widget=forms.CheckboxSelectMultiple, label="Units on this client's contract",
    )

    class Meta:
        model = ClientNPTSetting
        fields = ["basis", "callout_hours", "units"]
        labels = {"basis": "NPT is worked out on", "callout_hours": "Call-out hours counted per day"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["units"].queryset = unit_queryset()
        self.fields["units"].label_from_instance = lambda u: f"{u.name} \u2014 {u.description}" if u.description else u.name


class SQBTypeForm(forms.ModelForm):
    class Meta:
        model = SQBType
        fields = ["name"]


class SQBPartDefinitionForm(forms.ModelForm):
    class Meta:
        model = SQBPartDefinition
        fields = ["name", "order", "digital_form_key"]
        widgets = {
            "digital_form_key": forms.Select(
                choices=[("", "None \u2014 upload a scanned copy instead")] + DIGITAL_FORM_CHOICES
            ),
        }


class SQBPackageCreateForm(forms.Form):
    sqb_type = forms.ModelChoiceField(queryset=SQBType.objects.all(), label="SQB Type")


class SQBPartUploadForm(forms.ModelForm):
    class Meta:
        model = SQBPartUpload
        fields = ["file"]
