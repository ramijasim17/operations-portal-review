from django.conf import settings
from django.http import Http404


def allowed(user):
    """The whiteboard is a one-person tool: the accounts named in WHITEBOARD_USERNAMES
    (comma separated), or -- when that is empty -- superusers."""
    if not getattr(user, "is_authenticated", False):
        return False
    names = {n.strip().lower() for n in getattr(settings, "WHITEBOARD_USERNAMES", "").split(",") if n.strip()}
    if names:
        return user.username.lower() in names
    return user.is_superuser


def require_access(view):
    def wrapper(request, *args, **kwargs):
        if not allowed(request.user):
            raise Http404()          # nobody else even learns the page exists
        return view(request, *args, **kwargs)
    wrapper.__name__ = view.__name__
    return wrapper
