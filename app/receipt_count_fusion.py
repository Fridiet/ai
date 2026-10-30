"""Experimental row-level count recovery retaining primary product identities."""
from difflib import SequenceMatcher
import math
import re
from .ingredient_matcher import lookup_key
from .receipt_columns import bounds


def needs_secondary_count_recovery(analysis):
    """Experimental trigger: only named food rows with price evidence need a pass."""
    return any(p.get('purchase_quantity') is None and p.get('prices')
               and p.get('match', {}).get('standard_food_name')
               and p.get('match', {}).get('classification') in {'ingredient', 'processed_food'}
               for p in analysis['products'])


def compatible(primary, secondary):
    if secondary.get('purchase_quantity_source') != 'VERIFIED_RECEIPT_COLUMNS':
        return False
    count = secondary.get('purchase_quantity')
    if isinstance(count, bool) or not isinstance(count, (int, float)) or not math.isfinite(count) or not 1 <= count <= 99 or int(count) != count:
        return False
    # Unbalanced arithmetic can leave purchase_quantity empty even though the
    # primary OCR observed a conflicting digit. Do not silently replace it.
    text = re.sub(r'(?<=\d),\s+(?=\d{3}(?:\D|$))', ',', primary.get('raw_text', ''))
    observed = re.search(r'(?:^|\s)(\d[\d,]*)\s+(\d{1,3})\s+(\d[\d,]*)\s*원?\s*[*#%]{0,2}\s*$', text)
    if observed and int(observed.group(2)) != count:
        return False
    a = lookup_key(primary['normalized_name'])
    b = lookup_key(secondary['normalized_name'])
    if not a or not b or SequenceMatcher(None, a, b).ratio() < .9:
        return False
    prices = set(primary.get('prices', []))
    if not prices or not prices.issubset(set(secondary.get('prices', []))):
        return False
    left,top,right,bottom = bounds(primary)
    x1,y1,x2,y2 = bounds(secondary)
    if min(right-left, bottom-top, x2-x1, y2-y1) <= 0:
        return False
    if abs((top+bottom-y1-y2)/2) > min(bottom-top,y2-y1)*.4:
        return False
    if min(right,x2)-max(left,x1) < min(right-left,x2-x1)*.5:
        return False
    first = primary.get('match', {}).get('standard_food_name')
    second = secondary.get('match', {}).get('standard_food_name')
    return not (first and second and first != second)


def supplement_product_counts(primary, secondary):
    products = [dict(p) for p in primary['products']]
    candidates = {}
    for i,p in enumerate(products):
        # Counted rows must also reserve matching secondary evidence. Otherwise
        # a duplicate/overlapping primary detection can borrow their count.
        candidates[i] = [j for j,s in enumerate(secondary['products']) if compatible(p,s)]
    for i, matches in candidates.items():
        if products[i].get('purchase_quantity') is not None:
            continue
        if len(matches) != 1:
            continue
        j = matches[0]
        if sum(j in options for options in candidates.values()) != 1:
            continue
        s = secondary['products'][j]
        products[i].update(purchase_quantity=s['purchase_quantity'],
            purchase_quantity_source='SECONDARY_VERIFIED_RECEIPT_COLUMNS',
            secondary_quantity_evidence=s['raw_text'])
    return dict(primary, products=products)
