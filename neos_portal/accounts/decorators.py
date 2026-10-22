"""
Role-gating decorator for views that need MORE than "just logged in"
(which LoginRequiredMiddleware already guarantees for every view).

Usage:
    @role_required("admin")
    def settings_view(request): ...

    @role_required("admin", "editor")
    def upload_view(request): ...
"""

from functools import wraps
from django.core.exceptions import PermissionDenied


def role_required(*allowed_roles):
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            profile = getattr(request.user, "profile", None)
            if not profile or profile.role not in allowed_roles:
                raise PermissionDenied("Your account role doesn't have access to this page.")
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorator
