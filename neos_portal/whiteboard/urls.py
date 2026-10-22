from django.urls import path

from . import views

app_name = "whiteboard"

urlpatterns = [
    path("", views.board_list, name="list"),
    path("<int:pk>/", views.board_edit, name="edit"),
    path("<int:pk>/save/", views.board_save, name="save"),
    path("<int:pk>/delete/", views.board_delete, name="delete"),
    path("<int:pk>/export.pdf", views.board_export_pdf, name="export_pdf"),
]
