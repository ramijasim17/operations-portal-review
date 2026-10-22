from django.db import migrations, models
import django.db.models.deletion


STARTER_ORGS = ["Client A", "Client C", "CLIENT B", "Client D", "ABC"]


def create_orgs_and_migrate_data(apps, schema_editor):
    PublishingOrganization = apps.get_model("document_control", "PublishingOrganization")
    ControlledDocument = apps.get_model("document_control", "ControlledDocument")

    org_map = {}
    for name in STARTER_ORGS:
        org, _ = PublishingOrganization.objects.get_or_create(name=name)
        org_map[name] = org

    for doc in ControlledDocument.objects.all():
        old_value = (doc.published_by_old or "").strip()
        if not old_value:
            continue
        org = org_map.get(old_value)
        if org is None:
            # Whatever was actually stored (even if not one of the
            # starter names) still gets its own organization created,
            # rather than silently dropping the document's data.
            org, _ = PublishingOrganization.objects.get_or_create(name=old_value)
            org_map[old_value] = org
        doc.published_by = org
        doc.save(update_fields=["published_by"])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("document_control", "0002_documenttype_controlleddocument_published_by_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublishingOrganization",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.RenameField(
            model_name="controlleddocument",
            old_name="published_by",
            new_name="published_by_old",
        ),
        migrations.AddField(
            model_name="controlleddocument",
            name="published_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="documents", to="document_control.publishingorganization",
            ),
        ),
        migrations.RunPython(create_orgs_and_migrate_data, reverse_noop),
        migrations.RemoveField(
            model_name="controlleddocument",
            name="published_by_old",
        ),
    ]
