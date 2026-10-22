"""
Column-layout helpers shared by the Excel export, template, and import
views, so all three always agree on header text and column order.
"""

from .models import FieldDefinition


def build_excel_columns(field_defs):
    """
    Returns (headers, col_specs) where col_specs[i] = (kind, field_definition)
    and headers[i] is the matching column title. col_specs[0] is always
    ("name", None) for the asset's own name/serial column, and col_specs[1]
    is always ("description", None) for the asset's required description.

    kind is one of: "name", "simple", "value_unit_value", "value_unit_unit",
    "attachment" (attachment columns are exported for reference only and
    are never read back in on import).
    """
    headers = ["Asset No / Serial No", "Description"]
    col_specs = [("name", None), ("description", None)]

    for fd in field_defs:
        if fd.field_type == FieldDefinition.VALUE_UNIT:
            headers.append(f"{fd.label} (value)")
            col_specs.append(("value_unit_value", fd))
            headers.append(f"{fd.label} (unit)")
            col_specs.append(("value_unit_unit", fd))
        elif fd.field_type == FieldDefinition.ATTACHMENT:
            headers.append(f"{fd.label} (file \u2014 not importable)")
            col_specs.append(("attachment", fd))
        else:
            headers.append(fd.label)
            col_specs.append(("simple", fd))

    return headers, col_specs
