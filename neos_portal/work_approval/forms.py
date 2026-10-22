from django import forms

from wire_management.lists import SharedListFieldsMixin

from .models import WAFileType, WARecord


class WAFileTypeForm(forms.ModelForm):
    class Meta:
        model = WAFileType
        fields = ["type_name"]


class WARecordForm(SharedListFieldsMixin, forms.ModelForm):
    shared_list_fields = {"location_name": "location"}

    class Meta:
        model = WARecord
        fields = ["file_type", "details", "location_name", "expiry_date", "file", "notes"]
        labels = {"location_name": "Location", "file_type": "Type"}
        widgets = {
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }


class WARenewForm(WARecordForm):
    """A renewal only makes sense with the new document and its new expiry date."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["expiry_date"].required = True
        self.fields["file"].required = True
        self.fields["file"].label = "New document"
