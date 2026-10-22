from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.ThrottledLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", views.signup, name="signup"),
    path("verify/", views.verify, name="verify"),
    path("verify/resend/", views.resend_code, name="resend_code"),
    path("access-control/", views.access_control, name="access_control"),
    path("backup/", views.backup, name="backup"),
    path("backup/download/", views.backup_download, name="backup_download"),
    path("backup/preview/", views.backup_preview, name="backup_preview"),
    path("backup/sync/manifest/", views.backup_sync_manifest, name="backup_sync_manifest"),
    path("backup/sync/file/", views.backup_sync_file, name="backup_sync_file"),
    path("backup/sync/database/", views.backup_sync_database, name="backup_sync_database"),
    path("backup/sync/code/", views.backup_sync_code, name="backup_sync_code"),
    path("backup/sync/done/", views.backup_sync_done, name="backup_sync_done"),
    path("cleanup/", views.cleanup, name="cleanup"),
    path("cleanup/trash/", views.cleanup_trash, name="cleanup_trash"),
    path("cleanup/<int:pk>/restore/", views.cleanup_restore, name="cleanup_restore"),
    path("cleanup/<int:pk>/purge/", views.cleanup_purge, name="cleanup_purge"),
    path("password-reset/", auth_views.PasswordResetView.as_view(
        template_name="accounts/password_reset.html"), name="password_reset"),
    path("password-reset/done/", auth_views.PasswordResetDoneView.as_view(
        template_name="accounts/password_reset_done.html"), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", auth_views.PasswordResetConfirmView.as_view(
        template_name="accounts/password_reset_confirm.html"), name="password_reset_confirm"),
    path("reset/done/", auth_views.PasswordResetCompleteView.as_view(
        template_name="accounts/password_reset_complete.html"), name="password_reset_complete"),
]
