"""Availability semantics shared by recommendation and quantity comparison.

CUSTOM text is descriptive, not a parseable numeric amount. In particular an
old CUSTOM batch's numeric zero can be a placeholder rather than depletion.
"""
import math


def usable_inventory(unit_type, amount, status="ACTIVE"):
    if status is not None and str(status).upper() != "ACTIVE":
        return False
    if unit_type not in {"QUANTITY", "PERCENT", "CUSTOM"}:
        return False
    if unit_type == "CUSTOM":
        return True
    if amount is None:
        return True  # Ingredient is known; its usable quantity remains unknown.
    try:
        value = float(amount)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(value) and value > 0 and (unit_type != "PERCENT" or value <= 100)
