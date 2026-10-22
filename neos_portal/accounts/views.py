import hmac
import logging

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render

from .forms import SignupForm, VerifyCodeForm
from .models import EmailVerification, Profile, Role

logger = logging.getLogger(__name__)


def _send_code(user, code, subject):
    """Email the verification code. Returns False (and logs why) instead of crashing the page when the mail server refuses."""
    try:
        send_mail(
            subject=subject,
            message=f"Your verification code is: {code}\n\nThis code expires in 15 minutes.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
    except Exception:
        logger.exception("Could not send the verification email to %s", user.email)
        return False
    return True


class ThrottledLoginView(LoginView):
    """Login that locks a username out for a while after repeated failures,
    so passwords can't be guessed at speed. The counter lives in Django's
    cache (per server process) and clears on a successful login."""

    template_name = "accounts/login.html"
    MAX_FAILURES = 8
    LOCKOUT_SECONDS = 15 * 60

    def _key(self, request):
        username = (request.POST.get("username") or "").strip().lower()[:150]
        return f"login-failures:{username}"

    def post(self, request, *args, **kwargs):
        if cache.get(self._key(request), 0) >= self.MAX_FAILURES:
            form = self.get_form()
            form.add_error(None, "Too many failed sign-in attempts. Please wait 15 minutes and try again.")
            return self.render_to_response(self.get_context_data(form=form), status=429)
        return super().post(request, *args, **kwargs)

    def form_invalid(self, form):
        key = self._key(self.request)
        cache.add(key, 0, self.LOCKOUT_SECONDS)
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, self.LOCKOUT_SECONDS)
        return super().form_invalid(form)

    def form_valid(self, form):
        cache.delete(self._key(self.request))
        return super().form_valid(form)


def signup(request):
    if request.method == "POST":
        form = SignupForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data["username"]
            email = form.cleaned_data["email"]

            # Clean up any abandoned, never-verified signup that used the
            # same username/email -- the form only lets this through when
            # that old account was never activated, so it's safe to
            # replace rather than leaving the person stuck forever.
            User.objects.filter(username__iexact=username, is_active=False).delete()
            User.objects.filter(email__iexact=email, is_active=False).delete()
            # ...and any abandoned signup for the same employee.
            User.objects.filter(profile__employee=form.personnel, is_active=False).delete()

            first, _, last = form.personnel.name.strip().partition(" ")       # the name comes from the personnel record

            user = User.objects.create_user(
                username=username, email=email,
                password=form.cleaned_data["password"], is_active=False,
                first_name=first[:150], last_name=last[:150],
            )
            # A signal elsewhere in this app already auto-creates a
            # Profile whenever a User is saved (per the model's own
            # docstring) -- update_or_create handles that gracefully
            # instead of colliding with it via a plain create().
            Profile.objects.update_or_create(user=user, defaults={"role": Role.VIEWER, "employee": form.personnel})
            code = EmailVerification.generate_code()
            EmailVerification.objects.create(user=user, code=code)

            if not _send_code(user, code, "Verify your ABC Portal account"):
                user.delete()                      # nothing half-created: they can simply try again
                form.add_error(None, "We could not send the verification email just now. Please try again in a few minutes, or contact your administrator.")
                return render(request, "accounts/signup.html", {"form": form, "signup_hint": getattr(settings, "SIGNUP_HINT", "")})
            request.session["pending_verification_user_id"] = user.id
            return redirect("accounts:verify")
    else:
        form = SignupForm()
    return render(request, "accounts/signup.html", {"form": form, "signup_hint": getattr(settings, "SIGNUP_HINT", "")})


def verify(request):
    user_id = request.session.get("pending_verification_user_id")
    if not user_id:
        messages.error(request, "Nothing to verify \u2014 please sign up first.")
        return redirect("accounts:signup")

    user = get_object_or_404(User, pk=user_id, is_active=False)
    form = VerifyCodeForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            verification = user.email_verification
        except EmailVerification.DoesNotExist:
            messages.error(request, "Verification session expired. Please sign up again.")
            user.delete()
            del request.session["pending_verification_user_id"]
            return redirect("accounts:signup")

        if verification.is_expired():
            messages.error(request, "That code has expired. Please sign up again.")
            user.delete()
            del request.session["pending_verification_user_id"]
            return redirect("accounts:signup")

        if not hmac.compare_digest(form.cleaned_data["code"], verification.code):
            verification.attempts += 1
            if verification.attempts >= EmailVerification.MAX_ATTEMPTS:
                messages.error(request, "Too many incorrect codes. Please sign up again.")
                user.delete()
                del request.session["pending_verification_user_id"]
                return redirect("accounts:signup")
            verification.save(update_fields=["attempts"])
            messages.error(request, "Incorrect code. Please try again.")
        else:
            user.is_active = True
            user.save(update_fields=["is_active"])
            verification.delete()
            del request.session["pending_verification_user_id"]
            login(request, user)
            messages.success(request, "Account verified \u2014 you have View access. An admin can grant Edit or Admin access from Access Control.")
            return redirect("core:dashboard")

    return render(request, "accounts/verify.html", {"form": form, "email": user.email})


