from django.urls import path

from . import views

app_name = "job_reports"

urlpatterns = [
    path("", views.job_report_list, name="list"),
    path("new/", views.job_report_create, name="create"),
    path("<int:pk>/", views.job_report_detail, name="detail"),
    path("<int:pk>/edit/", views.job_report_edit, name="edit"),
    path("<int:pk>/delete/", views.job_report_delete, name="delete"),
    path("<int:pk>/supervisors/new/", views.supervisor_add, name="supervisor_add"),
    path("supervisors/<int:pk>/edit/", views.supervisor_edit, name="supervisor_edit"),
    path("supervisors/<int:pk>/delete/", views.supervisor_delete, name="supervisor_delete"),
    path("document/<int:pk>/delete/", views.job_report_document_delete, name="document_delete"),
]
