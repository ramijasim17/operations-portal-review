"""
Central permission policy: every route in the portal is classified here
by what it DOES, and the middleware enforces it before the view runs.

    READ      just looking (any logged-in role)
    EDIT      creating/changing records or uploading files (Editor, Admin)
    DOWNLOAD  handing back a file/export/report (Level 2 and above)
    DELETE    removing anything (Level 3 and above)
    APPROVE   approving or rejecting work (Level 3 and above)

Two rules apply on top of the table, so nothing can slip through:
  * ANY write (POST/PUT/PATCH/DELETE) needs Edit access, even on a page
    classified READ (e.g. a list page that also accepts a create form).
  * A route that is NOT in the table is treated as READ for GET and as
    EDIT for writes -- and accounts/tests.py fails if a route is missing,
    so a new route can't be forgotten silently.

Report photos and client logos shown inline on a page stay visible to
everyone, otherwise Editors couldn't see the images they just uploaded.
Everything else under /media/ (documents, certificates, logs) needs
Download access.
"""
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import Resolver404, resolve

READ, EDIT, DOWNLOAD, DELETE, APPROVE = "read", "edit", "download", "delete", "approve"

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
# Photos that are displayed INSIDE pages (report photos, client logos) stay
# visible to every role -- otherwise Editors couldn't see what they just
# uploaded. Certificates, logs, documents and everything else need Download.
INLINE_MEDIA_DIRS = ("sgs_reports/images/", "sgs_reports/client_logos/")

# Namespaces that manage their own rules (login/logout/signup, Django admin).
# notifications: everyone, whatever their role, can read and clear their own.
UNMANAGED_NAMESPACES = ("accounts", "admin", "core", "notifications", "whiteboard")        # approvals are classified route by route below

