from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    if not d:
        return None
    return d.get(str(key))


@register.filter
def get_key(mapping, key):
    """{{ colours|get_key:client }} -> the colour for that client (or grey)."""
    return (mapping or {}).get(key, "#6B6880")
