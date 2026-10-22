"""Settings for `manage.py test`: same as production, but static files use
the plain storage so templates don't need a collectstatic manifest."""
from .settings import *  # noqa: F401,F403

STORAGES = {**STORAGES, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

DEPARTMENT_LOCK = True            # the tests check the locked behaviour
