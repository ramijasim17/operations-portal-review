from django import forms

from wire_management.lists import SharedListFieldsMixin

from .models import Battery, Gauge, SGSImage, SGSReport, SGSSequenceEntry, SGSShift, SGSStation

DATETIME_ATTRS = {"type": "datetime-local", "step": "1"}


class SGSReportForm(SharedListFieldsMixin, forms.ModelForm):
    shared_list_fields = {"client": "client", "field": "field"}

    class Meta:
        model = SGSReport
        fields = [
            "report_type", "status", "client", "temp_unit", "time_display", "client_logo",
            "run_start", "run_end",
            "bottom_gauge_connected_at", "bottom_gauge_disconnected_at",
            "top_gauge_connected_at", "top_gauge_disconnected_at",
            "well_name", "field", "company", "sor_number", "dgs_cps", "test_type", "rig", "country_city",
            "depth_reference", "unit_system",
            "date_of_test", "company_representative", "neos_representative",
            "well_state_before_test", "well_type", "fluid_type", "completion_type",
            "well_status", "min_restriction", "casing_size", "tubing_size", "test_interval",
            "time_rih",  # kept as a form field only as a fallback for when Run start isn't set yet
            "bottom_gauge", "top_gauge",
        ]
        widgets = {
            "date_of_test": forms.DateInput(attrs={"type": "date"}),
            "run_start": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "run_end": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "bottom_gauge_connected_at": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "bottom_gauge_disconnected_at": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "top_gauge_connected_at": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "top_gauge_disconnected_at": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "temp_unit": forms.RadioSelect,
            "report_type": forms.RadioSelect,
            "time_display": forms.RadioSelect,
            "status": forms.RadioSelect,
        }

    def save(self, commit=True):
        report = super().save(commit=commit)
        if commit:
            report.record_battery_usage()
        return report


def _local_day(value):
    from django.utils import timezone
    return timezone.localtime(value).date() if timezone.is_aware(value) else value.date()


def _window_text(first, last):
    return f"{first:%d %b %Y}" + (f" to {last:%d %b %Y}" if last and last != first else "")


class RunWindowMixin:
    """Dates for events and stations follow the run: the pickers only offer the run's days, and a date
    outside them is refused (so nobody has to hunt for the right date, or mistype the month)."""
    window_fields = ()

    def __init__(self, *args, report=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_first, self.run_last = report.run_window() if report else (None, None)
        for name in self.window_fields:
            attrs = self.fields[name].widget.attrs
            if self.run_first:
                attrs["min"] = f"{self.run_first:%Y-%m-%d}T00:00:00"
            if self.run_last:
                attrs["max"] = f"{self.run_last:%Y-%m-%d}T23:59:59"

    def clean(self):
        cleaned = super().clean()
        for name in self.window_fields:
            value = cleaned.get(name)
            if not value or not self.run_first:
                continue
            day = _local_day(value)
            if day < self.run_first or (self.run_last and day > self.run_last):
                self.add_error(name, f"The run was {_window_text(self.run_first, self.run_last)}. Pick a time inside it "
                                     "(or correct the run dates under Common Information).")
        return cleaned


class SGSShiftForm(forms.ModelForm):
    class Meta:
        model = SGSShift
        fields = ["label", "shift_date", "order"]
        widgets = {"shift_date": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, report=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.run_first, self.run_last = report.run_window() if report else (None, None)
        attrs = self.fields["shift_date"].widget.attrs
        if self.run_first:
            attrs["min"] = f"{self.run_first:%Y-%m-%d}"
            if not self.is_bound and not self.instance.pk:
                self.fields["shift_date"].initial = self.run_first        # the run's first day, ready to use
        if self.run_last:
            attrs["max"] = f"{self.run_last:%Y-%m-%d}"

    def clean_shift_date(self):
        day = self.cleaned_data.get("shift_date")
        if day and self.run_first and (day < self.run_first or (self.run_last and day > self.run_last)):
            raise forms.ValidationError(
                f"The run was {_window_text(self.run_first, self.run_last)}. Pick a day inside it "
                "(or correct the run dates under Common Information).")
        return day


class SGSSequenceEntryForm(RunWindowMixin, forms.ModelForm):
    window_fields = ("start_datetime", "end_datetime")

    class Meta:
        model = SGSSequenceEntry
        fields = ["sr", "start_datetime", "end_datetime", "description", "order"]
        widgets = {
            "start_datetime": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "end_datetime": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "description": forms.Textarea(attrs={"rows": 2}),
        }


class SGSImageForm(forms.ModelForm):
    class Meta:
        model = SGSImage
        fields = ["image", "caption"]


class SGSStationForm(RunWindowMixin, forms.ModelForm):
    window_fields = ("start_datetime", "end_datetime")

    class Meta:
        model = SGSStation
        fields = ["step_no", "md_mgl", "start_datetime", "end_datetime", "pressure_psia", "temp_value"]
        widgets = {
            "start_datetime": forms.DateTimeInput(attrs=DATETIME_ATTRS),
            "end_datetime": forms.DateTimeInput(attrs=DATETIME_ATTRS),
        }


class StationExcelImportForm(forms.Form):
    excel_file = forms.FileField(
        label="Excel file with Step/Depth/Start/End columns",
        help_text="You'll only need to fill in Pressure and Temperature afterward.",
    )


class SequenceEntryExcelImportForm(forms.Form):
    excel_file = forms.FileField(
        label="Excel file with SR/Start/End/Description columns",
    )


class GaugeForm(forms.ModelForm):
    class Meta:
        model = Gauge
        fields = ["serial_number", "calibration_certificate", "pressure_calibration_factor", "battery", "draw_ma"]


class BatteryForm(forms.ModelForm):
    class Meta:
        model = Battery
        fields = ["serial_number", "capacity_mah", "used_before_mah"]

    def clean(self):
        cleaned = super().clean()
        capacity, used = cleaned.get("capacity_mah"), cleaned.get("used_before_mah")
        if capacity is not None and capacity <= 0:
            self.add_error("capacity_mah", "A battery must hold more than 0 mAh.")
        if used is not None and used < 0:
            self.add_error("used_before_mah", "Can't be negative.")
        return cleaned


class BatteryUsageForm(forms.Form):
    """Amend what one job took from a battery (the portal's figure is hours x the gauge's draw)."""
    consumed_mah = forms.FloatField(label="Used on this job (mAh)", min_value=0)
