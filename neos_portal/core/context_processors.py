"""Makes the sidebar navigation list available to every template, built
from the user's role — this is where 'different people see different tabs'
actually happens, in one place rather than scattered per-page checks."""

NAV_ITEMS = [
  {"label": "Dashboard",           "url_name": "core:dashboard",         "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Departments",         "url_name": "departments:list",       "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Action Tracker",      "url_name": "action_tracker:list",    "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Operations", "roles": ("admin", "approver", "editor", "viewer"), "children": [
      {"label": "Job Manager", "url_name": "job_manager:list", "hint": "Jobs and manifests"},
      {"label": "Job Reports", "url_name": "job_reports:list", "hint": "Reports and actions"},
      {"label": "SQB",         "url_name": "sqb:job_list",     "hint": "Quality and NPT"},
      {"label": "SGS Reports", "url_name": "sgs_report:list",  "hint": "Wellsite reports"},
  ]},
  {"label": "Maintenance",         "url_name": "maintenance:list",       "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Work Approval & SOC", "url_name": "work_approval:list",     "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Document Controller",  "url_name": "document_control:list",  "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Personnel",           "url_name": "personnel:list",         "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "QHSE",                "url_name": "qhse:dashboard",         "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Wire Management",     "url_name": "wire_management:list",   "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Clients & Fields",   "url_name": "wire_management:lists",  "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Approvals",           "url_name": "approvals:inbox",        "roles": ("admin", "approver", "editor", "viewer")},
  {"label": "Access Control",      "url_name": "accounts:access_control", "roles": ("admin",)},
  {"label": "Whiteboard",          "url_name": "whiteboard:list",       "roles": ("admin", "approver", "editor", "viewer"), "gate": "whiteboard"},
  {"label": "Backup",              "url_name": "accounts:backup",         "roles": ("admin",)},
]


# Pages that belong to a different sidebar item than their app would suggest.
NAV_OVERRIDES = {
    "wire_management:list_edit": "wire_management:lists",
    "wire_management:list_delete": "wire_management:lists",
    "accounts:cleanup": "accounts:backup",
    "accounts:cleanup_trash": "accounts:backup",
    "accounts:cleanup_restore": "accounts:backup",
    "accounts:cleanup_purge": "accounts:backup",
}


def _links():
    """Every real menu link in order, the ones inside a group (Operations) included."""
    out = []
    for item in NAV_ITEMS:
        out.extend(item["children"] if "children" in item else [item])
    return out


def active_nav_item(view_name):
    """The sidebar item to highlight for `view_name`: the item for that exact
    page if there is one, otherwise the first item of the same module, so a
    detail / edit / sub-page keeps its module highlighted."""
    if not view_name:
        return None
    view_name = NAV_OVERRIDES.get(view_name, view_name)
    links = _links()
    for item in links:
        if item["url_name"] == view_name:
            return item["url_name"]
    module = view_name.split(":")[0]
    for item in links:
        if item["url_name"].split(":")[0] == module:
            return item["url_name"]
    return None


def nav_items(request):
    if not request.user.is_authenticated:
        return {}
    profile = getattr(request.user, "profile", None)
    role = profile.role if profile else "viewer"

    current_view_name = None
    if getattr(request, "resolver_match", None):
        current_view_name = request.resolver_match.view_name  # e.g. "equipment:cert_list"
    active = active_nav_item(current_view_name)

    from whiteboard.access import allowed as whiteboard_allowed

    visible = []
    for item in NAV_ITEMS:
        if item.get("gate") == "whiteboard" and not whiteboard_allowed(request.user):
            continue
        if role in item["roles"]:
            if "children" in item:
                kids = [{**c, "is_active": c["url_name"] == active} for c in item["children"]]
                visible.append({**item, "children": kids, "is_active": any(k["is_active"] for k in kids)})
            else:
                visible.append({**item, "is_active": item["url_name"] == active})

    return {"nav_items": visible, "user_role": role}


def asset_version(request):
    """A number that changes whenever the portal's own CSS / JS change, added to their links so a browser (or an iPad)
    never keeps showing an old copy after an update."""
    import os

    from django.conf import settings
    from django.contrib.staticfiles import finders

    stamp = 0
    for name in ("core/css/portal.css", "core/js/portal.js", "whiteboard/whiteboard.js", "whiteboard/whiteboard.css"):
        path = finders.find(name)
        if path:
            stamp = max(stamp, int(os.path.getmtime(path)))
    return {"asset_version": stamp}
