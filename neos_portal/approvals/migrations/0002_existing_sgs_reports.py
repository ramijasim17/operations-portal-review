from django.db import migrations
from django.utils import timezone


def approve_existing(apps, schema_editor):
    """SGS reports that existed before approvals keep working: their result and final report count as approved."""
    SGSReport = apps.get_model("sgs_report", "SGSReport")
    Approval = apps.get_model("approvals", "Approval")
    ContentType = apps.get_model("contenttypes", "ContentType")
    ct, _ = ContentType.objects.get_or_create(app_label="sgs_report", model="sgsreport")
    now = timezone.now()
    for report in SGSReport.objects.all():
        for kind in ("sgs_result", "sgs_final"):
            Approval.objects.get_or_create(
                content_type=ct, object_id=report.pk, kind=kind,
                defaults={"status": "approved", "auto": True, "label": f"{report.well_name}", "decided_at": now,
                          "note": "made before approvals existed"})


class Migration(migrations.Migration):
    dependencies = [
        ("approvals", "0001_initial"),
        ("sgs_report", "0005_batteryusage_manual_alter_batteryusage_draw_ma_and_more"),
        ("contenttypes", "0002_remove_content_type_name"),
    ]
    operations = [migrations.RunPython(approve_existing, migrations.RunPython.noop)]
