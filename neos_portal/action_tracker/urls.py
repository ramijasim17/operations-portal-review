from django.urls import path
from . import views

app_name = "action_tracker"

urlpatterns = [
    path("", views.action_list, name="list"),
    path("insights/", views.insights, name="insights"),
    path("new/", views.action_create, name="create"),
    path("<int:pk>/", views.action_detail, name="detail"),
    path("<int:pk>/edit/", views.action_edit, name="edit"),
    path("<int:pk>/status/", views.action_set_status, name="set_status"),
    path("<int:pk>/delete/", views.action_delete, name="delete"),
    path("clear-completed/", views.clear_completed, name="clear_completed"),
]
