from django.urls import path

from . import views

app_name = "sqb"

urlpatterns = [
    path("", views.job_list, name="job_list"),

    path("create/<int:job_report_pk>/", views.package_create, name="package_create"),
    path("package/<int:pk>/", views.package_detail, name="package_detail"),
    path("package/<int:pk>/delete/", views.package_delete, name="package_delete"),
    path("package/<int:pk>/close/", views.package_close, name="package_close"),
    path("part/<int:part_pk>/fill/", views.fill_digital_form, name="fill_digital_form"),

    path("types/", views.type_manage, name="type_manage"),
    path("types/<int:pk>/", views.type_detail, name="type_detail"),
    path("types/<int:pk>/delete/", views.type_delete, name="type_delete"),
    path("part-definitions/<int:pk>/delete/", views.part_definition_delete, name="part_definition_delete"),

    path("tracker/", views.tracker_list, name="tracker_list"),
    path("tracker/<int:pk>/edit/", views.tracker_edit, name="tracker_edit"),
    path("tracker/<int:pk>/delete/", views.tracker_delete, name="tracker_delete"),
    path("tracker/compare/", views.tracker_compare, name="npt_compare"),
    path("tracker/clients/", views.client_npt_manage, name="client_npt_manage"),
    path("tracker/clients/<int:client_pk>/", views.client_npt_edit, name="client_npt_edit"),
    path("tracker/kpis/", views.kpi_manage, name="kpi_manage"),
    path("tracker/kpis/<int:pk>/edit/", views.kpi_edit, name="kpi_edit"),
    path("tracker/kpis/<int:pk>/delete/", views.kpi_delete, name="kpi_delete"),

    # Legacy (kept working, no longer the main SQB entry point)
    path("legacy/", views.legacy_job_list, name="legacy_job_list"),
    path("legacy/new/", views.job_create, name="job_create"),
    path("legacy/<int:pk>/", views.legacy_job_detail, name="legacy_job_detail"),
    path("legacy/<int:pk>/edit/", views.job_edit, name="job_edit"),
    path("legacy/<int:pk>/delete/", views.job_delete, name="job_delete"),
    path("legacy/document/<int:pk>/delete/", views.document_delete, name="document_delete"),
]