def resend_code(request):
    user_id = request.session.get("pending_verification_user_id")
    if not user_id:
        return redirect("accounts:signup")
    user = get_object_or_404(User, pk=user_id, is_active=False)

    existing = EmailVerification.objects.filter(user=user).first()
    if existing and not existing.can_resend():
        messages.error(request, "Please wait a minute before asking for another code.")
        return redirect("accounts:verify")

    code = EmailVerification.generate_code()
    # Recreated (not updated) so the 15-minute expiry and the wrong-guess
    # counter both start fresh.
    EmailVerification.objects.filter(user=user).delete()
    EmailVerification.objects.create(user=user, code=code)
    if _send_code(user, code, "Your new ABC Portal verification code"):
        messages.success(request, "A new code has been sent.")
    else:
        messages.error(request, "We could not send the email just now. Please try again in a few minutes.")
    return redirect("accounts:verify")


def access_control(request):
    if not (getattr(request.user, "profile", None) and request.user.profile.is_admin):
        messages.error(request, "Only admins can access Access Control.")
        return redirect("core:dashboard")

    if request.method == "POST":
        target = get_object_or_404(Profile, pk=request.POST.get("profile_id"))
        is_self = target.user_id == request.user.id
        other_admins = Profile.objects.filter(role=Role.ADMIN, is_active_employee=True).exclude(pk=target.pk)

        if "toggle_active" in request.POST:
            if is_self:
                messages.error(request, "You can't deactivate your own account.")
            else:
                target.is_active_employee = not target.is_active_employee
                target.save(update_fields=["is_active_employee"])
                state = "can sign in again" if target.is_active_employee else "is blocked from signing in"
                messages.success(request, f"{target.user.username} {state}.")
            return redirect("accounts:access_control")

        if "toggle_notify" in request.POST:
            target.notify_all = not target.notify_all
            target.save(update_fields=["notify_all"])
            state = "now gets" if target.notify_all else "no longer gets"
            messages.success(request, f"{target.user.username} {state} an alert for every change.")
            return redirect("accounts:access_control")

        if "link_employee" in request.POST:
            from personnel.models import Personnel
            chosen = Personnel.objects.filter(pk=request.POST.get("employee") or None).first()
            holder = Profile.objects.filter(employee=chosen).exclude(pk=target.pk).select_related("user").first() if chosen else None
            if holder is not None:
                messages.error(request, f"{chosen.name} (ID {chosen.employee_id}) is already linked to @{holder.user.username}. "
                                        f"Set that account to \u201cnot linked\u201d first, then link this one.")
            else:
                target.employee = chosen
                target.save(update_fields=["employee"])
                messages.success(request, f"{target.user.username} is now linked to {chosen.name}." if chosen else f"{target.user.username} is no longer linked to an employee.")
            return redirect("accounts:access_control")

        new_role = request.POST.get("role")
        if new_role in Role.values:
            if target.role == Role.ADMIN and new_role != Role.ADMIN and not other_admins.exists():
                messages.error(request, "There must be at least one active admin — promote someone else first.")
            elif is_self and new_role != Role.ADMIN:
                messages.error(request, "You can't remove your own admin access. Ask another admin.")
            else:
                target.role = new_role
                target.save(update_fields=["role"])
                messages.success(request, f"{target.user.username}'s role is now {target.get_role_display()}.")
        return redirect("accounts:access_control")

    profiles = Profile.objects.select_related("user", "employee").order_by("user__username")
    from personnel.models import Personnel
    return render(request, "accounts/access_control.html", {
        "profiles": profiles, "roles": Role.choices, "online_count": Profile.online_now().count(),
        "employees": Personnel.objects.filter(status="Active").order_by("name"),
    })


