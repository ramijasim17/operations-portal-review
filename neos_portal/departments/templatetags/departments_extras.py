from django import template

register = template.Library()


@register.filter
def get_item(d, key):
    if not d:
        return None
    return d.get(key)


@register.filter
def cert_for(asset, cert_type_id):
    for c in asset.certs.all():
        if c.cert_type_id == cert_type_id:
            return c
    return None


@register.filter
def status_class(status):
    return {
        "valid": "valid",
        "soon": "soon",
        "expired": "expired",
        "missing": "missing",
        "na": "na",
    }.get(status, "missing")
