from .models import Approval


def waiting(request):
    """How many items are waiting for a decision -- shown on the sidebar for Level 3 and admins only."""
    user = getattr(request, "user", None)
    profile = getattr(user, "profile", None) if user is not None and user.is_authenticated else None
    if not (profile and profile.can_approve):
        return {}
    return {"pending_approvals": Approval.objects.filter(status=Approval.PENDING).count()}
