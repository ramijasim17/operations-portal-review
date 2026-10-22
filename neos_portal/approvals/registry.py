"""What can be approved, and what approving it does."""
from dataclasses import dataclass
from typing import Callable, Optional

from django.apps import apps
from django.urls import reverse


class ApprovalError(Exception):
    """An approval could not be carried out (e.g. an asset that is still in use can't be removed)."""


@dataclass
class Kind:
    key: str
    label: str
    model: str                                  # "app.Model"
    describe: Callable
    url: Callable
    auto: bool = True                           # submitted automatically when a Level 2 creates the record
    removal: bool = False                       # approving it removes the record
    hint: str = ""
    on_approved: Optional[Callable] = None      # runs after approval (obj, user)

    def model_class(self):
        return apps.get_model(self.model)


def _delete(obj, user):
    from django.db.models import ProtectedError
    try:
        obj.delete()
    except ProtectedError as e:
        raise ApprovalError("It is still in use, so it can't be removed yet.") from e


def _apply_maintenance(log, user):
    apply = getattr(log, "apply_to_schedule", None)
    if apply:
        apply()


KINDS = {k.key: k for k in (
    Kind("manifest", "Job manifest", "job_manager.JobManifest",
         describe=lambda o: str(o), url=lambda o: reverse("job_manager:detail", args=[o.pk]),
         hint="Can't be dispatched, and no job can be made from it, until approved."),
    Kind("asset", "New asset", "departments.Asset",
         describe=lambda o: f"{o.name} — {o.classification}", url=lambda o: reverse("departments:classification_detail", args=[o.classification_id]),
         hint="Can't go on a manifest until approved."),
    Kind("asset_removal", "Asset removal", "departments.Asset", auto=False, removal=True,
         describe=lambda o: f"{o.name} — {o.classification}", url=lambda o: reverse("departments:classification_detail", args=[o.classification_id]),
         hint="Approving deletes the asset.", on_approved=_delete),
    Kind("personnel", "New person", "personnel.Personnel",
         describe=lambda o: f"{o.name} (ID {o.employee_id})", url=lambda o: reverse("personnel:detail", args=[o.pk]),
         hint="Can't be put on a job or assigned work until approved."),
    Kind("personnel_removal", "Person removal", "personnel.Personnel", auto=False, removal=True,
         describe=lambda o: f"{o.name} (ID {o.employee_id})", url=lambda o: reverse("personnel:detail", args=[o.pk]),
         hint="Approving deletes the person.", on_approved=_delete),
    Kind("maintenance_log", "Maintenance report", "maintenance.MaintenanceLog", auto=False,
         describe=lambda o: str(o), url=lambda o: reverse("maintenance:list"),
         hint="The schedule only moves on once it is approved.", on_approved=_apply_maintenance),
    Kind("site_visit", "Site visit report", "qhse.SiteVisit", auto=False,
         describe=lambda o: f"{o.reference} — {o.site}", url=lambda o: reverse("qhse:visit_detail", args=[o.pk]),
         hint="The report is locked once submitted, and released when approved."),
    Kind("sgs_result", "SGS result", "sgs_report.SGSReport", auto=False,
         describe=lambda o: f"{o.well_name} — results", url=lambda o: reverse("sgs_report:detail", args=[o.pk]) + "?tab=result",
         hint="The Excel result and the client data files are released once approved."),
    Kind("sgs_final", "SGS final report", "sgs_report.SGSReport", auto=False,
         describe=lambda o: f"{o.well_name} — final report", url=lambda o: reverse("sgs_report:detail", args=[o.pk]) + "?tab=final",
         hint="The Word and PDF report are released once approved."),
)}

AUTO_KINDS = {k.model: k for k in KINDS.values() if k.auto}
