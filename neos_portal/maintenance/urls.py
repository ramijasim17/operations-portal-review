from django.urls import path

from . import views

app_name = "maintenance"

urlpatterns = [
    path("", views.record_list, name="list"),
    path("new/", views.record_create, name="create"),
    path("<int:pk>/edit/", views.record_edit, name="edit"),
    path("<int:pk>/delete/", views.record_delete, name="delete"),
    path("<int:pk>/done/", views.record_complete, name="complete"),
    path("<int:pk>/failure/", views.record_failure, name="report_failure"),
    path("<int:pk>/history/", views.record_history, name="history"),
    path("<int:pk>/action/", views.record_action, name="raise_action"),
    path("<int:pk>/history/reports.pdf", views.record_history_pdf, name="history_pdf"),
    path("levels/", views.level_manage, name="level_manage"),
    path("levels/<int:pk>/edit/", views.level_edit, name="level_edit"),
    path("levels/<int:pk>/delete/", views.level_delete, name="level_delete"),
]
