"""
LoginRequiredMiddleware — this is the actual "control access" mechanism.

By default, EVERY view in the portal requires a logged-in, active account.
Instead of remembering to add @login_required to every single view (easy
to forget on one and accidentally leave a page public), this middleware
makes "logged in" the default and lets you explicitly opt individual paths
OUT via the prefix list below — e.g. the login page itself, sign-up and
email verification (which a logged-out person must be able to reach), and
Django's password-reset flow.

Note: this deliberately checks request.path against fixed prefixes, NOT
request.resolver_match — resolver_match isn't populated yet at the point
this middleware's __call__ runs (it's set later, right before the view
itself executes), so checking it here always returns None. That bug, if
shipped, would have made the login page redirect to itself in an infinite
loop, locking every single user out. Caught by testing before deploy.
"""

from django.conf import settings
from django.utils import timezone
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from .policy import enforce

EXEMPT_PATH_PREFIXES = (
    "/accounts/login",
    "/accounts/signup",
    "/accounts/verify",
    "/accounts/password-reset",
    "/accounts/reset/",
)


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        is_exempt = (
            any(path.startswith(p) for p in EXEMPT_PATH_PREFIXES)
            or path.startswith("/admin/")
            or path.startswith(settings.STATIC_URL)
        )
        # Django admin does its own login, but a deactivated employee must
        # not get in through it either.
        is_admin_path = path.startswith("/admin/")

        if not request.user.is_authenticated:
            if not is_exempt:
                return redirect_to_login(request.get_full_path(), login_url=reverse("accounts:login"))

        elif (not is_exempt or is_admin_path) and (
            not getattr(request.user, "profile", None) or not request.user.profile.is_active_employee
        ):
            # A real Django account that's been deactivated in Profile
            # (e.g. someone who left the company) — logged out immediately,
            # rather than just hidden behind a permission check that a
            # future developer might forget to add somewhere.
            from django.contrib.auth import logout
            logout(request)
            return redirect_to_login(request.get_full_path(), login_url=reverse("accounts:login"))

        elif not is_exempt:
            # Logged in, active, and not on an exempt path: apply the
            # View / Edit / Admin rules in accounts/policy.py.
            denied = enforce(request)
            if denied is not None:
                return denied

        return self.get_response(request)


class UploadGuardMiddleware:
    """Rejects oversized uploads and file types the portal never needs
    (HTML, SVG, scripts, executables...), for every form in one place."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.content_type == "multipart/form-data" and request.user.is_authenticated:
            max_bytes = settings.UPLOAD_MAX_BYTES
            for uploaded in request.FILES.values():
                name = uploaded.name.lower()
                if uploaded.size > max_bytes:
                    return self._reject(request, f"'{uploaded.name}' is larger than {max_bytes // (1024 * 1024)} MB.")
                if not name.endswith(settings.UPLOAD_ALLOWED_EXTENSIONS):
                    return self._reject(request, f"'{uploaded.name}' is not an allowed file type.")
        return self.get_response(request)

    @staticmethod
    def _reject(request, reason):
        return render(request, "accounts/denied.html", {"reason": f"Upload refused: {reason}"}, status=400)


class ModalRedirectMiddleware:
    """Popup forms submit with fetch(). If the view answers with a redirect
    (saved OK), hand the target back as JSON instead of following it, so the
    success message stays queued for the page the browser really loads."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.headers.get("X-Modal") == "1" and response.status_code in (301, 302, 303, 307, 308):
            return JsonResponse({"redirect": response["Location"]})
        return response


class LastSeenMiddleware:
    """Records that a signed-in person has the portal open (powers "who is online"). Written at most once a
    minute per person, with a plain UPDATE, so it adds almost nothing to a request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            profile = getattr(user, "profile", None)
            if profile is not None:
                now = timezone.now()
                if profile.last_seen is None or now - profile.last_seen >= profile.SEEN_REFRESH:
                    type(profile).objects.filter(pk=profile.pk).update(last_seen=now)
                    profile.last_seen = now
        return self.get_response(request)
