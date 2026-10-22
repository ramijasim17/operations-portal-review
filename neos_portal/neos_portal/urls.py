from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.urls import re_path
from django.views.static import serve

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("departments/", include("departments.urls")),
    path("maintenance/", include("maintenance.urls")),
    path("sqb/", include("sqb.urls")),
    path("work-approval/", include("work_approval.urls")),
    path("actions/", include("action_tracker.urls")),
    path("jobs/", include("job_manager.urls")),
    path("personnel/", include("personnel.urls")),
    path("", include("core.urls")),
    path("job-reports/", include("job_reports.urls")),
    path("notifications/", include("notifications.urls")),
    path("approvals/", include("approvals.urls")),
    path("sgs-report/", include("sgs_report.urls")),
    path("documents/", include("document_control.urls")),
    path("qhse/", include("qhse.urls")),
    path("whiteboard/", include("whiteboard.urls")),
    path("wire-management/", include("wire_management.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

if not settings.USE_S3:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    if getattr(settings, "SERVE_MEDIA_LOCALLY", False) and not settings.DEBUG:        # the tester copy (DEBUG off)
        urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT})]