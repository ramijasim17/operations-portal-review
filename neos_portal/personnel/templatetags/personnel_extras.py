from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    if not d:
        return None
    return d.get(key)


# Brand-led tints that the dark brand text (Ink) reads on; never white text on cyan or yellow.
AVATAR_COLOURS = ["#00A3E4", "#F5E100", "#A89DE0", "#66CBF0", "#D6D0F3", "#99DCF5", "#F7EE7A", "#B9AEE8"]


@register.filter
def initials(name):
    parts = [w for w in str(name or "").replace("-", " ").split() if w]
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()


@register.filter
def avatar_colour(name):
    """The same person always gets the same colour."""
    return AVATAR_COLOURS[sum(map(ord, str(name or ""))) % len(AVATAR_COLOURS)]
