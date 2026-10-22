from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Notification

PAGE_SIZE = 100


def notification_list(request):
    show = request.GET.get("show", "all")
    kind = request.GET.get("kind", "")
    mine = Notification.objects.filter(recipient=request.user).select_related("actor")
    rows = mine
    if show == "unread":
        rows = rows.filter(read_at__isnull=True)
    if kind in (Notification.ACTIVITY, Notification.ASSIGNED, Notification.APPROVAL):
        rows = rows.filter(kind=kind)
    return render(request, "notifications/list.html", {
        "rows": list(rows[:PAGE_SIZE]), "show": show, "kind": kind,
        "unread": mine.filter(read_at__isnull=True).count(),
        "assigned_unread": mine.filter(read_at__isnull=True, kind=Notification.ASSIGNED).count(),
        "total": mine.count(),
    })


def notification_open(request, pk):
    """Mark it read and go to the page it is about."""
    n = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if n.read_at is None:
        n.read_at = timezone.now()
        n.save(update_fields=["read_at"])
    return redirect(n.link or "notifications:list")


@require_POST
def mark_all_read(request):
    Notification.objects.filter(recipient=request.user, read_at__isnull=True).update(read_at=timezone.now())
    return redirect("notifications:list")


def poll(request):
    """Polled by the page every half minute: the unread count, plus anything newer than `after`."""
    mine = Notification.objects.filter(recipient=request.user, read_at__isnull=True)
    after = request.GET.get("after", "")
    fresh = mine.filter(pk__gt=int(after)) if after.isdigit() else mine.none()
    response = JsonResponse({
        "count": mine.count(),
        "latest": mine.values_list("pk", flat=True).first() or 0,
        "new": [{"id": n.pk, "message": n.message, "url": f"/notifications/{n.pk}/open/", "kind": n.kind}
                for n in fresh.order_by("pk")[:3]],
    })
    response["Cache-Control"] = "no-store"      # an iPad must never reuse an old answer
    return response
