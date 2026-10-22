from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("job_reports", "0002_jobreport_linked_sgs_report_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="jobreport",
            name="callout_start",
            field=models.DateField(
                blank=True, null=True, verbose_name="Call-out starts",
                help_text="For call-out work: the first day. Used to work out the hours NPT is measured against."),
        ),
        migrations.AddField(
            model_name="jobreport",
            name="callout_end",
            field=models.DateField(blank=True, null=True, verbose_name="Call-out ends"),
        ),
    ]
