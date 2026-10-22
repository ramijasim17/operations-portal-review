from django.urls import path

from . import views

app_name = "notifications"

urlpatterns = [
    path("", views.notification_list, name="list"),
    path("poll/", views.poll, name="poll"),
    path("<int:pk>/open/", views.notification_open, name="open"),
    path("read-all/", views.mark_all_read, name="read_all"),
]
