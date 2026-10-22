import datetime

from django import forms

from departments.models import Asset
from wire_management.lists import SharedListFieldsMixin

from .models import MaintenanceLevel, MaintenanceRecord


class MaintenanceLevelForm(forms.ModelForm):
    class Meta:
        model = MaintenanceLevel
        fields = ["level_name", "interval_count", "interval_unit"]
        labels = {"interval_count": "Done every", "interval_unit": "…"}


class MaintenanceRecordForm(SharedListFieldsMixin, forms.ModelForm):
    shared_list_fields = {"location_name": "location"}

    class Meta:
        model = MaintenanceRecord
        fields = [
            "asset_serial", "activity", "schedule", "maintenance_level",
            "times_per", "every_count", "every_unit",
            "due_date", "place", "location_name", "file", "notes",
        ]
        labels = {
            "activity": "What is it?",
            "schedule": "How often?",
            "maintenance_level": "Level",
            "times_per": "Times",
            "every_count": "every",
            "every_unit": "…",
            "due_date": "Next due (leave empty to start one period from today)",
            "place": "Where is it done?",
            "location_name": "Location",
        }
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "asset_serial": forms.TextInput(attrs={"list": "asset-serial-list", "autocomplete": "off"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, allowed=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed = allowed                       # Level 1 / 2: only their own department's equipment

    def clean_asset_serial(self):
        serial = self.cleaned_data["asset_serial"].strip()
        if self.allowed is not None and serial not in self.allowed:
            raise forms.ValidationError("That equipment is not in your department.")
        if not Asset.objects.filter(name=serial).exists():
            raise forms.ValidationError(
                "No asset with this Asset No / Serial No exists in Departments. Check spelling."
            )
        return serial

    def clean(self):
        cleaned = super().clean()
        schedule = cleaned.get("schedule")
        level, count = cleaned.get("maintenance_level"), cleaned.get("every_count")

        if schedule == MaintenanceRecord.LEVELLED:
            if not level:
                self.add_error("maintenance_level", "Choose the level.")
            elif not count and not level.interval_count:
                self.add_error(
                    "every_count",
                    f"{level.level_name} has no period yet. Enter one here, or set it under Manage levels.",
                )
        elif schedule == MaintenanceRecord.REPEATING:
            if not count:
                self.add_error("every_count", "Enter how often it repeats, e.g. 1 month.")
            if not cleaned.get("times_per"):
                self.add_error("times_per", "Enter how many times per period (at least 1).")
        elif schedule == MaintenanceRecord.ONE_OFF:
            if not cleaned.get("due_date"):
                self.add_error("due_date", "Pick the date it is due.")

        # Fields that don't apply to the chosen schedule are cleared so nothing stale is kept.
        if schedule != MaintenanceRecord.LEVELLED:
            cleaned["maintenance_level"] = None
        if schedule in (MaintenanceRecord.ONE_OFF, MaintenanceRecord.ON_FAILURE):
            cleaned["every_count"] = None
            cleaned["times_per"] = 1
        if schedule == MaintenanceRecord.ON_FAILURE:
            cleaned["due_date"] = None
        if cleaned.get("place") != MaintenanceRecord.LOCATION:
            cleaned["location_name"] = ""
        return cleaned

    def save(self, commit=True):
        record = super().save(commit=False)
        record.maintenance_level = self.cleaned_data.get("maintenance_level")
        record.every_count = self.cleaned_data.get("every_count")
        record.times_per = self.cleaned_data.get("times_per") or 1
        if record.schedule == MaintenanceRecord.ON_FAILURE:
            record.due_date = None
        # Changing the schedule of a finished one-off brings it back to life.
        if record.schedule != MaintenanceRecord.ONE_OFF or record.due_date:
            record.is_active = True
        if commit:
            record.save()
        return record


class CompleteForm(forms.Form):
    done_date = forms.DateField(label="Date done", widget=forms.DateInput(attrs={"type": "date"}))
    result = forms.ChoiceField(label="Result", required=False,
                               choices=[("", "—"), ("Pass", "Pass"), ("Fail", "Fail")])
    notes = forms.CharField(label="Notes", required=False, widget=forms.Textarea(attrs={"rows": 2}))
    file = forms.FileField(label="Report / certificate (optional)", required=False)

    def __init__(self, *args, record=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["done_date"].initial = datetime.date.today()
        # Result is offered for every job; it is only compulsory for pressure tests.
        if record is not None and record.activity == MaintenanceRecord.PRESSURE_TEST:
            self.fields["result"].required = True
            self.fields["result"].choices = [("Pass", "Pass"), ("Fail", "Fail")]


class FailureForm(forms.Form):
    date = forms.DateField(label="Date of failure", widget=forms.DateInput(attrs={"type": "date"}))
    notes = forms.CharField(label="What happened", required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["date"].initial = datetime.date.today()
