from django.urls import path

from . import views

app_name = "personnel"

urlpatterns = [
    path("", views.personnel_list, name="list"),
    path("new/", views.personnel_create, name="create"),
    path("<int:pk>/", views.personnel_detail, name="detail"),
    path("<int:pk>/edit/", views.personnel_edit, name="edit"),
    path("<int:pk>/delete/", views.personnel_delete, name="delete"),
    path("<int:pk>/request-removal/", views.personnel_request_removal, name="request_removal"),
    path("certs/", views.cert_matrix, name="cert_matrix"),
    path("certs/new/", views.cert_create, name="cert_create"),
    path("certs/<int:pk>/edit/", views.cert_edit, name="cert_edit"),
    path("courses/", views.course_manage, name="course_manage"),
    path("courses/<int:pk>/delete/", views.course_delete, name="course_delete"),
    path("courses/save-required/", views.required_courses_save, name="required_courses_save"),
    path("courses/<int:course_id>/certs-zip/", views.course_certs_zip_export, name="course_certs_zip"),
    path("import-csv/", views.csv_import, name="csv_import"),
    path("bulk-cert-upload/", views.personnel_bulk_cert_upload, name="bulk_cert_upload"),
    path("bulk-cert-upload/<str:batch_id>/preview/", views.personnel_bulk_cert_preview, name="bulk_cert_preview"),
    path("bulk-cert-upload/<str:batch_id>/confirm/", views.personnel_bulk_cert_confirm, name="bulk_cert_confirm"),
]
