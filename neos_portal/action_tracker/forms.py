from django import forms

from personnel.models import Personnel

from wire_management.lists import names as shared_names

from .models import INTERNAL, Action, ActionStep


class ActionForm(forms.ModelForm):
    steps_input = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}), required=False,
        label="Steps (one per line, optional)",
    )

    class Meta:
        model = Action
        fields = ["department", "action_item", "client", "action_type", "job_report", "assignee", "priority", "raised_date", "due_date", "status", "comments"]
        widgets = {
            "due_date": forms.DateInput(attrs={"type": "date"}),
            "raised_date": forms.DateInput(attrs={"type": "date"}),
            "comments": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"assignee": "Assigned to", "client": "Raised by", "action_type": "Type", "job_report": "Job (optional)"}

    def __init__(self, *args, scope=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.scope = scope
        self.fields["department"].required = False
        self.fields["department"].empty_label = "— choose the department —"
        self.fields["department"].help_text = ""
        if scope is not None and scope.locked and scope.department is not None:
            del self.fields["department"]                  # Level 1 / 2: always their own department
        # Assignee is picked from active Personnel. A name typed before this
        # existed stays selectable so editing an old action never blanks it.
        from approvals.services import only_approved

        people = Personnel.objects.filter(status="Active")
        if scope is not None and scope.locked and scope.department is not None:
            people = people.filter(department__iexact=scope.department.name)     # only their own department's people
        names = list(only_approved(people, "personnel").order_by("name").values_list("name", flat=True))
        current = (self.instance.assignee or "").strip() if self.instance.pk else ""
        if current and current not in names:
            names.append(current)
        self.fields["assignee"] = forms.ChoiceField(
            label="Assigned to", required=False,
            choices=[("", "— unassigned —")] + [(n, n) for n in names],
        )
        # Raised by: a shared-list client, or us. A client no longer in the list stays selectable.
        options = shared_names("client") + [INTERNAL]
        saved = (self.instance.client or "").strip() if self.instance.pk else ""
        if saved and saved not in options:
            options.append(saved)
        self.fields["client"] = forms.ChoiceField(label="Raised by", required=False,
                                                  choices=[("", "— not recorded —")] + [(n, n) for n in options])
        from sqb.forms import JobChoiceField, job_queryset
        self.fields["job_report"] = JobChoiceField(queryset=job_queryset(), required=False, label="Job (optional)",
                                                   empty_label="— no job —")
        self.fields["raised_date"].required = False
        if not self.instance.pk:
            import datetime
            self.fields["raised_date"].initial = datetime.date.today()
        if self.instance.pk:
            del self.fields["steps_input"]     # steps are managed on the action's own page
        else:
            del self.fields["status"]          # new actions always start Pending

    def clean_raised_date(self):
        import datetime
        d = self.cleaned_data.get("raised_date")
        if d and d > datetime.date.today():
            raise forms.ValidationError("An action can't have been raised in the future.")
        return d

    def clean(self):
        data = super().clean()
        if "department" in self.fields and not data.get("department") and not self.instance.department_id:
            probe = Action(assignee=data.get("assignee", ""))
            if not probe.derive_department_id():
                self.add_error("department", "Choose the department. (It is filled in for you when the person assigned has one.)")
        job = data.get("job_report")
        if job and not data.get("client") and job.manifest.client:     # the job's client, unless one was chosen
            data["client"] = job.manifest.client
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.scope is not None and self.scope.locked and self.scope.department is not None:
            obj.department = self.scope.department
        elif obj.department_id is None:
            obj.department_id = obj.derive_department_id()
        if not obj.raised_date and not obj.pk:
            import datetime
            obj.raised_date = datetime.date.today()
        if commit:
            obj.save()
        if commit and "steps_input" in self.fields:
            for line in self.cleaned_data.get("steps_input", "").splitlines():
                line = line.strip()
                if line:
                    ActionStep.objects.create(action=obj, step_description=line)
        return obj


class ActionStepForm(forms.ModelForm):
    class Meta:
        model = ActionStep
        fields = ["step_description"]


def people_names(current=""):
    """Active personnel names for 'assigned to' lists; a name typed before stays selectable."""
    from approvals.services import only_approved

    names = list(only_approved(Personnel.objects.filter(status="Active"), "personnel").order_by("name").values_list("name", flat=True))
    current = (current or "").strip()
    if current and current not in names:
        names.append(current)
    return names


class RaiseActionForm(forms.Form):
    """The small form used by Maintenance and QHSE to raise a corrective action into the Action Tracker."""
    action_item = forms.CharField(label="What must be done", max_length=255)
    assignee = forms.ChoiceField(label="Assigned to", required=False)
    priority = forms.ChoiceField(choices=[("High", "High"), ("Medium", "Medium"), ("Low", "Low")], initial="Medium")
    due_date = forms.DateField(label="Due", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].choices = [("", "— unassigned —")] + [(n, n) for n in people_names()]
