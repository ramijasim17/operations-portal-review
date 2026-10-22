"""
Django settings for the ABC Operations Portal.

This is the Django equivalent of what your Streamlit app.py did with
st.secrets — every value that differs between your machine and production
(database credentials, storage keys, the secret key) comes from environment
variables, never hardcoded here. Locally, put them in a `.env` file (see
`.env.example`); on Render, you'll set them in the dashboard's Environment
tab instead.
"""

from pathlib import Path
from decouple import config
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent

# ── Core ─────────────────────────────────────────────────────────────────
SECRET_KEY = config("DJANGO_SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)

# Comma-separated list of allowed hostnames, e.g. "portal.example.com,localhost"
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",")
CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS", default="https://portal.example.com"
).split(",")

INSTALLED_APPS = [

    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "storages",
    "accounts",
    "core",
    "equipment",
    "departments",
    "maintenance",
    "wire_management",
    "sqb",
    "document_control",
    "work_approval",
    "action_tracker",
    "job_manager",
    "job_reports",
    "personnel",
    "sgs_report",
    "notifications",
    "approvals",
    "whiteboard",
    "qhse",
]


MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "notifications.middleware.CurrentUserMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Every logged-in view still requires an active account — this
    # middleware is the actual "access control" the whole migration is for.
    "accounts.middleware.ModalRedirectMiddleware",
    "accounts.middleware.LoginRequiredMiddleware",
    "accounts.middleware.UploadGuardMiddleware",
    "accounts.middleware.LastSeenMiddleware",
]

ROOT_URLCONF = "neos_portal.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "core" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.nav_items",
                "core.context_processors.asset_version",
                "approvals.context_processors.waiting",
                "notifications.context_processors.unread",
            ],
        },
    },
]

WSGI_APPLICATION = "neos_portal.wsgi.application"

# ── Database — PostgreSQL via DATABASE_URL (Render provides this) ─────────
DATABASES = {
    "default": dj_database_url.config(
        default=config("DATABASE_URL"),
        conn_max_age=600,
        ssl_require=not DEBUG,
    )
}

AUTHENTICATION_BACKENDS = ["accounts.backends.FlexibleLoginBackend"]   # sign in with username, Employee ID or email

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ── Sign-up ──────────────────────────────────────────────────────────────
# Anyone can open the sign-up page, but the Employee ID and full name must
# match an Active record in Personnel (see accounts/forms.py). Optionally
# also limit the email domain (comma-separated, e.g. "example.com");
# leave empty to accept any email address.
SIGNUP_ALLOWED_DOMAINS = tuple(
    d.strip().lower().lstrip("@")
    for d in config("SIGNUP_ALLOWED_DOMAINS", default="").split(",")
    if d.strip()
)

# ── Uploads ──────────────────────────────────────────────────────────────
UPLOAD_MAX_BYTES = config("UPLOAD_MAX_MB", default=25, cast=int) * 1024 * 1024
UPLOAD_ALLOWED_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".log",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip",
)
# Departments: when on, Level 1 and 2 see only their own department in Action Tracker, QHSE, Maintenance and Personnel.
# Leave it off until every person's department (on their Personnel record) matches a Department name -- see
# `python manage.py department_report` -- then set DEPARTMENT_LOCK=True. With it off, everyone can use the Department box.
DEPARTMENT_LOCK = config("DEPARTMENT_LOCK", default=False, cast=bool)

# Who can use the private Whiteboard: comma-separated usernames (empty = superusers only).
WHITEBOARD_USERNAMES = config("WHITEBOARD_USERNAMES", default="")
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024  # non-file form data
DATA_UPLOAD_MAX_NUMBER_FIELDS = 5000  # bulk certificate forms post many fields

# ── HTTPS / cookie hardening (production only) ────────────────────────────
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # Render terminates TLS
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30  # raise to a year once you're sure
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12  # sign out after 12 hours

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"

LANGUAGE_CODE = "en-us"
TIME_ZONE = config("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

# ── Static files (served via WhiteNoise — no separate static host needed) ─
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ── File storage — S3-compatible object storage for uploaded PDFs ─────────
# Works with AWS S3, Cloudflare R2, Backblaze B2, or any S3-compatible
# provider — just point AWS_S3_ENDPOINT_URL at the provider you choose.
# This replaces the Google Drive integration your Streamlit app used.
AWS_ACCESS_KEY_ID = config("AWS_ACCESS_KEY_ID", default="")
AWS_SECRET_ACCESS_KEY = config("AWS_SECRET_ACCESS_KEY", default="")
AWS_STORAGE_BUCKET_NAME = config("AWS_STORAGE_BUCKET_NAME", default="")
AWS_S3_ENDPOINT_URL = config("AWS_S3_ENDPOINT_URL", default="")
AWS_S3_REGION_NAME = config("AWS_S3_REGION_NAME", default="auto")
AWS_S3_FILE_OVERWRITE = False
AWS_DEFAULT_ACL = "private"
AWS_QUERYSTRING_EXPIRE = 3600  # signed URLs expire after 1 hour


# ── Email — for signup verification codes ─────────────────────────────────
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_TIMEOUT = 10        # a blocked or slow mail server fails in 10 s (with a message), instead of hanging until the worker is killed
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = EMAIL_HOST_USER

# Only use S3 when real credentials are configured (i.e. in production).
# Locally, with no bucket/endpoint set, fall back to normal disk storage
# under MEDIA_ROOT so file uploads (attachments, certs) work in dev
# without needing S3 credentials at all.
USE_S3 = bool(AWS_STORAGE_BUCKET_NAME and AWS_S3_ENDPOINT_URL)

if USE_S3:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }
else:
    MEDIA_URL = "media/"
    MEDIA_ROOT = Path(config("MEDIA_ROOT", default=str(BASE_DIR / "media")))     # the tester copy points this at its own folder
    STORAGES = {
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
        },
        "staticfiles": {
            "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
        },
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"