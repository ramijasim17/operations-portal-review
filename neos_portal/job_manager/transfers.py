"""Moving equipment between manifests, or back to the Workshop."""
from django.db import transaction

from departments.models import Asset

from .models import EquipmentTransfer, JobManifest

WORKSHOP = "Workshop"


class TransferError(ValueError):
    pass


def manifest_label(manifest):
    return f"{manifest.job_name} ({manifest.job_number})"


@transaction.atomic
def transfer_serials(serials, source, destination, user=None, note=""):
    """Move `serials` off `source`.

    destination: another open JobManifest, or None to return them to the Workshop.
    Returns the list of serials actually moved. Raises TransferError for a
    destination that can't take equipment.
    """
    if destination is not None:
        if destination.pk == source.pk:
            raise TransferError("Choose a different manifest to transfer to.")
        if destination.status == "Completed":
            raise TransferError("That manifest is completed and can't receive equipment.")

    on_source = source.serial_list()
    moving = [s for s in dict.fromkeys(serials) if s in on_source]
    if not moving:
        raise TransferError("None of the selected items are on this manifest.")

    if destination is not None:
        already_there = set(destination.serial_list())
        moving_new = [s for s in moving if s not in already_there]
        destination.equipment_serials = ",".join(destination.serial_list() + moving_new)
        destination.save(update_fields=["equipment_serials"])
        if destination.status == "Active":
            Asset.objects.filter(name__in=moving).update(current_location=destination.location or "Dispatched")
    else:
        Asset.objects.filter(name__in=moving).update(current_location=WORKSHOP)

    source.equipment_serials = ",".join(s for s in on_source if s not in moving)
    source.save(update_fields=["equipment_serials"])

    EquipmentTransfer.objects.bulk_create([
        EquipmentTransfer(
            asset_name=serial, from_manifest=source, to_manifest=destination,
            from_label=manifest_label(source),
            to_label=manifest_label(destination) if destination else "Workshop",
            note=note[:255], moved_by=user if getattr(user, "pk", None) else None,
        )
        for serial in moving
    ])
    return moving
