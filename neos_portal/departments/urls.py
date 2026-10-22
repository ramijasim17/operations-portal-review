from django.urls import path

from . import views

app_name = "departments"

urlpatterns = [
    path("", views.department_list, name="list"),
    path("bulk-cert-upload/", views.bulk_cert_upload, name="bulk_cert_upload"),
    path("bulk-cert-upload/<str:batch_id>/preview/", views.bulk_cert_preview, name="bulk_cert_preview"),
    path("bulk-cert-upload/<str:batch_id>/confirm/", views.bulk_cert_confirm, name="bulk_cert_confirm"),
    path("<int:pk>/", views.department_detail, name="detail"),
    path("<int:pk>/edit/", views.department_edit, name="edit"),
    path("<int:pk>/delete/", views.department_delete, name="delete"),

    path("classification/<int:pk>/", views.classification_detail, name="classification_detail"),
    path("classification/<int:pk>/fields/", views.classification_fields, name="classification_fields"),
    path("classification/<int:pk>/edit/", views.classification_edit, name="classification_edit"),
    path("classification/<int:pk>/delete/", views.classification_delete, name="classification_delete"),
    path("classification/<int:pk>/export/", views.classification_export_xlsx, name="classification_export"),
    path("classification/<int:pk>/export-selected/", views.classification_export_selected_xlsx, name="classification_export_selected"),
    path("classification/<int:pk>/template/", views.classification_template_xlsx, name="classification_template"),
    path("classification/<int:pk>/import/", views.classification_import_xlsx, name="classification_import"),
    path("classification/<int:pk>/certtypes/", views.classification_certtypes, name="classification_certtypes"),

    path("field/<int:pk>/delete/", views.field_delete, name="field_delete"),

    path("classification/<int:classification_pk>/asset/new/", views.asset_create, name="asset_create"),
    path("asset/<int:pk>/", views.asset_detail, name="asset_detail"),
    path("asset/<int:pk>/edit/", views.asset_edit, name="asset_edit"),
    path("asset/<int:pk>/delete/", views.asset_delete, name="asset_delete"),
    path("asset/<int:pk>/request-removal/", views.asset_request_removal, name="asset_request_removal"),

    path("asset/<int:asset_pk>/cert/new/", views.asset_cert_create, name="asset_cert_create"),
    path("cert/<int:pk>/edit/", views.asset_cert_edit, name="asset_cert_edit"),

    path("cert-types/", views.cert_type_manage, name="cert_type_manage"),
    path("cert-types/<int:pk>/delete/", views.cert_type_delete, name="cert_type_delete"),
]
