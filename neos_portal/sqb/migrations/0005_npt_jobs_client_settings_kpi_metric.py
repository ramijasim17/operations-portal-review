import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """NPT entries link to a job; clients get their own NPT basis and units;
    KPIs can be a percentage (the old hours target is kept, renamed)."""

    dependencies = [
        ("departments", "0004_classification_is_unit"),
        ("job_reports", "0003_callout_dates"),
        ("sqb", "0004_npt_units_categories_kpis"),
    ]

    operations = [
        migrations.AddField(
            model_name="sqbtrackerentry",
            name="job_report",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="npt_entries",
                to="job_reports.jobreport",
                help_text="The job (from Job Reports) the NPT happened on. The client comes from its manifest."),
        ),
        migrations.CreateModel(
            name="ClientNPTSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("client", models.CharField(max_length=255, unique=True)),
                ("basis", models.CharField(
                    choices=[("monthly_24", "Monthly — 24 hours a day"), ("monthly_12", "Monthly — 12 hours a day"),
                             ("callout", "Call-out — by the job's duration")],
                    default="monthly_24", max_length=12)),
                ("callout_hours", models.PositiveSmallIntegerField(
                    choices=[(12, "12 hours a day"), (24, "24 hours a day")], default=12,
                    help_text="For call-outs: the hours counted for each day of the call-out.")),
                ("units", models.ManyToManyField(
                    blank=True, related_name="npt_clients", to="departments.asset",
                    help_text="The units on this client's contract. Each has the full hours available every day.")),
            ],
            options={"ordering": ["client"]},
        ),
        # target_hours -> target keeps every existing KPI
        migrations.RenameField(model_name="nptkpi", old_name="target_hours", new_name="target"),
        migrations.AlterField(
            model_name="nptkpi",
            name="target",
            field=models.DecimalField(
                decimal_places=2, max_digits=7,
                help_text="The most NPT allowed: hours in a month, or % of the hours available."),
        ),
        migrations.AddField(
            model_name="nptkpi",
            name="metric",
            field=models.CharField(
                choices=[("hours", "Hours per month"), ("percent", "% of available hours")],
                default="hours", max_length=10),
        ),
    ]
