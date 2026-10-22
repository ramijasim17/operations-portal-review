from django.urls import path

from . import views

app_name = "work_approval"

urlpatterns = [
    path("", views.record_list, name="list"),
    path("new/", views.record_create, name="create"),
    path("<int:pk>/edit/", views.record_edit, name="edit"),
    path("<int:pk>/renew/", views.record_renew, name="renew"),
    path("<int:pk>/delete/", views.record_delete, name="delete"),
    path("types/", views.file_type_manage, name="file_type_manage"),
    path("types/<int:pk>/delete/", views.file_type_delete, name="file_type_delete"),
]
