"""Auto-create a Profile for every new User, so code can always safely
assume request.user.profile exists without a None-check everywhere."""

from django.conf import settings
from django.contrib.auth.signals import user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Profile


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_or_save_profile(sender, instance, created, raw=False, **kwargs):
    if raw:                         # loading a saved export (loaddata): the Profile rows come from the file
        return
    if created:
        Profile.objects.create(user=instance)
    else:
        # If a Profile is somehow missing (e.g. a user created before this
        # app existed), create it rather than raising an error.
        Profile.objects.get_or_create(user=instance)


@receiver(user_logged_out)
def clear_last_seen(sender, request, user, **kwargs):
    """Signing out means "not online" straight away, instead of waiting for the few-minute window to pass."""
    if user is not None:
        Profile.objects.filter(user=user).update(last_seen=None)
