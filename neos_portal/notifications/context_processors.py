from .models import Notification


def unread(request):
    """Unread count for the bell in the sidebar (one small query, only for signed-in users)."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    qs = Notification.objects.filter(recipient=user, read_at__isnull=True)
    latest = qs.values_list("id", flat=True).first()
    return {"unread_count": qs.count(), "unread_latest_id": latest or 0}
