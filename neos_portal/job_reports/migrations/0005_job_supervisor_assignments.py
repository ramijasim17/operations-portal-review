import datetime

import django.db.models.deletion
from django.db import migrations, models


def copy_supervisors(apps, schema_editor):
    """The single day / night supervisor fields become assignments with no end date."""
    JobReport = apps.get_model("job_reports", "JobReport")
    JobSupervisor = apps.get_model("job_reports", "JobSupervisor")
    for job in JobReport.objects.select_related("manifest"):
        started = job.callout_start or job.manifest.date or job.created_at.date()
        for shift, person_id in (("day", job.day_supervisor_id), ("night", job.night_supervisor_id)):
            if person_id:
                JobSupervisor.objects.create(job_report=job, person_id=person_id, shift=shift, start_date=started)


def restore_supervisors(apps, schema_editor):
    JobSupervisor = apps.get_model("job_reports", "JobSupervisor")
    JobReport = apps.get_model("job_reports", "JobReport")
    for a in JobSupervisor.objects.order_by("-start_date"):
        field = "night_supervisor_id" if a.shift == "night" else "day_supervisor_id"
        JobReport.objects.filter(pk=a.job_report_id, **{field: None}).update(**{field: a.person_id})


class Migration(migrations.Migration):

    dependencies = [
        ("job_reports", "0004_supervisors"),
        ("personnel", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="JobSupervisor",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("shift", models.CharField(choices=[("day", "Day shift"), ("night", "Night shift"), ("both", "Day & night")], default="day", max_length=5)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField(blank=True, help_text="Leave empty while they are still on the job.", null=True)),
                ("job_report", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assignments", to="job_reports.jobreport")),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="job_assignments", to="personnel.personnel")),
            ],
            options={"ordering": ["start_date", "shift", "id"]},
        ),
        migrations.RunPython(copy_supervisors, restore_supervisors),
        migrations.RemoveField(model_name="jobreport", name="day_supervisor"),
        migrations.RemoveField(model_name="jobreport", name="night_supervisor"),
    ]
