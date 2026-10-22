"""Watches saves and deletes across the portal's apps and raises notifications."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import FileField
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from . import services
from .middleware import current_actor

TRACKED_APPS = {
    "equipment", "departments", "maintenance", "wire_management", "sqb", "document_control", "work_approval",
    "action_tracker", "job_manager", "job_reports", "personnel", "sgs_report",
}


def _tracked(sender, raw=False):
    meta = sender._meta
    return not raw and meta.app_label in TRACKED_APPS and not meta.auto_created


def _has_new_file(instance):
    return any(isinstance(f, FileField) and getattr(instance, f.name) for f in instance._meta.get_fields() if hasattr(f, "name"))


@receiver(post_save, dispatch_uid="notifications_saved")
def saved(sender, instance, created, raw=False, **kwargs):
    if not _tracked(sender, raw):
        return
    actor = current_actor()
    label = sender._meta.label_lower
    if label == "action_tracker.action":
        _action_assignment(instance, created, actor)
    elif label == "job_reports.jobsupervisor" and created:
        _supervisor_assignment(instance, actor)
    if actor is None:
        return                      # only things people do in the portal are reported
    verb = "uploaded" if created and _has_new_file(instance) else "added" if created else "changed"
    services.record_change(instance, verb, actor)


@receiver(post_delete, dispatch_uid="notifications_deleted")
def deleted(sender, instance, **kwargs):
    if not _tracked(sender):
        return
    actor = current_actor()
    if actor is not None:
        services.record_change(instance, "deleted", actor)


@receiver(post_save, sender=get_user_model(), dispatch_uid="notifications_new_account")
def new_account(sender, instance, created, raw=False, update_fields=None, **kwargs):
    """Tell the managers about a new account once it can be used: created active, or just verified by email
    (an unverified sign-up that never gets its code is not worth an alert)."""
    just_verified = not created and update_fields is not None and "is_active" in update_fields
    if not raw and instance.is_active and (created or just_verified):
        from .models import Notification
        for user in services.managers():
            Notification.objects.create(recipient=user, kind=Notification.ACTIVITY, model_label="auth.user", verb="added",
                                        message=f"New account signed up: {instance.username}",
                                        link="/accounts/access-control/")


def _action_assignment(action, created, actor):
    """Tell the assignee when an action is given to them (new, or the assignee changed)."""
    previous = getattr(action, "_loaded_assignee", "")
    if action.assignee and (created or action.assignee != previous):
        services.assigned(
            services.user_for_name(action.assignee),
            f"{services.person(actor)} assigned you an action: “{action.action_item[:80]}”",
            services.page_for(action), actor)
    action._loaded_assignee = action.assignee


def _supervisor_assignment(assignment, actor):
    profile = getattr(assignment.person, "portal_profile", None)
    user = profile.user if profile and profile.is_active_employee else None
    shift = assignment.get_shift_display().lower()
    services.assigned(
        user,
        f"{services.person(actor)} put you on job “{assignment.job_report.name}” as supervisor ({shift}) from {assignment.start_date:%d %b %Y}",
        services.page_for(assignment), actor)
