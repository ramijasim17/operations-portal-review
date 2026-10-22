from django.urls import path

from . import views

app_name = "wire_management"

urlpatterns = [
    path("lists/", views.shared_lists_page, name="lists"),
    path("lists/<str:kind>/<int:pk>/edit/", views.shared_list_edit, name="list_edit"),
    path("lists/<str:kind>/<int:pk>/delete/", views.shared_list_delete, name="list_delete"),
    path("", views.drum_list, name="list"),
    path("<int:pk>/", views.drum_detail, name="detail"),
    path("<int:pk>/edit/", views.drum_edit, name="edit"),
    path("<int:pk>/delete/", views.drum_delete, name="delete"),
    path("<int:pk>/evidence-pdf/", views.drum_evidence_combined_pdf, name="drum_evidence_pdf"),
    path("<int:pk>/import-excel/", views.drum_import_excel, name="drum_import_excel"),
    path("entry/<int:pk>/edit/", views.entry_edit, name="entry_edit"),
    path("entry/<int:pk>/delete/", views.entry_delete, name="entry_delete"),
    path("entry/<int:pk>/evidence-pdf/", views.entry_evidence_combined_pdf, name="entry_evidence_pdf"),
    path("evidence/<int:pk>/delete/", views.evidence_delete, name="evidence_delete"),
    path("clients/", views.client_manage, name="client_manage"),
    path("clients/<int:pk>/delete/", views.client_delete, name="client_delete"),
    path("fields/", views.field_well_manage, name="field_well_manage"),
    path("fields/<int:pk>/delete/", views.field_well_delete, name="field_well_delete"),
    path("import-template.xlsx", views.import_template_xlsx, name="import_template"),
]
