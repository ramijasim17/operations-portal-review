from django import forms

from personnel.models import Personnel

from .models import JobReport, JobReportDocument, JobSupervisor


class PersonChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.name} \u2014 {obj.position}" if obj.position else obj.name


def supervisor_queryset(current=()):
    """Active personnel, people whose position says supervisor first. Someone already on a job
    stays selectable even if they have since been deactivated."""
    from django.db.models import Case, IntegerField, Q, Value, When

    from approvals.services import pending_pks

    keep = [p for p in current if p]
    waiting = [pk for pk in pending_pks(Personnel, "personnel") if pk not in keep]        # not yet approved: can't be put on a job
    return (Personnel.objects.filter(Q(status="Active") | Q(pk__in=keep)).exclude(pk__in=waiting)
            .annotate(rank=Case(When(position__icontains="supervisor", then=Value(0)), default=Value(1), output_field=IntegerField()))
            .order_by("rank", "name"))


class JobReportForm(forms.ModelForm):
    class Meta:
        model = JobReport
        fields = [
            "manifest", "name", "description",
            "needs_survey_report", "survey_report_type",
            "callout_start", "callout_end",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "callout_start": forms.DateInput(attrs={"type": "date"}),
            "callout_end": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Jobs can only be made from approved manifests (the one a job already uses stays selectable).
        from approvals.services import only_approved

        queryset = only_approved(self.fields["manifest"].queryset, "manifest")
        if self.instance.pk and self.instance.manifest_id:
            queryset = self.fields["manifest"].queryset.filter(pk__in=list(queryset.values_list("pk", flat=True)) + [self.instance.manifest_id])
        self.fields["manifest"].queryset = queryset

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("callout_start"), cleaned.get("callout_end")
        if bool(start) != bool(end):
            self.add_error("callout_end" if start else "callout_start", "Enter both the start and the end of the call-out.")
        elif start and end < start:
            self.add_error("callout_end", "The call-out can't end before it starts.")
        if cleaned.get("needs_survey_report") and not cleaned.get("survey_report_type"):
            self.add_error(
                "survey_report_type",
                "Choose SGS, FGS, or PBU since this job needs a survey report.",
            )
        return cleaned


class JobReportDocumentForm(forms.ModelForm):
    class Meta:
        model = JobReportDocument
        fields = ["document_type", "title", "file", "notes"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class JobSupervisorForm(forms.ModelForm):
    class Meta:
        model = JobSupervisor
        fields = ["person", "shift", "start_date", "end_date"]
        widgets = {"start_date": forms.DateInput(attrs={"type": "date"}), "end_date": forms.DateInput(attrs={"type": "date"})}
        labels = {"person": "Supervisor", "start_date": "From", "end_date": "Until (empty = still on the job)"}

    def __init__(self, *args, job=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job or self.instance.job_report
        current = [self.instance.person_id] if self.instance.pk else []
        self.fields["person"] = PersonChoiceField(queryset=supervisor_queryset(current), label="Supervisor")
        if not self.instance.pk:
            import datetime
            self.fields["start_date"].initial = self.job.callout_start or self.job.manifest.date or datetime.date.today()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "They can't leave the job before they start.")
            return cleaned
        if start and cleaned.get("shift"):
            mine = JobSupervisor(job_report=self.job, person=cleaned.get("person"), shift=cleaned["shift"], start_date=start, end_date=end)
            for other in self.job.assignments.exclude(pk=self.instance.pk):
                if mine.overlaps(other):
                    until = f" to {other.end_date:%d %b}" if other.end_date else " onwards"
                    self.add_error(None, f"{other.person.name} already covers the {other.get_shift_display().lower()} "
                                         f"from {other.start_date:%d %b}{until}. End that stretch first, or change the dates.")
                    break
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.job_report = self.job
        if commit:
            obj.save()
        return obj
