from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.dept_scope import filter_qs, get_scope, initial_department, visible_or_404

from .forms import ActionForm
from .models import Action, ActionStep

TABS = ("open", "overdue", "completed", "all")


def action_list(request):
    tab = request.GET.get("tab", "open")
    if tab not in TABS:
        tab = "open"
    q = request.GET.get("q", "").strip()
    priority = request.GET.get("priority", "")
    assignee = request.GET.get("assignee", "")

    scope = get_scope(request)
    everything = list(filter_qs(Action.objects.prefetch_related("steps").select_related("department"), scope))
    counts = {
        "open": sum(a.is_open for a in everything),
        "overdue": sum(a.is_overdue for a in everything),
        "completed": sum(not a.is_open for a in everything),
        "all": len(everything),
        "high_open": sum(a.is_open and a.priority == "High" for a in everything),
    }
    assignees = sorted({a.assignee for a in everything if a.assignee})

    rows = everything
    if tab == "open":
        rows = [a for a in rows if a.is_open]
    elif tab == "overdue":
        rows = [a for a in rows if a.is_overdue]
    elif tab == "completed":
        rows = [a for a in rows if not a.is_open]
    if q:
        needle = q.lower()
        rows = [a for a in rows if needle in a.action_item.lower() or needle in a.assignee.lower()]
    if priority:
        rows = [a for a in rows if a.priority == priority]
    if assignee:
        rows = [a for a in rows if a.assignee == assignee]
    rows.sort(key=Action.urgency_key)
    for a in rows:
        steps = list(a.steps.all())
        a.step_total, a.step_done = len(steps), sum(s.is_completed for s in steps)
        a.step_percent = round(100 * a.step_done / a.step_total) if a.step_total else 0

    return render(request, "action_tracker/action_list.html", {
        "actions": rows, "counts": counts, "tab": tab, "q": q, "priority": priority,
        "assignee": assignee, "assignees": assignees, "scope": scope,
    })


def insights(request):
    """Who raised how many actions, of what type, per month, and how fast they were closed."""
    from sqb import npt
    from wire_management.models import Client

    from . import insights as ins
    from .models import INTERNAL, TYPE_CHOICES

    period = npt.parse_period(request.GET)
    client = request.GET.get("client", "")
    action_type = request.GET.get("type", "")
    scope = get_scope(request)
    everything = list(filter_qs(Action.objects.select_related("job_report__manifest"), scope))
    names = sorted(set(Client.objects.values_list("name", flat=True)) | {a.client for a in everything if a.client} | {INTERNAL})
    colours = npt.colour_map(names + ["Not recorded"])

    undated = sum(1 for a in everything if not a.raised_date)
    dated = ins.in_period(everything, period["start"], period["end"])
    shown = ins.apply_filters(dated, client, action_type)

    return render(request, "action_tracker/insights.html", {
        "scope": scope, "period": period, "period_choices": npt.PERIOD_CHOICES, "client": client, "type": action_type,
        "client_names": names, "type_choices": TYPE_CHOICES, "colours": colours, "undated": undated,
        "summary": ins.summary(shown),
        "by_client": ins.group(ins.apply_filters(dated, "", action_type), lambda a: a.client or "", ins.client_label,
                               colour=lambda k: colours.get(k or "Not recorded", "#6B6880")),
        "by_type": ins.group(ins.apply_filters(dated, client, ""), lambda a: a.action_type, ins.type_label),
        "monthly": ins.per_month(shown, period["start"], period["end"], colours, split_by_client=not client),
        "slowest": ins.slowest_open(ins.apply_filters(everything, client, action_type)),
        "recent_closed": sorted([a for a in shown if a.days_to_close is not None], key=lambda a: -a.days_to_close)[:8],
    })


def action_create(request):
    scope = get_scope(request)
    if scope.mode == "blocked":
        messages.error(request, "Your account is not linked to a department yet, so you can't add actions. Ask an admin to set your department.")
        return redirect("action_tracker:list")
    if request.method == "POST":
        form = ActionForm(request.POST, scope=scope)
        if form.is_valid():
            form.save()
            messages.success(request, "Action created.")
            return redirect("action_tracker:list")
    else:
        form = ActionForm(scope=scope, initial={"department": initial_department(request)})
    return render(request, "action_tracker/action_form.html", {"form": form})


def action_edit(request, pk):
    action = get_object_or_404(Action, pk=pk)
    visible_or_404(request, action.department_id)
    scope = get_scope(request)
    if request.method == "POST":
        form = ActionForm(request.POST, instance=action, scope=scope)
        if form.is_valid():
            form.save()
            messages.success(request, "Action updated.")
            return redirect("action_tracker:detail", pk=pk)
    else:
        form = ActionForm(instance=action, scope=scope)
    return render(request, "action_tracker/action_form.html", {"form": form, "action": action})


def action_detail(request, pk):
    action = get_object_or_404(Action, pk=pk)
    visible_or_404(request, action.department_id)
    if request.method == "POST":
        if "add_step" in request.POST:
            desc = request.POST.get("step_description", "").strip()
            if desc:
                ActionStep.objects.create(action=action, step_description=desc)
                if action.status == "Completed":
                    action.sync_status_from_steps()      # a new open step reopens it
        elif "toggle_step" in request.POST:
            step = get_object_or_404(ActionStep, pk=request.POST["toggle_step"], action=action)
            if step.is_completed:
                step.reopen(request.user)
            else:
                step.close(request.user)
            action.refresh_from_db()
            action.sync_status_from_steps()
        elif "delete_step" in request.POST:
            get_object_or_404(ActionStep, pk=request.POST["delete_step"], action=action).delete()
            action.sync_status_from_steps()
        elif "save_status" in request.POST:
            new_status = request.POST.get("status", action.status)
            if new_status in dict(Action._meta.get_field("status").choices):
                action.status = new_status
                action.save(update_fields=["status"])
        elif "save_comments" in request.POST:
            action.comments = request.POST.get("comments", "")
            action.save(update_fields=["comments"])
            messages.success(request, "Notes saved.")
        return redirect("action_tracker:detail", pk=pk)

    steps = list(action.steps.all())
    done = sum(s.is_completed for s in steps)
    return render(request, "action_tracker/action_detail.html", {
        "action": action, "steps": steps, "done": done, "total": len(steps),
        "percent": round(100 * done / len(steps)) if steps else 0,
    })


@require_POST
def action_set_status(request, pk):
    """One-click Mark done / Reopen from the list."""
    action = get_object_or_404(Action, pk=pk)
    visible_or_404(request, action.department_id)
    target = "Pending" if action.status == "Completed" else "Completed"
    action.status = target
    action.save(update_fields=["status"])
    if target == "Completed":
        for step in action.steps.filter(is_completed=False):
            step.close(request.user)
    messages.success(request, f"'{action.action_item}' marked {'done' if target == 'Completed' else 'open again'}.")
    return redirect(request.POST.get("next") or "action_tracker:list")


def action_delete(request, pk):
    action = get_object_or_404(Action, pk=pk)
    visible_or_404(request, action.department_id)
    if request.method == "POST":
        action.delete()
        messages.success(request, "Deleted.")
    return redirect("action_tracker:list")


def clear_completed(request):
    if request.method == "POST":
        filter_qs(Action.objects.filter(status="Completed"), get_scope(request)).delete()      # only what this person can see
        messages.success(request, "Archive cleared.")
    return redirect("action_tracker:list")
