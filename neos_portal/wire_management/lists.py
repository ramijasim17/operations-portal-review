"""Shared Client / Field / Location lists.

One managed list per kind, used as a dropdown on every page that asks for a
client, field or location (Job Manager, SGS Reports, SQB, Wire Management).
The pages keep storing the chosen *name* in their existing text columns, so
reports, exports and searches keep working unchanged. Renaming a list entry
updates every page that uses the old name.
"""
from django import forms
from django.apps import apps
from django.db import transaction

from .models import Client, FieldWell, Location

# kind -> (model, label, text columns "app.Model.column" that store the name)
KINDS = {
    "client": (Client, "Clients", [
        "job_manager.JobManifest.client", "sgs_report.SGSReport.client", "sqb.SQBTrackerEntry.client",
        "action_tracker.Action.client",
    ]),
    "field": (FieldWell, "Fields / Wells", ["sgs_report.SGSReport.field"]),
    "location": (Location, "Locations", [
        "job_manager.JobManifest.location", "departments.Asset.current_location",
    ]),
}


def _columns(kind):
    for path in KINDS[kind][2]:
        app_label, model_name, column = path.split(".")
        yield apps.get_model(app_label, model_name), column


def usage_count(kind, name):
    """How many records on other pages currently use this name."""
    return sum(model.objects.filter(**{column: name}).count() for model, column in _columns(kind))


@transaction.atomic
def rename(kind, obj, new_name):
    """Rename a list entry and every stored copy of the old name."""
    old_name = obj.name
    obj.name = new_name
    obj.save(update_fields=["name"])
    if old_name != new_name:
        for model, column in _columns(kind):
            model.objects.filter(**{column: old_name}).update(**{column: new_name})


def names(kind):
    return list(KINDS[kind][0].objects.values_list("name", flat=True))


class SharedListFieldsMixin:
    """Turns the named text fields of a ModelForm into dropdowns fed by the
    shared lists. A value already saved on the record but missing from the
    list is kept as an option, so editing old data never blanks it."""

    shared_list_fields = {}  # form field name -> kind

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, kind in self.shared_list_fields.items():
            if field_name not in self.fields:
                continue
            original = self.fields[field_name]
            options = names(kind)
            current = (self.initial.get(field_name) or getattr(self.instance, field_name, "") or "").strip()
            if current and current not in options:
                options.append(current)
            self.fields[field_name] = forms.ChoiceField(
                label=original.label,
                required=original.required,
                choices=[("", "— select —")] + [(n, n) for n in options],
            )
