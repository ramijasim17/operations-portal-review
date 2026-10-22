from django.urls import path

from . import views

app_name = "document_control"

urlpatterns = [
    path("", views.document_list, name="list"),
    path("<int:pk>/download/", views.document_download, name="download"),
    path("<int:pk>/edit/", views.document_edit, name="edit"),
    path("<int:pk>/delete/", views.document_delete, name="delete"),
    path("types/", views.document_type_manage, name="type_manage"),
    path("types/<int:pk>/edit/", views.document_type_edit, name="type_edit"),
    path("types/<int:pk>/delete/", views.document_type_delete, name="type_delete"),
    path("publishers/", views.publisher_manage, name="publisher_manage"),
    path("publishers/<int:pk>/edit/", views.publisher_edit, name="publisher_edit"),
    path("publishers/<int:pk>/delete/", views.publisher_delete, name="publisher_delete"),
]
