import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("action_tracker", "0003_ordering"),
        ("job_reports", "0003_callout_dates"),
    ]

    operations = [
        migrations.AddField(model_name="action", name="client",
                            field=models.CharField(blank=True, default="", help_text="Blank = not recorded; 'ABC internal' for our own.", max_length=255)),
        migrations.AddField(model_name="action", name="action_type",
                            field=models.CharField(blank=True, choices=[("equipment", "Equipment"), ("personnel", "Personnel"), ("procedure", "Procedure / HSE"), ("documentation", "Documentation"), ("commercial", "Commercial"), ("other", "Other")], default="", max_length=20)),
        migrations.AddField(model_name="action", name="raised_date", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="action", name="closed_date", field=models.DateField(blank=True, null=True)),
        migrations.AddField(model_name="action", name="job_report",
                            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="actions", to="job_reports.jobreport")),
    ]
