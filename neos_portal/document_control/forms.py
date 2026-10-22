from django import forms

from .models import ControlledDocument, DocumentType, PublishingOrganization


class ControlledDocumentForm(forms.ModelForm):
    class Meta:
        model = ControlledDocument
        fields = ["name", "document_type", "document_number", "version", "published_by", "date_of_issue", "file"]
        widgets = {"date_of_issue": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Both are nullable at the model level (so switching from the old
        # choices fields didn't need a migration default), but every new
        # or edited document should still have to pick both.
        self.fields["document_type"].required = True
        self.fields["published_by"].required = True


class DocumentTypeForm(forms.ModelForm):
    class Meta:
        model = DocumentType
        fields = ["name"]


class PublishingOrganizationForm(forms.ModelForm):
    class Meta:
        model = PublishingOrganization
        fields = ["name"]
