from django import forms

from wire_management.lists import SharedListFieldsMixin

from .models import JobManifest


class JobManifestForm(SharedListFieldsMixin, forms.ModelForm):
    shared_list_fields = {"client": "client", "location": "location"}

    class Meta:
        model = JobManifest
        fields = ["job_name", "client", "date", "location", "status", "department"]
        widgets = {"date": forms.DateInput(attrs={"type": "date"})}