POLICY = {
    # ── Approvals ────────────────────────────────────────────────────────
    "approvals:inbox": READ,
    "approvals:decide": APPROVE,
    "approvals:bulk": APPROVE,
    "approvals:resubmit": EDIT,
    "approvals:submit_sgs": EDIT,
    # ── Departments ──────────────────────────────────────────────────────
    "departments:list": READ,
    "departments:bulk_cert_upload": EDIT,
    "departments:bulk_cert_preview": EDIT,
    "departments:bulk_cert_confirm": EDIT,
    "departments:detail": READ,
    "departments:edit": EDIT,
    "departments:delete": DELETE,
    "departments:classification_detail": READ,
    "departments:classification_fields": EDIT,
    "departments:classification_edit": EDIT,
    "departments:classification_delete": DELETE,
    "departments:classification_export": DOWNLOAD,
    "departments:classification_export_selected": DOWNLOAD,
    # Blank import sheet (headers + dropdowns, no asset data): Editors need
    # it to use Import, so it is EDIT rather than DOWNLOAD.
    "departments:classification_template": EDIT,
    "departments:classification_import": EDIT,
    "departments:classification_certtypes": EDIT,
    "departments:field_delete": DELETE,
    "departments:asset_create": EDIT,
    "departments:asset_detail": READ,
    "departments:asset_edit": EDIT,
    "departments:asset_delete": DELETE,
    "departments:asset_request_removal": EDIT,
    "departments:asset_cert_create": EDIT,
    "departments:asset_cert_edit": EDIT,
    "departments:cert_type_manage": EDIT,
    "departments:cert_type_delete": DELETE,
    # ── Action Tracker ───────────────────────────────────────────────────
    "action_tracker:list": READ,
    "action_tracker:insights": READ,
    "action_tracker:create": EDIT,
    "action_tracker:detail": READ,
    "action_tracker:edit": EDIT,
    "action_tracker:set_status": EDIT,
    "action_tracker:delete": DELETE,
    "action_tracker:clear_completed": DELETE,
    # ── Job Manager ──────────────────────────────────────────────────────
    "job_manager:list": READ,
    "job_manager:create": EDIT,
    "job_manager:detail": READ,
    "job_manager:edit": EDIT,
    "job_manager:delete": DELETE,
    "job_manager:export_manifest": DOWNLOAD,
    "job_manager:export_manifest_pdf": DOWNLOAD,
    "job_manager:import_serials": EDIT,
    "job_manager:export_certs": DOWNLOAD,
    "job_manager:export_certs_zip": DOWNLOAD,
    # ── Job Reports ──────────────────────────────────────────────────────
    "job_reports:list": READ,
    "job_reports:create": EDIT,
    "job_reports:detail": READ,
    "job_reports:edit": EDIT,
    "job_reports:delete": DELETE,
    "job_reports:supervisor_add": EDIT,
    "job_reports:supervisor_edit": EDIT,
    "job_reports:supervisor_delete": DELETE,
    "job_reports:document_delete": DELETE,
    # ── SQB ──────────────────────────────────────────────────────────────
    "sqb:job_list": READ,
    "sqb:package_create": EDIT,
    "sqb:package_detail": READ,
    "sqb:package_delete": DELETE,
    "sqb:package_close": EDIT,
    "sqb:fill_digital_form": EDIT,
    "sqb:type_manage": EDIT,
    "sqb:type_detail": EDIT,
    "sqb:type_delete": DELETE,
    "sqb:part_definition_delete": DELETE,
    "sqb:tracker_list": READ,
    "sqb:tracker_edit": EDIT,
    "sqb:tracker_delete": DELETE,
    "sqb:npt_compare": READ,
    "sqb:client_npt_manage": EDIT,
    "sqb:client_npt_edit": EDIT,
    "sqb:kpi_manage": EDIT,
    "sqb:kpi_edit": EDIT,
    "sqb:kpi_delete": DELETE,
    "sqb:legacy_job_list": READ,
    "sqb:job_create": EDIT,
    "sqb:legacy_job_detail": READ,
    "sqb:job_edit": EDIT,
    "sqb:job_delete": DELETE,
    "sqb:document_delete": DELETE,
    # ── SGS Reports ──────────────────────────────────────────────────────
    "sgs_report:list": READ,
    "sgs_report:create": EDIT,
    "sgs_report:detail": READ,
    "sgs_report:edit": EDIT,
    "sgs_report:delete": DELETE,
    "sgs_report:generate": DOWNLOAD,
    "sgs_report:generate_pdf": DOWNLOAD,
    "sgs_report:export_excel": DOWNLOAD,
    "sgs_report:client_file": DOWNLOAD,
    "sgs_report:station_import_excel": EDIT,
    "sgs_report:entry_add": EDIT,
    "sgs_report:entry_import_excel": EDIT,
    "sgs_report:entry_delete": DELETE,
    "sgs_report:entry_edit": EDIT,
    "sgs_report:shift_delete": DELETE,
    "sgs_report:station_delete": DELETE,
    "sgs_report:station_edit": EDIT,
    "sgs_report:station_update_from_chart": EDIT,
    "sgs_report:status_update": EDIT,
    "sgs_report:image_upload": EDIT,
    "sgs_report:image_delete": DELETE,
    "sgs_report:gauge_list": READ,
    "sgs_report:gauge_edit": EDIT,
    "sgs_report:gauge_delete": DELETE,
    "sgs_report:battery_delete": DELETE,
    "sgs_report:battery_detail": READ,
    "sgs_report:battery_edit": EDIT,
    "sgs_report:battery_usage_edit": EDIT,
    "sgs_report:battery_log_export": DOWNLOAD,
    # ── Maintenance ──────────────────────────────────────────────────────
    # ── QHSE ─────────────────────────────────────────────────────────────
    "qhse:dashboard": READ,
    "qhse:investigation_list": READ,
    "qhse:investigation_detail": READ,
    "qhse:investigation_create": EDIT,
    "qhse:investigation_edit": EDIT,
    "qhse:investigation_status": EDIT,
    "qhse:investigation_action": EDIT,
    "qhse:investigation_delete": DELETE,
    "qhse:incident_list": READ,
    "qhse:incident_detail": READ,
    "qhse:incident_create": EDIT,
    "qhse:incident_edit": EDIT,
    "qhse:incident_status": EDIT,
    "qhse:incident_action": EDIT,
    "qhse:incident_delete": DELETE,
    "qhse:inspection_list": READ,
    "qhse:inspection_detail": READ,
    "qhse:inspection_create": EDIT,
    "qhse:inspection_edit": EDIT,
    "qhse:inspection_done": EDIT,
    "qhse:inspection_conduct": EDIT,
    "qhse:inspection_action": EDIT,
    "qhse:inspection_delete": DELETE,
    # audit templates: only Level 3 builds, changes or removes them; everyone can look at them
    "qhse:template_list": READ,
    "qhse:template_detail": READ,
    "qhse:template_create": APPROVE,
    "qhse:template_edit": APPROVE,
    "qhse:template_delete": DELETE,
    "qhse:template_copy": APPROVE,
    "qhse:part_create": APPROVE,
    "qhse:part_edit": APPROVE,
    "qhse:part_delete": DELETE,
    "qhse:point_create": APPROVE,
    "qhse:point_edit": APPROVE,
    "qhse:point_delete": DELETE,
    "qhse:move": APPROVE,
    "qhse:visit_list": READ,
    "qhse:visit_detail": READ,
    "qhse:visit_create": EDIT,
    "qhse:visit_edit": EDIT,
    "qhse:visit_submit": EDIT,
    "qhse:visit_pdf": DOWNLOAD,
    "qhse:visit_delete": DELETE,
    "qhse:item_create": EDIT,
    "qhse:item_edit": EDIT,
    "qhse:item_remove": EDIT,
    "qhse:item_action": EDIT,
    "maintenance:list": READ,
    "maintenance:create": EDIT,
    "maintenance:edit": EDIT,
    "maintenance:delete": DELETE,
    "maintenance:complete": EDIT,
    "maintenance:report_failure": EDIT,
    "maintenance:history": READ,
    "maintenance:raise_action": EDIT,
    "maintenance:history_pdf": DOWNLOAD,
    "maintenance:level_manage": EDIT,
    "maintenance:level_edit": EDIT,
    "maintenance:level_delete": DELETE,
    # ── Work Approval & SOC ──────────────────────────────────────────────
    "work_approval:list": READ,
    "work_approval:create": EDIT,
    "work_approval:edit": EDIT,
    "work_approval:renew": EDIT,
    "work_approval:delete": DELETE,
    "work_approval:file_type_manage": EDIT,
    "work_approval:file_type_delete": DELETE,
    # ── Personnel ────────────────────────────────────────────────────────
    "personnel:list": READ,
    "personnel:detail": READ,
    "personnel:create": EDIT,
    "personnel:edit": EDIT,
    "personnel:delete": DELETE,
    "personnel:request_removal": EDIT,
    "personnel:cert_matrix": READ,
    "personnel:cert_create": EDIT,
    "personnel:cert_edit": EDIT,
    "personnel:course_manage": EDIT,
    "personnel:course_delete": DELETE,
    "personnel:required_courses_save": EDIT,
    "personnel:course_certs_zip": DOWNLOAD,
    "personnel:csv_import": EDIT,
    "personnel:bulk_cert_upload": EDIT,
    "personnel:bulk_cert_preview": EDIT,
    "personnel:bulk_cert_confirm": EDIT,
    # ── Equipment (legacy) ───────────────────────────────────────────────
    "equipment:list": READ,
    "equipment:create": EDIT,
    "equipment:cert_list": READ,
    "equipment:cert_edit": EDIT,
    # ── Wire Management ──────────────────────────────────────────────────
    "wire_management:lists": READ,
    "wire_management:list_edit": EDIT,
    "wire_management:list_delete": DELETE,
    "wire_management:list": READ,
    "wire_management:detail": READ,
    "wire_management:edit": EDIT,
    "wire_management:delete": DELETE,
    "wire_management:drum_evidence_pdf": DOWNLOAD,
    "wire_management:drum_import_excel": EDIT,
    "wire_management:entry_edit": EDIT,
    "wire_management:entry_delete": DELETE,
    "wire_management:entry_evidence_pdf": DOWNLOAD,
    "wire_management:evidence_delete": DELETE,
    "wire_management:client_manage": EDIT,
    "wire_management:client_delete": DELETE,
    "wire_management:field_well_manage": EDIT,
    "wire_management:field_well_delete": DELETE,
    # Blank import sheet (headers only): Editors need it to use Import.
    "wire_management:import_template": EDIT,
    # ── Document Controller ──────────────────────────────────────────────
    "document_control:list": READ,
    "document_control:download": DOWNLOAD,
    "document_control:edit": EDIT,
    "document_control:delete": DELETE,
    "document_control:type_manage": EDIT,
    "document_control:type_edit": EDIT,
    "document_control:type_delete": DELETE,
    "document_control:publisher_manage": EDIT,
    "document_control:publisher_edit": EDIT,
    "document_control:publisher_delete": DELETE,
}

