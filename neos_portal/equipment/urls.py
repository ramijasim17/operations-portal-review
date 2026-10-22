from django.urls import path
from . import views

app_name = "equipment"

urlpatterns = [
    path("", views.equipment_list, name="list"),
    path("new/", views.equipment_create, name="create"),
    path("certificates/", views.cert_list, name="cert_list"),
    path("certificates/<int:cert_id>/edit/", views.cert_edit, name="cert_edit"),
]
