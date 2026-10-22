from django import forms

from .models import (
    Asset,
    AssetCert,
    CertType,
    Classification,
    Department,
    FieldDefinition,
)


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name", "location"]


class ClassificationForm(forms.ModelForm):
    copy_fields_from = forms.ModelChoiceField(
        queryset=Classification.objects.none(),
        required=False,
        label="Copy fields from (optional)",
        help_text="Start with the same fields as an existing classification in this department, then add/remove as needed.",
    )

    class Meta:
        model = Classification
        fields = ["name", "is_unit"]

    def __init__(self, *args, department=None, **kwargs):
        super().__init__(*args, **kwargs)
        if department is not None:
            self.fields["copy_fields_from"].queryset = department.classifications.all()
        self.order_fields(["name", "is_unit", "copy_fields_from"])


class ClassificationEditForm(forms.ModelForm):
    """Rename a classification and say whether its assets are units."""

    class Meta:
        model = Classification
        fields = ["name", "is_unit"]


class FieldDefinitionForm(forms.ModelForm):
    class Meta:
        model = FieldDefinition
        fields = ["label", "field_type", "dropdown_options", "is_required", "order"]
        widgets = {
            "dropdown_options": forms.TextInput(attrs={"placeholder": "Option A, Option B, Option C"}),
        }

    def clean(self):
        cleaned = super().clean()
        field_type = cleaned.get("field_type")
        if field_type == FieldDefinition.DROPDOWN and not cleaned.get("dropdown_options"):
            self.add_error("dropdown_options", "Provide at least two comma-separated options for a dropdown field.")
        elif field_type == FieldDefinition.SEARCHABLE and not cleaned.get("dropdown_options"):
            self.add_error("dropdown_options", "Provide comma-separated suggestions for a searchable field.")
        elif field_type == FieldDefinition.VALUE_UNIT and not cleaned.get("dropdown_options"):
            self.add_error("dropdown_options", "Provide comma-separated units (e.g. 'psi, bbl, ft') for a value-with-unit field.")
        return cleaned


class _BaseAssetForm(forms.Form):
    """Base class for the dynamically built asset form \u2014 adds a uniqueness
    check on the asset name/serial that a plain forms.Form doesn't get for
    free (only ModelForms validate unique= automatically)."""

    def __init__(self, *args, exclude_asset_id=None, **kwargs):
        self.exclude_asset_id = exclude_asset_id
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = Asset.objects.filter(name=name)
        if self.exclude_asset_id:
            qs = qs.exclude(pk=self.exclude_asset_id)
        if qs.exists():
            raise forms.ValidationError(
                "An asset with this Asset No / Serial No already exists. It must be unique."
            )
        return name


def build_asset_form(classification):
    """
    Builds a Django form class at runtime with one field per FieldDefinition
    belonging to the given classification, plus a fixed "name" field for the
    asset's own identifier/serial number (required to be unique \u2014 see
    _BaseAssetForm.clean_name).

    A "Value with unit" field produces two sub-fields: "<field>_value" (the
    number) and "<field>_unit" (a choice field built from the field's
    dropdown_options, reused here as the unit list).
    """
    field_defs = list(classification.fields.all())

    fields = {
        "name": forms.CharField(label="Asset No / Serial No", max_length=150),
        "description": forms.CharField(label="Description", max_length=255),
    }
    for fd in field_defs:
        kwargs = {"label": fd.label, "required": fd.is_required}
        if fd.field_type == FieldDefinition.NUMBER:
            fields[fd.field_name()] = forms.FloatField(**kwargs)
        elif fd.field_type == FieldDefinition.DROPDOWN:
            choices = [("", "---")] + [(o, o) for o in fd.options_list()]
            fields[fd.field_name()] = forms.ChoiceField(choices=choices, **kwargs)
        elif fd.field_type == FieldDefinition.SEARCHABLE:
            fields[fd.field_name()] = forms.CharField(
                widget=forms.TextInput(attrs={
                    "list": f"{fd.field_name()}-list",
                    "autocomplete": "off",
                    "placeholder": "Type to search or enter a new value",
                }),
                **kwargs,
            )
        elif fd.field_type == FieldDefinition.VALUE_UNIT:
            value_kwargs = dict(kwargs)
            value_kwargs["label"] = f"{fd.label} (value)"
            unit_kwargs = dict(kwargs)
            unit_kwargs["label"] = f"{fd.label} (unit)"
            unit_choices = [("", "---")] + [(u, u) for u in fd.options_list()]
            fields[f"{fd.field_name()}_value"] = forms.FloatField(**value_kwargs)
            fields[f"{fd.field_name()}_unit"] = forms.ChoiceField(choices=unit_choices, **unit_kwargs)
        elif fd.field_type == FieldDefinition.ATTACHMENT:
            kwargs["required"] = False
            fields[fd.field_name()] = forms.FileField(**kwargs)
        else:
            fields[fd.field_name()] = forms.CharField(**kwargs)

    form_class = type("DynamicAssetForm", (_BaseAssetForm,), fields)
    return form_class, field_defs


def asset_form_rows(form, field_defs):
    """
    Template-ready rows pairing each FieldDefinition with its bound form
    field(s), so the template doesn't need to do dynamic-name lookups or
    know about the Value-with-unit special case.
    """
    rows = []
    for fd in field_defs:
        if fd.field_type == FieldDefinition.VALUE_UNIT:
            rows.append({
                "fd": fd,
                "value_field": form[f"{fd.field_name()}_value"],
                "unit_field": form[f"{fd.field_name()}_unit"],
            })
        else:
            row = {"fd": fd, "field": form[fd.field_name()]}
            if fd.field_type == FieldDefinition.SEARCHABLE:
                row["datalist_id"] = f"{fd.field_name()}-list"
                row["options"] = fd.options_list()
            rows.append(row)
    return rows


class CertTypeForm(forms.ModelForm):
    class Meta:
        model = CertType
        fields = ["name"]


class AssetCertForm(forms.ModelForm):
    class Meta:
        model = AssetCert
        fields = ["cert_type", "cert_number", "issue_date", "expiry_date", "not_applicable", "certificate_file", "notes"]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, classification=None, **kwargs):
        super().__init__(*args, **kwargs)
        if classification is not None:
            allowed_ids = classification.cert_requirements.values_list("cert_type_id", flat=True)
            self.fields["cert_type"].queryset = CertType.objects.filter(id__in=allowed_ids)
