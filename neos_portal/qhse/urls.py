from django.urls import path

from . import views, views_audit, views_follow, views_visits

app_name = "qhse"

urlpatterns = [
    path("", views_follow.dashboard, name="dashboard"),
    # follow-up and investigations
    path("investigations/", views_follow.investigation_list, name="investigation_list"),
    path("investigations/new/", views_follow.investigation_create, name="investigation_create"),
    path("investigations/<int:pk>/", views_follow.investigation_detail, name="investigation_detail"),
    path("investigations/<int:pk>/edit/", views_follow.investigation_edit, name="investigation_edit"),
    path("investigations/<int:pk>/status/", views_follow.investigation_status, name="investigation_status"),
    path("investigations/<int:pk>/action/", views_follow.investigation_raise_action, name="investigation_action"),
    path("investigations/<int:pk>/delete/", views_follow.investigation_delete, name="investigation_delete"),
    # incidents
    path("incidents/", views.incident_list, name="incident_list"),
    path("incidents/new/", views.incident_create, name="incident_create"),
    path("incidents/<int:pk>/", views.incident_detail, name="incident_detail"),
    path("incidents/<int:pk>/edit/", views.incident_edit, name="incident_edit"),
    path("incidents/<int:pk>/status/", views.incident_set_status, name="incident_status"),
    path("incidents/<int:pk>/delete/", views.incident_delete, name="incident_delete"),
    path("incidents/<int:pk>/action/", views.incident_raise_action, name="incident_action"),
    # audits and inspections
    path("inspections/", views.inspection_list, name="inspection_list"),
    path("inspections/new/", views.inspection_create, name="inspection_create"),
    path("inspections/<int:pk>/", views.inspection_detail, name="inspection_detail"),
    path("inspections/<int:pk>/edit/", views.inspection_edit, name="inspection_edit"),
    path("inspections/<int:pk>/done/", views.inspection_done, name="inspection_done"),
    path("inspections/<int:pk>/conduct/", views_audit.inspection_conduct, name="inspection_conduct"),
    path("inspections/<int:pk>/delete/", views.inspection_delete, name="inspection_delete"),
    path("inspections/<int:pk>/action/", views.inspection_raise_action, name="inspection_action"),
    # audit templates (Level 3)
    path("templates/", views_audit.template_list, name="template_list"),
    path("templates/new/", views_audit.template_create, name="template_create"),
    path("templates/<int:pk>/", views_audit.template_detail, name="template_detail"),
    path("templates/<int:pk>/edit/", views_audit.template_edit, name="template_edit"),
    path("templates/<int:pk>/delete/", views_audit.template_delete, name="template_delete"),
    path("templates/<int:pk>/copy/", views_audit.template_copy, name="template_copy"),
    path("templates/<int:pk>/part/new/", views_audit.part_create, name="part_create"),
    path("parts/<int:pk>/edit/", views_audit.part_edit, name="part_edit"),
    path("parts/<int:pk>/delete/", views_audit.part_delete, name="part_delete"),
    path("parts/<int:pk>/point/new/", views_audit.point_create, name="point_create"),
    path("points/<int:pk>/edit/", views_audit.point_edit, name="point_edit"),
    path("points/<int:pk>/delete/", views_audit.point_delete, name="point_delete"),
    path("move/<str:kind>/<int:pk>/", views_audit.move, name="move"),
    # site visits
    path("visits/", views_visits.visit_list, name="visit_list"),
    path("visits/new/", views_visits.visit_create, name="visit_create"),
    path("visits/<int:pk>/", views_visits.visit_detail, name="visit_detail"),
    path("visits/<int:pk>/edit/", views_visits.visit_edit, name="visit_edit"),
    path("visits/<int:pk>/submit/", views_visits.visit_submit, name="visit_submit"),
    path("visits/<int:pk>/pdf/", views_visits.visit_pdf, name="visit_pdf"),
    path("visits/<int:pk>/delete/", views_visits.visit_delete, name="visit_delete"),
    path("visits/<int:pk>/item/new/", views_visits.item_create, name="item_create"),
    path("visit-items/<int:pk>/edit/", views_visits.item_edit, name="item_edit"),
    path("visit-items/<int:pk>/remove/", views_visits.item_remove, name="item_remove"),
    path("visit-items/<int:pk>/action/", views_visits.item_raise_action, name="item_action"),
]
