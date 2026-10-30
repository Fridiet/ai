"""Merge independently read numeric cells without overwriting row evidence."""
import math
import re
from collections import defaultdict
from .receipt_count_fusion import compatible,supplement_product_counts


def verified_triplet(cells):
    fields={x['field']:x for x in cells}
    if len(cells)!=3 or set(fields)!={'unit_price','count','total'}:return None
    if any(not math.isfinite(x['confidence']) or x['confidence']<.9 for x in cells):return None
    values={}
    for key,x in fields.items():
        pattern=r'[1-9]\d?' if key=='count' else r'(?:[1-9]\d{2,6}|[1-9]\d{0,2}(?:,\d{3})+)'
        if not re.fullmatch(pattern,x['text'].strip()):return None
        values[key]=int(x['text'].strip().replace(',',''))
    return values if values['unit_price']*values['count']==values['total'] else None


def supplement_numeric_cells(primary,secondary,cells):
    grouped=defaultdict(list)
    for cell in cells:
        if cell.get('margin')==0:grouped[cell['row']].append(cell)
    products=[dict(p) for p in secondary['products']]
    confirmed={}
    for index,readings in grouped.items():
        if not isinstance(index,int) or not 0<=index<len(products):continue
        original=products[index]
        if any(c.get('secondary')!=original['raw_text'] for c in readings):continue
        values=verified_triplet(readings)
        if values is None:continue
        if original.get('prices',[])[-2:]!=[values['unit_price'],values['total']]:continue
        if original.get('purchase_quantity') not in (None,values['count']):continue
        evidence=dict(original,purchase_quantity=values['count'],purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS')
        # Check the secondary raw text too: a previously observed 7 must not
        # silently become 1 merely because the numeric crop now says 1.
        if not compatible(original,evidence):continue
        products[index]=evidence
        confirmed[index]=values
    result=supplement_product_counts(primary,dict(secondary,products=products))
    output=[dict(p) for p in result['products']]
    for index,p in enumerate(output):
        if primary['products'][index].get('purchase_quantity') is not None:continue
        matches=[j for j,s in enumerate(products) if compatible(primary['products'][index],s)]
        if p.get('purchase_quantity') is not None and len(matches)==1 and matches[0] in confirmed:
            p['purchase_quantity_source']='NUMERIC_CELLS_VERIFIED'
            p['numeric_cell_evidence']=confirmed[matches[0]]
    return dict(result,products=output)
