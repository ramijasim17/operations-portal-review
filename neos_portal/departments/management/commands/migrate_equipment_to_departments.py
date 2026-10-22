from django.core.management.base import BaseCommand
from django.db import transaction

from departments.models import (
    Asset,
    AssetCert,
    AssetFieldValue,
    CertType,
    Classification,
    ClassificationCertRequirement,
    Department,
    FieldDefinition,
)
from equipment.models import (
    CATEGORY_FIELDS,
    COMMON_FIELDS,
    CategoryCertRequirement,
)
from equipment.models import CertType as OldCertType
from equipment.models import Equipment, EquipmentCert

DEPARTMENT_NAME = "Wireline & Slickline"
DEPARTMENT_LOCATION = "Main field"

FIELD_LABELS = {
    "manufacturer": "Manufacturer",
    "location": "Location",
    "in_operations": "In operations",
    "plate_number": "Plate number",
    "engine_model": "Engine model",
    "power_rating": "Power rating",
    "fuel_type": "Fuel type",
    "working_pressure": "Working pressure",
    "test_pressure": "Test pressure",
    "connection_top": "Connection top",
    "connection_bottom": "Connection bottom",
    "max_od": "Max OD",
    "id_inch": "ID (inch)",
    "length_val": "Length",
    "fish_neck": "Fish neck",
    "size": "Size",
    "swl": "SWL",
    "calibration_date": "Calibration date",
    "sensor_type": "Sensor type",
    "battery_type": "Battery type",
}

# Fields where we build the dropdown options from whatever values are
# actually present in your data, rather than guessing at a fixed list.
DROPDOWN_FIELDS = {"in_operations", "fuel_type"}


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


class Command(BaseCommand):
    help = (
        "One-time migration: copies every Equipment/EquipmentCert row into the new "
        "Department -> Classification -> Asset structure. Safe to re-run; existing "
        "rows are matched and updated rather than duplicated."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Print what would be migrated without writing anything.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN — no changes will be saved."))

        with transaction.atomic():
            department, created = Department.objects.get_or_create(
                name=DEPARTMENT_NAME, defaults={"location": DEPARTMENT_LOCATION}
            )
            self.stdout.write(f"Department: {department.name} ({'created' if created else 'existing'})")

            classification_map = {}
            fielddef_map = {}

            for category_value, extra_fields in CATEGORY_FIELDS.items():
                category = category_value.value
                classification, _ = Classification.objects.get_or_create(
                    department=department, name=category
                )
                classification_map[category] = classification

                old_field_names = COMMON_FIELDS + extra_fields
                equipment_qs = Equipment.objects.filter(category=category)

                for order, old_name in enumerate(old_field_names):
                    field_type, options = self._infer_field_type(equipment_qs, old_name)
                    fd, _ = FieldDefinition.objects.get_or_create(
                        classification=classification,
                        label=FIELD_LABELS.get(old_name, old_name.replace("_", " ").title()),
                        defaults={
                            "field_type": field_type,
                            "dropdown_options": ", ".join(options) if field_type == FieldDefinition.DROPDOWN else "",
                            "order": order,
                        },
                    )
                    fielddef_map[(category, old_name)] = fd

                asset_count = 0
                for eq in equipment_qs:
                    asset, _ = Asset.objects.get_or_create(
                        classification=classification, name=eq.serial_number
                    )
                    for old_name in old_field_names:
                        fd = fielddef_map[(category, old_name)]
                        raw_value = getattr(eq, old_name, "") or ""
                        afv, _ = AssetFieldValue.objects.get_or_create(asset=asset, field_definition=fd)
                        if fd.field_type == FieldDefinition.NUMBER:
                            afv.number_value = float(raw_value) if _is_number(raw_value) else None
                        else:
                            afv.text_value = raw_value
                        afv.save()
                    asset_count += 1
                self.stdout.write(f"  {category}: {asset_count} assets, {len(old_field_names)} fields")

            old_to_new_cert_type = {}
            for old_ct in OldCertType.objects.all():
                new_ct, _ = CertType.objects.get_or_create(name=old_ct.name)
                old_to_new_cert_type[old_ct.id] = new_ct
            self.stdout.write(f"Cert types: {len(old_to_new_cert_type)}")

            req_count = 0
            for req in CategoryCertRequirement.objects.all():
                classification = classification_map.get(req.category)
                new_ct = old_to_new_cert_type.get(req.cert_type_id)
                if not classification or not new_ct:
                    continue
                ClassificationCertRequirement.objects.get_or_create(
                    classification=classification, cert_type=new_ct
                )
                req_count += 1
            self.stdout.write(f"Cert requirements: {req_count}")

            cert_count = 0
            for old_cert in EquipmentCert.objects.select_related("equipment", "cert_type"):
                eq = old_cert.equipment
                classification = classification_map.get(eq.category)
                new_ct = old_to_new_cert_type.get(old_cert.cert_type_id)
                if not classification or not new_ct:
                    continue
                try:
                    asset = Asset.objects.get(classification=classification, name=eq.serial_number)
                except Asset.DoesNotExist:
                    continue
                not_applicable = bool(old_cert.file) and not old_cert.expiry_date
                AssetCert.objects.update_or_create(
                    asset=asset,
                    cert_type=new_ct,
                    defaults={
                        "cert_number": old_cert.cert_number,
                        "expiry_date": old_cert.expiry_date,
                        "not_applicable": not_applicable,
                        "certificate_file": old_cert.file,
                    },
                )
                cert_count += 1
            self.stdout.write(f"Asset certificates: {cert_count}")

            if dry_run:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING("Dry run complete — rolled back, nothing saved."))
            else:
                self.stdout.write(self.style.SUCCESS("Migration complete."))

    def _infer_field_type(self, equipment_qs, field_name):
        values = [getattr(e, field_name, "") for e in equipment_qs]
        non_empty = [v for v in values if v]

        if field_name in DROPDOWN_FIELDS:
            options = sorted(set(non_empty))
            if not options:
                options = ["Yes", "No"] if field_name == "in_operations" else ["Diesel", "Petrol"]
            return FieldDefinition.DROPDOWN, options

        if non_empty and all(_is_number(v) for v in non_empty):
            return FieldDefinition.NUMBER, []

        return FieldDefinition.TEXT, []
