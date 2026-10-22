"""
Permission decorators enforcing the three Access Control levels
(View / Edit / Admin) on individual views. Each one:
  - redirects with an error message if the user has no permission
  - otherwise runs the underlying view unchanged

Use whichever matches what the view actually does:
  @can_edit_required     -- creating/editing records, uploading files
  @can_download_required -- opening/downloading an uploaded file
  @can_delete_required   -- deleting a record
  @admin_required        -- admin-only pages

Works bare (@can_edit_required) or with a custom landing page for the
denial message (@can_edit_required(redirect_to="document_control:list")).
"""
from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect


def user_can(request, permission):
    """For views that mix reading and writing (e.g. a list page that also
    accepts a POST to create) where a decorator on the whole view would be
    too blunt. `permission` is a Profile property name: can_edit,
    can_download, can_delete or is_admin."""
    profile = getattr(request.user, "profile", None)
    return bool(profile and getattr(profile, permission, False))


def _make_decorator(permission, denial_message):
    def decorator(view_func=None, *, redirect_to="core:dashboard"):
        def build(func):
            @wraps(func)
            def wrapped(request, *args, **kwargs):
                if not user_can(request, permission):
                    messages.error(request, denial_message)
                    return redirect(redirect_to)
                return func(request, *args, **kwargs)
            return wrapped
        return build(view_func) if view_func is not None else build
    return decorator


can_edit_required = _make_decorator("can_edit", "You don't have permission to create or edit records.")
can_download_required = _make_decorator("can_download", "You don't have permission to download files.")
can_delete_required = _make_decorator("can_delete", "You don't have permission to delete records.")
admin_required = _make_decorator("is_admin", "Only admins can access this page.")
