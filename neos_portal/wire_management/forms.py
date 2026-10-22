from decimal import Decimal

from django import forms

from .models import Client, FieldWell, WireDrum, WireLogEntry


FEET_PER_METRE = Decimal("3.28084")


class LengthUnitMixin:
    """Adds a Feet / Metres choice beside the length fields. Lengths are
    always stored in feet, so metres are converted when saved."""
    length_fields = ()

    def _add_unit_field(self):
        self.fields["length_unit"] = forms.ChoiceField(
            label="Length unit", choices=[("ft", "Feet (ft)"), ("m", "Metres (m)")],
            initial="ft", help_text="Applies to the length fields below.")
        # Keep the unit choice next to the first length field.
        order = [n for n in self.fields if n != "length_unit"]
        order.insert(order.index(self.length_fields[0]), "length_unit")
        self.order_fields(order)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("length_unit") == "m":
            for name in self.length_fields:
                value = cleaned.get(name)
                if value is not None:
                    cleaned[name] = (value * FEET_PER_METRE).quantize(Decimal("0.1"))
        return cleaned


class WireDrumForm(LengthUnitMixin, forms.ModelForm):
    length_fields = ("total_length_ft",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["total_length_ft"].label = "Total length"
        self._add_unit_field()

    class Meta:
        model = WireDrum
        fields = ["doc_code", "wire_number", "diameter", "manufacturer", "total_length_ft",
                  "asset_serial_number", "drum_number", "grade", "purchase_date"]
        widgets = {"purchase_date": forms.DateInput(attrs={"type": "date"})}


class WireLogEntryForm(LengthUnitMixin, forms.ModelForm):
    length_fields = ("cut_length_ft",)

    class Meta:
        model = WireLogEntry
        fields = ["date", "client", "field_well", "sour_environment", "wire_test_type",
                  "wire_test_results", "max_depth", "max_tension", "jarring_event_depth",
                  "jarring_event_tension", "jarring_event_duration", "run_number",
                  "cut_length_ft", "remarks", "supervisor"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"}),
                   "remarks": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cut_length_ft"].label = "Cut length"
        self._add_unit_field()
        # Imported here, not at module level, to avoid a hard startup
        # dependency between the two apps if one is ever installed
        # without the other.
        from personnel.models import Personnel
        self.fields["supervisor"].queryset = Personnel.objects.filter(status="Active")
        self.fields["client"].queryset = Client.objects.all()
        self.fields["field_well"].queryset = FieldWell.objects.all()


class ClientForm(forms.ModelForm):
    class Meta:
        model = Client
        fields = ["name"]


class FieldWellForm(forms.ModelForm):
    class Meta:
        model = FieldWell
        fields = ["name"]
