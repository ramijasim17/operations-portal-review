from django.urls import path

from . import views

app_name = "approvals"

urlpatterns = [
    path("", views.inbox, name="inbox"),
    path("<int:pk>/decide/", views.decide, name="decide"),
    path("bulk/", views.bulk, name="bulk"),
    path("<int:pk>/resubmit/", views.resubmit, name="resubmit"),
    path("sgs/<int:pk>/<str:kind>/submit/", views.submit_sgs, name="submit_sgs"),
]
