from django.urls import path

from . import views

app_name = "job_manager"

urlpatterns = [
    path("", views.job_list, name="list"),
    path("new/", views.job_create, name="create"),
    path("<int:pk>/", views.job_detail, name="detail"),
    path("<int:pk>/edit/", views.job_edit, name="edit"),
    path("<int:pk>/delete/", views.job_delete, name="delete"),
    path("<int:pk>/export/manifest/", views.job_export_manifest_xlsx, name="export_manifest"),
    path("<int:pk>/export/manifest-pdf/", views.job_export_manifest_pdf, name="export_manifest_pdf"),
    path("<int:pk>/import-serials/", views.job_manifest_import_serials, name="import_serials"),
    path("<int:pk>/export/certs/", views.job_export_certs_xlsx, name="export_certs"),
    path("<int:pk>/export/certs-zip/", views.job_export_certs_zip, name="export_certs_zip"),
]
