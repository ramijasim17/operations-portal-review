from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()

FEET_PER_METRE = Decimal("3.28084")


@register.filter
def ft_m(value):
    """Show a length stored in feet as 'X ft (Y m)'."""
    try:
        feet = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    metres = (feet / FEET_PER_METRE).quantize(Decimal("0.1"))
    return f"{feet.quantize(Decimal('0.1'))} ft ({metres} m)"
