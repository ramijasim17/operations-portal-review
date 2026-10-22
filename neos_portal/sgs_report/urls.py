from django.urls import path

from . import views

app_name = "sgs_report"

urlpatterns = [
    path("", views.report_list, name="list"),
    path("new/", views.report_create, name="create"),
    path("<int:pk>/", views.report_detail, name="detail"),
    path("<int:pk>/edit/", views.report_edit, name="edit"),
    path("<int:pk>/delete/", views.report_delete, name="delete"),
    path("<int:pk>/generate/", views.generate_docx, name="generate"),
    path("<int:pk>/generate-pdf/", views.generate_pdf, name="generate_pdf"),
    path("<int:pk>/export-excel/", views.export_excel, name="export_excel"),
    path("<int:pk>/client-file/<str:side>/", views.client_file, name="client_file"),
    path("<int:pk>/station-import/", views.station_import_excel, name="station_import_excel"),

    path("shift/<int:shift_pk>/entry/add/", views.entry_add, name="entry_add"),
    path("shift/<int:shift_pk>/entry/import/", views.entry_import_excel, name="entry_import_excel"),
    path("entry/<int:pk>/delete/", views.entry_delete, name="entry_delete"),
    path("entry/<int:pk>/edit/", views.entry_edit, name="entry_edit"),
    path("shift/<int:pk>/delete/", views.shift_delete, name="shift_delete"),
    path("station/<int:pk>/delete/", views.station_delete, name="station_delete"),
    path("station/<int:pk>/edit/", views.station_edit, name="station_edit"),
    path("<int:pk>/station-from-chart/", views.station_update_from_chart, name="station_update_from_chart"),

    path("<int:pk>/status/", views.report_status_update, name="status_update"),

    path("<int:pk>/image/<str:section>/", views.image_upload, name="image_upload"),
    path("image/<int:pk>/delete/", views.image_delete, name="image_delete"),

    path("gauges/", views.gauge_list, name="gauge_list"),
    path("gauges/<int:pk>/edit/", views.gauge_edit, name="gauge_edit"),
    path("gauges/<int:pk>/delete/", views.gauge_delete, name="gauge_delete"),
    path("batteries/<int:pk>/delete/", views.battery_delete, name="battery_delete"),
    path("batteries/<int:pk>/", views.battery_detail, name="battery_detail"),
    path("batteries/<int:pk>/edit/", views.battery_edit, name="battery_edit"),
    path("batteries/usage/<int:pk>/edit/", views.battery_usage_edit, name="battery_usage_edit"),
    path("batteries/log/", views.battery_log_export, name="battery_log_export"),
]
