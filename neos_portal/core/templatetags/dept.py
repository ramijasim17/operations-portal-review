from django import template

from core.dept_scope import BLOCKED, get_scope
from departments.models import Department

register = template.Library()


@register.inclusion_tag("core/_dept_scope.html", takes_context=True)
def dept_scope(context):
    """The Department box: a choice for Level 3 / Admin, a fixed label (or a warning) for Level 1 / 2."""
    request = context["request"]
    scope = get_scope(request)
    return {"scope": scope, "departments": list(Department.objects.all()), "path": request.path, "blocked": scope.mode == BLOCKED}
