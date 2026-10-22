"""Settings for the TESTER copy of the portal (see tester_copy/start_testers.sh).

Same portal, but safe to put behind a public https link: DEBUG is off (no debug pages), the address of the
https tunnel is trusted, and static files and uploads are served by the portal itself."""
from .settings import *  # noqa: F401,F403

DEBUG = False
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")      # the tunnel terminates https
SECURE_SSL_REDIRECT = False                                         # the same copy is also reachable by plain http on the LAN
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_HSTS_SECONDS = 0

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
WHITENOISE_USE_FINDERS = True          # serve the portal's own CSS / JS without running collectstatic
WHITENOISE_AUTOREFRESH = True
SERVE_MEDIA_LOCALLY = True             # uploads are served by the portal (after the usual download check)

SIGNUP_HINT = ("This is a test site with made-up people. Use the Employee ID you were given (T001 to T025). Your own Employee ID will not be found.")
