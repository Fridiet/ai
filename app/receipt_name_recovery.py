"""Experimental name proposals only; not called by serving routes."""
from .ingredient_matcher import lookup_key
from .receipt_columns import bounds


def needs_secondary_name_hint(analysis):
    return any(not p.get('match',{}).get('standard_food_name')
               and p.get('match',{}).get('classification')=='unknown'
               and p.get('purchase_quantity_source')=='VERIFIED_RECEIPT_COLUMNS'
               and p.get('prices') for p in analysis['products'])


def attach_name_hints(primary, secondary):
    products=[dict(p) for p in primary['products']]
    for hint in propose_names(primary,secondary):
        products[hint['primary_index']]['secondary_name_hint']=hint
    return dict(primary,products=products)


def same_transaction_row(a,b):
    if a.get('purchase_quantity_source')!='VERIFIED_RECEIPT_COLUMNS' or b.get('purchase_quantity_source')!='VERIFIED_RECEIPT_COLUMNS':
        return False
    if a.get('purchase_quantity') != b.get('purchase_quantity') or not a.get('purchase_quantity'):
        return False
    if not a.get('prices') or a['prices']!=b.get('prices'):
        return False
    x,y,r,d=bounds(a); x2,y2,r2,d2=bounds(b)
    return (min(r-x,d-y,r2-x2,d2-y2)>0
            and abs((y+d-y2-d2)/2)<=min(d-y,d2-y2)*.4
            and min(r,r2)-max(x,x2)>=min(r-x,r2-x2)*.5)


def propose_names(primary,secondary):
    # Build correspondence over every row, including named rows, to avoid
    # stealing evidence from an already identified neighboring purchase.
    links={i:[j for j,b in enumerate(secondary['products']) if same_transaction_row(a,b)]
           for i,a in enumerate(primary['products'])}
    proposals=[]
    for i, js in links.items():
        a=primary['products'][i]
        if a.get('match',{}).get('standard_food_name') or a.get('match',{}).get('classification')!='unknown' or len(js)!=1:
            continue
        j=js[0]
        if sum(j in others for others in links.values())!=1:
            continue
        b=secondary['products'][j]
        match=b.get('match',{})
        name=match.get('standard_food_name')
        if not name or match.get('source') not in {'receipt_food_head_rule','receipt_fresh_suffix_rule','receipt_egg_head_rule'}:
            continue
        # Numeric confidence is not evidence that the name was read correctly.
        if not any(name in lookup_key(item['text']) and item.get('confidence',0)>=.95 for item in b.get('items',[])):
            continue
        proposals.append({'primary_index':i,'secondary_index':j,'canonical_name':name,
                          'original_raw_text':a['raw_text'],'secondary_raw_text':b['raw_text'],
                          'secondary_product_name':b['product_name'],'secondary_match':dict(match),
                          'requires_confirmation':True})
    return proposals
