from django import template

register = template.Library()


@register.filter
def thousands(value):
    """1234567 → '1 234 567' (узкий неразрывный пробел между разрядами)."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return value
    # формат с пробелом-разделителем, затем меняем на узкий неразрывный
    return f"{n:,}".replace(",", "\u202f")