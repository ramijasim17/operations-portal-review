import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("job_reports", "0003_callout_dates"),
        ("personnel", "0001_initial"),
    ]

    operations = [
        migrations.AddField(model_name="jobreport", name="day_supervisor",
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="day_jobs", to="personnel.personnel", verbose_name="Day-shift supervisor")),
        migrations.AddField(model_name="jobreport", name="night_supervisor",
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="night_jobs", to="personnel.personnel", verbose_name="Night-shift supervisor")),
    ]