PERMISSION_FOR = {EDIT: "can_edit", DOWNLOAD: "can_download", DELETE: "can_delete", APPROVE: "can_approve"}
DENIAL_TEXT = {
    "can_edit": "Your account is Level 1 (view only), so it can't create, edit or upload records.",
    "can_download": "Your account is Level 1 (view only), so it can't download or open files, exports and reports.",
    "can_delete": "Only Level 3 (approvers) and admins can delete records. You can ask for a removal where the page offers it.",
    "can_approve": "Only Level 3 (approvers) and admins can do this (approve or reject work, build audit templates).",
}


def required_permission(view_name, method):
    """The Profile property the user must have, or None if any logged-in
    user may proceed."""
    needed = PERMISSION_FOR.get(POLICY.get(view_name))
    if needed:
        return needed
    if method not in SAFE_METHODS:
        return "can_edit"
    return None


def _denied(request, permission):
    return render(request, "accounts/denied.html",
                  {"reason": DENIAL_TEXT[permission]}, status=403)


def enforce(request):
    """Called by LoginRequiredMiddleware for every logged-in request that
    isn't on an exempt path. Returns a 403 response, or None to continue."""
    profile = request.user.profile
    path = request.path_info

    media_url = getattr(settings, "MEDIA_URL", "") or ""
    # With the S3/R2 bucket MEDIA_URL is empty, and Django turns an empty value into "/": that must not make
    # every page look like a media file.
    if media_url.startswith("/") and media_url != "/" and path.startswith(media_url):
        # Local-disk media (development). In production the private bucket
        # only hands out short-lived signed links, and those are only
        # rendered for users who pass the checks below.
        relative = path[len(media_url):]
        if relative.startswith(INLINE_MEDIA_DIRS) or profile.can_download:
            return None
        return _denied(request, "can_download")

    try:
        match = resolve(path)
    except Resolver404:
        return None
    if match.namespaces and match.namespaces[0] in UNMANAGED_NAMESPACES:
        return None

    needed = required_permission(match.view_name, request.method)
    if needed and not getattr(profile, needed, False):
        return _denied(request, needed)
    return None
