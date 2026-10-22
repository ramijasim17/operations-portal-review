"""Remembers who is making the current request, so a model being saved can say who did it."""
from contextvars import ContextVar

_actor = ContextVar("notification_actor", default=None)


def current_actor():
    return _actor.get()


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        token = _actor.set(user if user is not None and user.is_authenticated else None)
        try:
            return self.get_response(request)
        finally:
            _actor.reset(token)