def backup(request):
    """Admin only: download a backup (database + files + code) as a zip, in full or only what changed."""
    from . import backup as backup_tools
    from .models import BackupLog

    if not (getattr(request.user, "profile", None) and request.user.profile.is_admin):
        raise PermissionDenied("Only admins can make backups.")
    last = BackupLog.objects.exclude(kind=BackupLog.SYNC).select_related("by").first()
    return render(request, "accounts/backup.html", {
        "info": backup_tools.describe(), "storage": "bucket" if settings.USE_S3 else "this server's disk",
        "last": last, "preview": backup_tools.changes_preview(last) if last else None,
        "history": BackupLog.objects.exclude(kind=BackupLog.SYNC).select_related("by", "based_on")[:12],
        "last_sync": BackupLog.objects.filter(kind=BackupLog.SYNC).select_related("by").first(),
    })


def backup_preview(request):
    """JSON for the Backup page: what a changes-only backup would hold, measured from the backup the admin picked."""
    from django.http import JsonResponse

    from . import backup as backup_tools
    from .models import BackupLog

    if not (getattr(request.user, "profile", None) and request.user.profile.is_admin):
        raise PermissionDenied("Only admins can make backups.")
    base = BackupLog.objects.exclude(kind=BackupLog.SYNC).filter(pk=request.GET.get("base") or None).first()
    if base is None:
        return JsonResponse({"error": "Unknown backup."}, status=404)
    return JsonResponse(backup_tools.changes_preview(base))


