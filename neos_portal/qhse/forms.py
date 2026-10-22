import datetime

from django import forms

from action_tracker.forms import RaiseActionForm as CorrectiveActionForm, people_names as _people  # noqa: F401
from departments.models import Asset
from wire_management.lists import names as shared_names

from .models import AuditTemplate, Incident, Inspection


def _person_field(label, current=""):
    return forms.ChoiceField(label=label, required=False,
                             choices=[("", "— not set —")] + [(n, n) for n in _people(current)])


class IncidentForm(forms.ModelForm):
    class Meta:
        model = Incident
        fields = ["department", "date", "time", "incident_type", "severity", "title", "description", "location", "client",
                  "asset_serial", "people_involved", "immediate_action", "root_cause", "assigned_to", "status", "file"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "time": forms.TimeInput(attrs={"type": "time"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "people_involved": forms.Textarea(attrs={"rows": 2}),
            "immediate_action": forms.Textarea(attrs={"rows": 2}),
            "root_cause": forms.Textarea(attrs={"rows": 2}),
        }
        labels = {"incident_type": "Type", "assigned_to": "Investigated by", "file": "Photo / report (optional)",
                  "asset_serial": "Equipment involved (Asset No / Serial No)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"] = _person_field("Investigated by", self.instance.assigned_to if self.instance.pk else "")
        options = shared_names("client")
        saved = (self.instance.client or "").strip() if self.instance.pk else ""
        if saved and saved not in options:
            options.append(saved)
        self.fields["client"] = forms.ChoiceField(label="Client", required=False,
                                                  choices=[("", "— none —")] + [(n, n) for n in options])
        if not self.instance.pk:
            del self.fields["status"]            # a new report always starts Open

    def clean_date(self):
        d = self.cleaned_data["date"]
        if d > datetime.date.today():
            raise forms.ValidationError("The date can't be in the future.")
        return d


class InspectionForm(forms.ModelForm):
    class Meta:
        model = Inspection
        fields = ["department", "title", "kind", "template", "scope", "auditor_company", "asset_serial", "area", "inspector", "due_date", "repeat_months", "findings", "file"]
        widgets = {"due_date": forms.DateInput(attrs={"type": "date"}), "findings": forms.Textarea(attrs={"rows": 2})}
        labels = {"kind": "Type", "findings": "Notes", "file": "File (optional)",
                  "asset_serial": "Equipment inspected (Asset No / Serial No)"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["inspector"] = _person_field("Done by", self.instance.inspector if self.instance.pk else "")
        self.fields["due_date"].required = True
        self.fields["template"].queryset = AuditTemplate.objects.filter(is_active=True) | AuditTemplate.objects.filter(pk=self.instance.template_id or 0)
        self.fields["template"].label = "Checklist template"
        self.fields["template"].empty_label = "— none (simple inspection) —"


class InspectionDoneForm(forms.Form):
    done_date = forms.DateField(label="Date done", widget=forms.DateInput(attrs={"type": "date"}))
    result = forms.ChoiceField(label="Result", choices=[("", "—")] + Inspection.RESULT_CHOICES)
    inspector = forms.ChoiceField(label="Done by", required=False)
    findings = forms.CharField(label="Findings / notes", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    file = forms.FileField(label="Report / photos (optional)", required=False)

    def __init__(self, *args, inspection=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["done_date"].initial = datetime.date.today()
        current = inspection.inspector if inspection else ""
        self.fields["inspector"].choices = [("", "— not set —")] + [(n, n) for n in _people(current)]
        self.fields["inspector"].initial = current
        self.fields["findings"].initial = inspection.findings if inspection else ""


def _asset_widget(form):
    form.fields["asset_serial"].required = False
    form.fields["asset_serial"].widget = forms.TextInput(attrs={"list": "asset-serial-list", "autocomplete": "off"})
    form.fields["asset_serial"].help_text = "Optional. Links this to the equipment's maintenance history and overview page."


def _clean_asset(self):
    serial = (self.cleaned_data.get("asset_serial") or "").strip()
    if serial and not Asset.objects.filter(name=serial).exists():
        raise forms.ValidationError("No asset with this Asset No / Serial No exists in Departments. Check spelling or leave it empty.")
    return serial


for _form in (IncidentForm, InspectionForm):
    _orig = _form.__init__

    def _init(self, *a, _orig=_orig, **k):
        _orig(self, *a, **k)
        _asset_widget(self)

    _form.__init__ = _init
    _form.clean_asset_serial = _clean_asset


# ───────── audit templates ─────────
from .models import AuditPart, AuditPoint, AuditTemplate, Investigation, SiteVisit, SiteVisitItem  # noqa: E402


class AuditTemplateForm(forms.ModelForm):
    class Meta:
        model = AuditTemplate
        fields = ["name", "department", "kind", "description", "pass_mark", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}
        labels = {"kind": "Type", "pass_mark": "Pass mark (%)", "is_active": "In use", "department": "Department (leave empty for all)"}

    def clean_pass_mark(self):
        v = self.cleaned_data["pass_mark"]
        if not 1 <= v <= 100:
            raise forms.ValidationError("Enter a percentage from 1 to 100.")
        return v


class AuditPartForm(forms.ModelForm):
    class Meta:
        model = AuditPart
        fields = ["title"]
        labels = {"title": "Part (section) name"}


class AuditPointForm(forms.ModelForm):
    class Meta:
        model = AuditPoint
        fields = ["text", "guidance", "answer_type", "unit", "min_value", "max_value", "critical", "note_required"]
        widgets = {"guidance": forms.Textarea(attrs={"rows": 2})}

    def clean(self):
        data = super().clean()
        if data.get("answer_type") == AuditPoint.NUMBER:
            lo, hi = data.get("min_value"), data.get("max_value")
            if lo is None and hi is None:
                raise forms.ValidationError("For a measured value, enter the lowest and/or the highest allowed value.")
            if lo is not None and hi is not None and lo > hi:
                raise forms.ValidationError("The lowest allowed value is higher than the highest.")
        else:
            data["min_value"] = data["max_value"] = None
            data["unit"] = ""
        return data


# ───────── investigations ─────────
class InvestigationForm(forms.ModelForm):
    class Meta:
        model = Investigation
        fields = ["department", "title", "trigger", "asset_serial", "opened_date", "what_happened", "findings", "root_cause", "owner", "due_date", "status"]
        widgets = {
            "opened_date": forms.DateInput(attrs={"type": "date"}), "due_date": forms.DateInput(attrs={"type": "date"}),
            "what_happened": forms.Textarea(attrs={"rows": 3}), "findings": forms.Textarea(attrs={"rows": 3}),
            "root_cause": forms.Textarea(attrs={"rows": 2}),
            "asset_serial": forms.TextInput(attrs={"list": "asset-serial-list", "autocomplete": "off"}),
        }
        labels = {"trigger": "Why", "asset_serial": "Equipment (Asset No / Serial No)", "opened_date": "Opened"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["owner"] = _person_field("Investigated by", self.instance.owner if self.instance.pk else "")
        self.fields["asset_serial"].required = False
        if not self.instance.pk:
            del self.fields["status"]

    clean_asset_serial = _clean_asset


# ───────── site visits ─────────
class SiteVisitForm(forms.ModelForm):
    class Meta:
        model = SiteVisit
        fields = ["department", "date", "site", "client", "purpose", "visited_by", "attendees", "summary", "next_steps"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "attendees": forms.Textarea(attrs={"rows": 2}), "summary": forms.Textarea(attrs={"rows": 3}),
            "next_steps": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["visited_by"] = _person_field("Visited by", self.instance.visited_by if self.instance.pk else "")
        options = shared_names("client")
        saved = (self.instance.client or "").strip() if self.instance.pk else ""
        if saved and saved not in options:
            options.append(saved)
        self.fields["client"] = forms.ChoiceField(label="Client", required=False, choices=[("", "— none —")] + [(n, n) for n in options])


class SiteVisitItemForm(forms.ModelForm):
    class Meta:
        model = SiteVisitItem
        fields = ["area", "kind", "severity", "text", "file"]
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}
        labels = {"severity": "Severity (for a finding)"}

    def clean(self):
        data = super().clean()
        if data.get("kind") == SiteVisitItem.FINDING and not data.get("severity"):
            self.add_error("severity", "Choose how serious the finding is.")
        if data.get("kind") != SiteVisitItem.FINDING:
            data["severity"] = ""
        return data


# ───────── department on every QHSE record ─────────
from .models import _asset_department_id  # noqa: E402


def _require_department(self):
    """A record needs a department: the one chosen, or the one the named equipment belongs to."""
    data = self.cleaned_data
    scope = getattr(self, "_scope", None)
    if data.get("department") or (scope is not None and scope.locked and scope.department is not None):
        return data
    serial = (data.get("asset_serial") or "").strip()
    tpl = data.get("template")
    if (serial and _asset_department_id(serial)) or (tpl and tpl.department_id):
        return data
    self.add_error("department", "Choose the department. (It is filled in for you when you name equipment.)")
    return data


for _form in (IncidentForm, InspectionForm, InvestigationForm, SiteVisitForm):
    _prev_clean = _form.clean

    def _clean(self, _prev=_prev_clean):
        _prev(self)
        return _require_department(self)

    _form.clean = _clean
    _inner = _form.__init__

    def _init(self, *a, _inner=_inner, scope=None, **k):
        _inner(self, *a, **k)
        self._scope = scope
        self.fields["department"].required = False
        self.fields["department"].empty_label = "— choose the department —"
        self.fields["department"].help_text = ""
        if scope is not None and scope.locked and scope.department is not None:
            del self.fields["department"]               # Level 1 / 2: always their own department

    _form.__init__ = _init

    def _save(self, commit=True, _inner_save=_form.save):
        obj = _inner_save(self, commit=False)
        scope = getattr(self, "_scope", None)
        if scope is not None and scope.locked and scope.department is not None:
            obj.department = scope.department
        if commit:
            obj.save()
            self.save_m2m()
        return obj

    _form.save = _save
