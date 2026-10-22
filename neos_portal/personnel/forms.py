from django import forms

from .models import Personnel, PersonnelCert, TrainingCourse


class PersonnelForm(forms.ModelForm):
    class Meta:
        model = Personnel
        fields = ["employee_id", "name", "position", "department", "status", "required_courses"]
        widgets = {
            "required_courses": forms.TextInput(attrs={"placeholder": "H2S, Well Control, First Aid"}),
        }

    def __init__(self, *args, scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
        if scope is not None and scope.locked and scope.department is not None:
            del self.fields["department"]                       # Level 1 / 2: always their own department
        else:
            from departments.models import Department

            names = list(Department.objects.values_list("name", flat=True))
            current = (self.instance.department or "").strip() if self.instance.pk else ""
            if current and current.lower() not in [n.lower() for n in names]:
                names.append(current)                           # an older, differently-typed value stays selectable
            self.fields["department"] = forms.ChoiceField(label="Department", required=False,
                                                          choices=[("", "— none —")] + [(n, n) for n in names])

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.scope is not None and self.scope.locked and self.scope.department is not None:
            obj.department = self.scope.department.name
        if commit:
            obj.save()
            self.save_m2m()
        return obj

    def clean_employee_id(self):
        eid = self.cleaned_data["employee_id"].strip()
        qs = Personnel.objects.filter(employee_id=eid)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("An employee with this ID already exists.")
        return eid


class TrainingCourseForm(forms.ModelForm):
    class Meta:
        model = TrainingCourse
        fields = ["course_name"]


class PersonnelCertForm(forms.ModelForm):
    def __init__(self, *args, people=None, **kwargs):
        super().__init__(*args, **kwargs)
        if people is not None:
            self.fields["personnel"].queryset = people

    class Meta:
        model = PersonnelCert
        fields = ["personnel", "course", "expiry_date", "file", "notes"]
        widgets = {
            "expiry_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
        }
