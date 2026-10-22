from django.contrib.contenttypes.fields import GenericRelation
from django.db import models
from django.utils import timezone

from departments.models import Asset, Department

STATUS_CHOICES = [("Draft", "Draft"), ("Active", "Active"), ("Completed", "Completed")]


class JobManifest(models.Model):
    job_number = models.CharField(max_length=150, unique=True, blank=True, editable=False)
    job_name = models.CharField(max_length=255)
    client = models.CharField(max_length=255, blank=True, default="")
    date = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="Draft")
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True,
        related_name="job_manifests",
        help_text="Which department's equipment this manifest draws from.",
    )
    equipment_serials = models.TextField(
        blank=True, default="",
        help_text="Comma-separated Asset No / Serial No values, linked to Departments",
    )

    approvals = GenericRelation("approvals.Approval")      # waiting / approved / rejected, see approvals/

    class Meta:
        ordering = ["-date", "job_name"]

    def __str__(self):
        return f"{self.job_number} \u2014 {self.job_name}" if self.job_number else self.job_name

    def _generate_job_number(self):
        date_str = (self.date or timezone.localdate()).strftime("%Y%m%d")
        dept_name = self.department.name if self.department else "NoDept"
        client_name = self.client or "NoClient"
        base = f"Job-manifest_{date_str}_{dept_name}_{client_name}"
        # The requested format has no built-in disambiguator, so if two
        # manifests would otherwise collide (same date/department/client),
        # append a small suffix rather than crash on the unique constraint.
        candidate = base
        suffix = 2
        while JobManifest.objects.filter(job_number=candidate).exclude(pk=self.pk).exists():
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new and not self.job_number:
            # department/client/date are already set on self at this point
            # (they come from the form before save() is called), so the
            # number can be generated in one save -- no need for the
            # two-step pk-based pattern used for the old JM-YYYY-NNNNN format.
            self.job_number = self._generate_job_number()
        super().save(*args, **kwargs)

    @classmethod
    def reserved_map(cls, exclude_pk=None):
        """{serial: manifest} for every equipment item that is on a manifest
        that isn't Completed. Such items are taken: no other manifest can
        pick them until the manifest is completed, they are removed, or they
        are transferred."""
        taken = {}
        manifests = cls.objects.exclude(status="Completed")
        if exclude_pk is not None:
            manifests = manifests.exclude(pk=exclude_pk)
        for manifest in manifests.only("id", "job_number", "job_name", "equipment_serials"):
            for serial in manifest.serial_list():
                taken.setdefault(serial, manifest)
        return taken

    def serial_list(self):
        return [s.strip() for s in self.equipment_serials.split(",") if s.strip()]

    def assets(self):
        serials = self.serial_list()
        if not serials:
            return Asset.objects.none()
        return Asset.objects.filter(name__in=serials).select_related(
            "classification", "classification__department"
        )

    def dispatch_equipment(self):
        """Moves every asset currently on this manifest to the manifest's
        own location. Called when status becomes Active."""
        serials = self.serial_list()
        if serials:
            Asset.objects.filter(name__in=serials).update(
                current_location=self.location or "Dispatched"
            )

    def return_equipment(self):
        """Moves every asset on this manifest back to Workshop. Called
        when status becomes Completed."""
        serials = self.serial_list()
        if serials:
            Asset.objects.filter(name__in=serials).update(current_location="Workshop")


class EquipmentTransfer(models.Model):
    """One line of history: an item moved between manifests / the Workshop."""
    asset_name = models.CharField(max_length=150, help_text="Asset No / Serial No at the time of the move")
    from_manifest = models.ForeignKey(
        JobManifest, null=True, blank=True, on_delete=models.SET_NULL, related_name="transfers_out")
    to_manifest = models.ForeignKey(
        JobManifest, null=True, blank=True, on_delete=models.SET_NULL, related_name="transfers_in")
    from_label = models.CharField(max_length=255, blank=True, default="")
    to_label = models.CharField(max_length=255, blank=True, default="")
    note = models.CharField(max_length=255, blank=True, default="")
    moved_by = models.ForeignKey(
        "auth.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="equipment_transfers")
    moved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-moved_at", "-id"]

    def __str__(self):
        return f"{self.asset_name}: {self.from_label} \u2192 {self.to_label}"
