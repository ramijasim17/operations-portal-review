from django.db import migrations, models
import django.db.models.deletion


OLD_CHOICES = {
    "sop": "SOP",
    "policy": "Policy",
    "procedure": "Procedure",
    "form": "Form",
    "general": "General Document",
}


def create_types_and_migrate_data(apps, schema_editor):
    DocumentType = apps.get_model("document_control", "DocumentType")
    ControlledDocument = apps.get_model("document_control", "ControlledDocument")
    type_map = {}
    for old_value, label in OLD_CHOICES.items():
        dt, _ = DocumentType.objects.get_or_create(name=label)
        type_map[old_value] = dt
    for doc in ControlledDocument.objects.all():
        dt = type_map.get(doc.document_type_old)
        doc.document_type = dt
        doc.save(update_fields=["document_type"])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("document_control", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="DocumentType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.RenameField(
            model_name="controlleddocument",
            old_name="document_type",
            new_name="document_type_old",
        ),
        migrations.AddField(
            model_name="controlleddocument",
            name="document_type",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name="documents", to="document_control.documenttype",
            ),
        ),
        migrations.AddField(
            model_name="controlleddocument",
            name="published_by",
            field=models.CharField(blank=True, choices=[("Client A", "Client A"), ("Client C", "Client C")], default="", max_length=20),
        ),
        migrations.AddField(
            model_name="controlleddocument",
            name="version",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.RunPython(create_types_and_migrate_data, reverse_noop),
        migrations.RemoveField(
            model_name="controlleddocument",
            name="document_type_old",
        ),
        migrations.AlterModelOptions(
            name="controlleddocument",
            options={"ordering": ["-date_of_issue", "name"]},
        ),
    ]
