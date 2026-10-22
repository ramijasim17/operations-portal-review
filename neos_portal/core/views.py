from django.shortcuts import render

from accounts.decorators import role_required


def dashboard(request):
    from .dashboard_data import build

    from accounts.models import Profile

    context = build()
    context["online_people"] = list(Profile.online_now())
    return render(request, "core/dashboard.html", context)


@role_required("admin")
def settings_view(request):
    return render(request, "core/settings.html")