def backup_download(request):
    from django.http import StreamingHttpResponse

    from . import backup as backup_tools
    from .models import BackupLog

    if not (getattr(request.user, "profile", None) and request.user.profile.is_admin):
        raise PermissionDenied("Only admins can make backups.")
    wanted = request.GET.get("part", "all")
    kind, previous = backup_tools.FULL, None
    if wanted == "all":
        parts = backup_tools.PARTS
        if request.GET.get("mode") == "changes":
            wanted_base = request.GET.get("base")
            zips = BackupLog.objects.exclude(kind=BackupLog.SYNC)
            previous = zips.filter(pk=wanted_base).first() if wanted_base else zips.first()
            if previous is None:
                messages.error(request, "There is no such earlier backup to measure from. Pick one from the list, or make a full backup first.")
                return redirect("accounts:backup")
            kind = backup_tools.CHANGES
    else:
        parts = tuple(p for p in backup_tools.PARTS if p == wanted)
        if not parts:
            raise PermissionDenied("Unknown backup part.")
    logger.warning("Backup (%s, %s) downloaded by %s", kind, ", ".join(parts), request.user.username)
    from django.utils import timezone

    now = timezone.localtime()
    response = StreamingHttpResponse(backup_tools.stream(parts, user=request.user, kind=kind, previous=previous, now=now),
                                     content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{backup_tools.filename(parts, kind, now, previous)}"'
    response["Cache-Control"] = "no-store"
    return response


def _backup_admin(request):
    if not (getattr(request.user, "profile", None) and request.user.profile.is_admin):
        raise PermissionDenied("Only admins can make backups.")


def backup_sync_manifest(request):
    """What the browser compares with the backup folder: every file with its signature, and the code fingerprint."""
    from django.http import JsonResponse

    from . import backup as backup_tools

    _backup_admin(request)
    response = JsonResponse(backup_tools.sync_manifest())
    response["Cache-Control"] = "no-store"
    return response


def backup_sync_file(request):
    """One uploaded file, sent as it is (with its length, so the browser can tell if it arrived whole)."""
    from django.http import Http404, StreamingHttpResponse

    from . import backup as backup_tools

    _backup_admin(request)
    found = backup_tools.open_file(request.GET.get("name", ""))
    if found is None:
        raise Http404("No such file.")
    size, chunks = found
    response = StreamingHttpResponse(chunks, content_type="application/octet-stream")
    response["Content-Length"] = str(size)
    response["Cache-Control"] = "no-store"
    return response


def backup_sync_database(request):
    from django.http import HttpResponse

    from . import backup as backup_tools

    _backup_admin(request)
    response = HttpResponse(backup_tools.database_bytes(), content_type="application/json")
    response["Cache-Control"] = "no-store"
    return response


def backup_sync_code(request):
    from django.http import StreamingHttpResponse

    from . import backup as backup_tools

    _backup_admin(request)
    response = StreamingHttpResponse(backup_tools.code_zip(), content_type="application/zip")
    response["Cache-Control"] = "no-store"
    return response


def backup_sync_done(request):
    """The browser reports that a folder update finished; recorded so the page can show it."""
    import json

    from django.http import JsonResponse
    from django.utils import timezone

    from .models import BackupLog

    _backup_admin(request)
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    try:
        data = json.loads(request.body or b"{}")
        numbers = {k: max(0, int(data.get(k, 0))) for k in ("total", "downloaded", "downloaded_bytes", "removed")}
    except (ValueError, TypeError):
        return JsonResponse({"error": "Bad report"}, status=400)
    now = timezone.localtime()
    BackupLog.objects.create(
        kind=BackupLog.SYNC, started_at=timezone.now(), by=request.user, files_in_storage=numbers["total"],
        files_included=numbers["downloaded"], files_bytes=numbers["downloaded_bytes"],
        code_included=bool(data.get("code_updated")), code_fingerprint=str(data.get("code_fingerprint", ""))[:64],
        stamp=f"{now:%Y%m%d-%H%M}")
    return JsonResponse({"ok": True})


def cleanup(request):
    """Admin only: files that no record uses (old versions, leftovers). Moved to a trash first, never deleted at once."""
    from . import storage_cleanup
    from .models import TrashBatch

    _backup_admin(request)
    rows = storage_cleanup.unused_files()
    groups = storage_cleanup.grouped(rows) if rows else []
    return render(request, "accounts/cleanup.html", {
        "can_tell": rows is not None, "groups": groups,
        "total_files": sum(len(g["files"]) for g in groups), "total_bytes": sum(g["bytes"] for g in groups),
        "trash": [b for b in TrashBatch.objects.select_related("by")[:15]],
        "hold_days": TrashBatch.HOLD.days, "grace_hours": int(storage_cleanup.GRACE.total_seconds() // 3600),
    })


def cleanup_trash(request):
    from . import storage_cleanup

    _backup_admin(request)
    if request.method != "POST":
        raise PermissionDenied("POST only.")
    names = request.POST.getlist("name")
    if not names:
        messages.error(request, "Tick at least one file first.")
        return redirect("accounts:cleanup")
    try:
        batch, skipped = storage_cleanup.move_to_trash(names, user=request.user)
    except storage_cleanup.CleanupError as error:
        messages.error(request, str(error))
        return redirect("accounts:cleanup")
    shown = {why: n for why, n in skipped.items() if n}
    extra = f" Skipped: {', '.join(f'{n} {why}' for why, n in shown.items())}." if shown else ""
    if batch is None:
        messages.error(request, "Nothing was moved." + extra)
    else:
        from django.template.defaultfilters import filesizeformat
        messages.success(request, f"{batch.count} file{'s' if batch.count != 1 else ''} ({filesizeformat(batch.bytes)}) moved to the trash. "
                                  f"You can restore them for the next {batch.HOLD.days} days.{extra}")
    return redirect("accounts:cleanup")


def cleanup_restore(request, pk):
    from django.shortcuts import get_object_or_404

    from . import storage_cleanup
    from .models import TrashBatch

    _backup_admin(request)
    if request.method != "POST":
        raise PermissionDenied("POST only.")
    batch = get_object_or_404(TrashBatch, pk=pk)
    try:
        restored, kept = storage_cleanup.restore(batch)
    except storage_cleanup.CleanupError as error:
        messages.error(request, str(error))
        return redirect("accounts:cleanup")
    if kept:
        messages.warning(request, f"{restored} restored; {len(kept)} stay in the trash because a file with that name exists again.")
    else:
        messages.success(request, f"{restored} file{'s' if restored != 1 else ''} restored.")
    return redirect("accounts:cleanup")


def cleanup_purge(request, pk):
    from django.shortcuts import get_object_or_404

    from . import storage_cleanup
    from .models import TrashBatch

    _backup_admin(request)
    if request.method != "POST":
        raise PermissionDenied("POST only.")
    batch = get_object_or_404(TrashBatch, pk=pk)
    try:
        count = storage_cleanup.purge(batch)
        messages.success(request, f"{count} file{'s' if count != 1 else ''} deleted for good.")
    except storage_cleanup.CleanupError as error:
        messages.error(request, str(error))
    return redirect("accounts:cleanup")
