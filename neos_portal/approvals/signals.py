"""A Level 2 person creating a manifest, asset or person puts it up for approval automatically."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from notifications.middleware import current_actor

from . import services
from .registry import AUTO_KINDS


@receiver(post_save, dispatch_uid="approvals_new_record")
def new_record(sender, instance, created, raw=False, **kwargs):
    if raw or not created:
        return
    kind = AUTO_KINDS.get(sender._meta.label)
    if kind is None:
        return
    actor = current_actor()
    if actor is None:                  # a script or an import run outside the portal: counts as approved
        return
    services.submit(instance, kind.key, actor)


# ── SGS: editing approved work sends it back for approval ───────────────────────────────────────────────
from django.db.models.signals import post_delete  # noqa: E402
from django.apps import apps  # noqa: E402


def _reopen(report, kind):
    actor = current_actor()
    if actor is not None and report is not None:
        services.reopen(report, kind, actor)


def _connect():
    Station = apps.get_model("sgs_report", "SGSStation")
    Entry = apps.get_model("sgs_report", "SGSSequenceEntry")
    Shift = apps.get_model("sgs_report", "SGSShift")
    Image = apps.get_model("sgs_report", "SGSImage")

    def result_changed(sender, instance, **kw):
        _reopen(instance.report, "sgs_result")

    def final_changed_entry(sender, instance, **kw):
        _reopen(instance.shift.report, "sgs_final")

    def final_changed(sender, instance, **kw):
        _reopen(instance.report, "sgs_final")

    for sig in (post_save, post_delete):
        sig.connect(result_changed, sender=Station, dispatch_uid=f"appr_station_{sig}")
        sig.connect(final_changed_entry, sender=Entry, dispatch_uid=f"appr_entry_{sig}")
        sig.connect(final_changed, sender=Shift, dispatch_uid=f"appr_shift_{sig}")
        sig.connect(final_changed, sender=Image, dispatch_uid=f"appr_image_{sig}")


_connect()
