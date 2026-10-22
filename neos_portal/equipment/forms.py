from django import forms
from .models import EquipmentCert, Equipment, CertType


class EquipmentCertForm(forms.ModelForm):
    class Meta:
        model = EquipmentCert
        fields = ["cert_number", "expiry_date", "file"]
        widgets = {
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
        }


class EquipmentForm(forms.ModelForm):
    class Meta:
        model = Equipment
        fields = ["serial_number", "category", "description", "location"]
