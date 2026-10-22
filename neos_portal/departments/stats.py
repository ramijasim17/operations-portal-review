"""Counts for the Departments pages, gathered with a few grouped queries
(rather than one query per department / classification / asset)."""
import datetime

from django.db.models import Count, Q

from .models import Asset, AssetCert, Classification

SOON_DAYS = 30
EMPTY = {"assets": 0, "away": 0, "expired": 0, "soon": 0, "certs": 0}


def _cert_counts(group_field, filters):
    today = datetime.date.today()
    soon = today + datetime.timedelta(days=SOON_DAYS)
    rows = (
        AssetCert.objects.filter(not_applicable=False, **filters)
        .values(group_field)
        .annotate(
            certs=Count("pk"),
            expired=Count("pk", filter=Q(expiry_date__lt=today)),
            soon=Count("pk", filter=Q(expiry_date__gte=today, expiry_date__lte=soon)),
        )
    )
    return {r[group_field]: r for r in rows}


def _asset_counts(group_field, filters):
    rows = (
        Asset.objects.filter(**filters).values(group_field)
        .annotate(assets=Count("pk"), away=Count("pk", filter=~Q(current_location="Workshop")))
    )
    return {r[group_field]: r for r in rows}


def _merge(assets, certs, keys):
    out = {}
    for key in keys:
        a, c = assets.get(key, {}), certs.get(key, {})
        out[key] = {
            "assets": a.get("assets", 0), "away": a.get("away", 0),
            "expired": c.get("expired", 0), "soon": c.get("soon", 0), "certs": c.get("certs", 0),
        }
    return out


def classification_stats(department=None, classification=None):
    """{classification_id: {assets, away, expired, soon}} for one department or one classification."""
    if classification is not None:
        keys = [classification.pk]
        a_filter, c_filter = {"classification": classification}, {"asset__classification": classification}
    else:
        keys = list(Classification.objects.filter(department=department).values_list("pk", flat=True))
        a_filter, c_filter = {"classification__department": department}, {"asset__classification__department": department}
    return _merge(_asset_counts("classification", a_filter), _cert_counts("asset__classification", c_filter), keys)


def department_stats(departments):
    """{department_id: {classifications, assets, away, expired, soon}}."""
    ids = [d.pk for d in departments]
    assets = _asset_counts("classification__department", {"classification__department__in": ids})
    certs = _cert_counts("asset__classification__department", {"asset__classification__department__in": ids})
    class_counts = dict(
        Classification.objects.filter(department__in=ids).values_list("department").annotate(n=Count("pk"))
    )
    merged = _merge(assets, certs, ids)
    for pk in ids:
        merged[pk]["classifications"] = class_counts.get(pk, 0)
    return merged


def total(stats_by_key):
    out = dict(EMPTY, classifications=0)
    for s in stats_by_key.values():
        for k in out:
            out[k] += s.get(k, 0)
    return out
