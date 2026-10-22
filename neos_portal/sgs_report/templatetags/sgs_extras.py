from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from ..models import fmt_signed

import re

register = template.Library()


@register.filter
def signed(value, digits=3):
    """Renders a number with `digits` decimals; negative values show in
    red, wrapped in parentheses, matching the Excel/Word output."""
    if value is None:
        return ""
    text = fmt_signed(value, int(digits))
    if text.startswith("("):
        return mark_safe(f'<span style="color:#c00000;">{text}</span>')
    return text


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@register.filter
def highlight_numbers(value):
    """Wraps every numeric value inside a piece of text (e.g. a
    Sequence of Events description) in red, so depths/pressures/times
    mentioned in free-text stand out at a glance."""
    if not value:
        return value
    escaped = escape(value)
    highlighted = _NUMBER_RE.sub(
        lambda m: f'<span style="color:#c00000; font-weight:700;">{m.group(0)}</span>', escaped
    )
    return mark_safe(highlighted)
