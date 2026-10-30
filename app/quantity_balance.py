"""Subtract compatible quantities, never guess density, pack size or count weight."""
import math
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN
from pathlib import Path
from .inventory_state import usable_inventory

UNITS = {}
for line in (Path(__file__).parent / 'data/quantity_units.properties').read_text(encoding='utf-8').splitlines():
    if line and not line.startswith('#'):
        key, spec = line.split('=', 1)
        unit, factor = spec.split(',')
        UNITS[key] = ({'g':'mass', 'mL':'volume', '개':'count'}[unit], Decimal(factor))


def number(value):
    try:
        value = Decimal(str(value))
        return value.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP) if value.is_finite() and 0 <= value < Decimal('1e12') else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def ingredient_balances(ingredients, inventory_by_name):
    grouped = {}
    for item in ingredients:
        if item.get("optional"):
            continue
        unit = str(item.get("unit") or "").strip().lower()
        spec = UNITS.get(unit)
        key = (item["canonical_name"], {"mass":"g","volume":"ml","count":"개"}[spec[0]] if spec else unit)
        value = number(item.get("quantity"))
        if value is not None and spec:
            value *= spec[1]
        previous = grouped.get(key, 0)
        grouped[key] = previous + value if previous is not None and value is not None else None
    result = []
    reserved = {}
    for (name, unit), needed in grouped.items():
        target = UNITS.get(unit)
        stock, uncertain = Decimal(0), False
        for index, batch in enumerate(inventory_by_name.get(name, [])):
            if not usable_inventory(batch.get('unitType', 'QUANTITY'), batch.get('remaining_quantity'), batch.get('status', 'ACTIVE')):
                continue
            amount = number(batch.get("remaining_quantity"))
            if amount is not None:
                amount -= reserved.get((name, index), Decimal(0))
            original_amount = amount
            source = UNITS.get(str(batch.get("unit") or "").strip().lower())
            if batch.get('quantityNeedsReview') or batch.get('unitType', 'QUANTITY') != 'QUANTITY':
                uncertain = True
                continue
            if source and source[0] == 'count' and target:
                if target[0] == 'count' and batch.get('countBasis'):
                    uncertain = True
                    continue
                per = UNITS.get(str(batch.get('perCountUnit') or '').strip().lower())
                size = number(batch.get('perCountQuantity'))
                if per and per[0] == target[0] and size is not None and size > 0 and amount is not None:
                    amount *= size
                    source = per
            if amount == 0:
                continue
            if amount is None or not target or not source or source[0] != target[0]:
                uncertain = True
            else:
                available = amount * source[1] / target[1]
                if needed is not None and original_amount is not None and original_amount > 0:
                    factor = available / original_amount
                    take = min(original_amount, max(Decimal(0), needed-stock) / factor).quantize(Decimal('0.000001'), rounding=ROUND_DOWN)
                    reserved[name, index] = reserved.get((name, index), Decimal(0)) + take
                stock += available
        known = needed is not None and needed > 0 and target is not None
        if known and stock >= needed:
            status, shortage = "ENOUGH", 0.0
        elif not known or uncertain:
            status, shortage = "UNKNOWN", None
        else:
            status, shortage = "SHORTAGE", needed-stock
        result.append({"name":name,"requiredQuantity":float(needed) if needed is not None else None,"knownAvailableQuantity":float(round(stock,6)),
            "shortageQuantity":float(round(shortage,6)) if shortage is not None else None,
            "unit":"mL" if unit == 'ml' else unit,"status":status})
    return result
