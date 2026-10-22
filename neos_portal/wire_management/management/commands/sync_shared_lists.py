"""Fill the shared Client / Field / Location lists from values already typed
into Job Manager, SGS Reports, SQB and Departments. Safe to run repeatedly:
it only adds names that aren't in a list yet.

    python manage.py sync_shared_lists
"""
from django.core.management.base import BaseCommand

from wire_management.lists import KINDS, _columns


class Command(BaseCommand):
    help = "Add every client / field / location already in use to the shared lists."

    def handle(self, *args, **options):
        for kind, (model, label, _cols) in KINDS.items():
            existing = {n.casefold() for n in model.objects.values_list("name", flat=True)}
            found = set()
            for source, column in _columns(kind):
                for value in source.objects.values_list(column, flat=True).distinct():
                    value = (value or "").strip()
                    # "Workshop" is where equipment sits by default, not a job location.
                    if value and value.casefold() not in existing and not (kind == "location" and value == "Workshop"):
                        found.add(value)
            for value in sorted(found, key=str.casefold):
                if value.casefold() not in existing:
                    model.objects.create(name=value)
                    existing.add(value.casefold())
            self.stdout.write(f"{label}: {len(found)} added, {model.objects.count()} total")
