"""Validate reread column labels without replacing existing OCR evidence."""
import re
from .product_pipeline import clean_ocr_items,group_product_rows,is_product_table_header
from .receipt_columns import bounds,column_anchors


def header_seed(raw):
    headers=[r for r in group_product_rows(clean_ocr_items(raw)) if is_product_table_header(r)]
    if len(headers)!=1:return None
    header=headers[0]
    fields=[i for i in header if re.sub(r'\s+','',i['text']) in {'단가','수량','금액'}]
    if len(fields)!=2 or len({re.sub(r'\s+','',i['text']) for i in fields})!=2:return None
    if any(i.get('confidence',0)<.8 for i in fields):return None
    return header


def recover_header(image, raw):
    from .ocr import run_ocr
    header=header_seed(raw)
    if header is None:return raw
    top=min(bounds(i)[1] for i in header);bottom=max(bounds(i)[3] for i in header)
    height=bottom-top
    if height<=0:return raw
    y1=max(0,int(top-height*.25));y2=min(image.shape[0],int(bottom+height*.25))
    if y2<=y1:return raw
    reread=run_ocr(image[y1:y2].copy())
    aligned=[dict(i,box=[[x,y+y1] for x,y in i['box']]) for i in reread]
    return supplement_header_labels(raw,aligned)


def supplement_header_labels(raw, reread):
    headers=[r for r in group_product_rows(clean_ocr_items(raw)) if is_product_table_header(r)]
    fresh=[r for r in group_product_rows(clean_ocr_items(reread)) if is_product_table_header(r)]
    if len(headers)!=1 or len(fresh)!=1:return raw
    old,new=headers[0],fresh[0]
    if column_anchors(old) is not None:return raw
    anchors=column_anchors(new)
    if anchors is None:return raw
    labels={'단가','수량','금액'}
    def key(item):return re.sub(r'\s+','',item['text'])
    old_fields={key(i):i for i in old if key(i) in labels}
    new_fields={key(i):i for i in new if key(i) in labels}
    if len(old_fields)<2 or len(new_fields)!=3:return raw
    if sum(key(i) in labels for i in old)!=len(old_fields):return raw
    if any(i.get('confidence',0)<.8 for i in old_fields.values()):return raw
    gap=min(anchors[1]-anchors[0],anchors[2]-anchors[1])
    top=min(bounds(i)[1] for i in old);bottom=max(bounds(i)[3] for i in old)
    for name,item in new_fields.items():
        x1,y1,x2,y2=bounds(item)
        if not top <= (y1+y2)/2 <= bottom:return raw
        if item.get('confidence',0)<.8:return raw
        if name in old_fields:
            a,_,b,_=bounds(old_fields[name])
            if abs((x1+x2-a-b)/2)>gap*.2:return raw
    missing=labels-old_fields.keys()
    if len(missing)!=1:return raw
    addition=dict(new_fields[next(iter(missing))],header_reread=True)
    return [*raw,addition]
