"""Conservative missing-count reconstruction from labeled receipt columns."""
import re
import statistics


def bounds(item):
    points=item['box']
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def column_anchors(header, rows=None):
    positions={}
    for item in header:
        key=re.sub(r'\s+','',item['text'])
        if key in {'단가','수량','금액'} and item.get('confidence',0)>=.8:
            if key in positions:
                return None
            b=bounds(item)
            positions[key]=(b[0]+b[2])/2
    if len(positions)!=3:
        return calibrated_anchors(header, rows or [])
    p,q,t=(positions[k] for k in ('단가','수량','금액'))
    return (p,q,t) if p<q<t and min(q-p,t-q)>10 else None


def calibrated_anchors(header, rows):
    """Merged header boxes need at least two independently balanced body rows."""
    header_text=re.sub(r'\s+','',''.join(i['text'] for i in header))
    if not all(k in header_text for k in ('단가','수량','금액')):
        return None
    samples=[]
    price_pairs=[]
    for row in rows:
        numbers=[]
        for item in sorted(row,key=lambda i:bounds(i)[0]):
            s=re.sub(r'\s+','',item['text']).rstrip('*#원')
            if re.fullmatch(r'(?:\d{1,3}(?:[,.]\d{3})+|\d+)',s) and item.get('confidence',0)>=.9:
                b=bounds(item)
                numbers.append((int(re.sub(r'\D','',s)),(b[0]+b[2])/2))
        if len(numbers)==2:
            (unit,x),(total,z)=numbers
            if unit>=100 and total>=unit and total%unit==0 and 1<=total//unit<=99 and x<z:
                price_pairs.append((x,z))
        if len(numbers)<3:
            continue
        (price,p),(count,q),(total,t)=numbers[-3:]
        if price>=100 and 1<=count<=99 and price*count==total and p<q<t:
            samples.append((p,q,t))
    if len(samples)<2:
        # A single fully observed balanced row may be corroborated by two
        # separate two-price rows. This uses labeled column geometry, never
        # arbitrary pairs of prices on headerless receipts. Any observed
        # count glyph still prevents reconstruction in the target row below.
        if len(samples)!=1 or len(price_pairs)<2:
            return None
        p,q,t=samples[0]
        gap=min(q-p,t-q)
        if gap<=10:
            return None
        supporting=[pair for pair in price_pairs
                    if abs(pair[0]-p)<=gap*.25 and abs(pair[1]-t)<=gap*.25]
        if len(supporting)<2:
            return None
        return p,q,t
    anchors=tuple(statistics.median(s[j] for s in samples) for j in range(3))
    gap=min(anchors[1]-anchors[0],anchors[2]-anchors[1])
    if gap<=10 or any(abs(s[j]-anchors[j])>gap*.25 for s in samples for j in range(3)):
        return None
    return anchors


def reconstruct_missing_count(row, anchors, all_items):
    if anchors is None:
        return None
    p,q,t=anchors
    unit_zone=(p-(q-p)/2,(p+q)/2)
    count_zone=((p+q)/2,(q+t)/2)
    total_zone=((q+t)/2,t+(t-q)/2)
    top=min(bounds(i)[1] for i in row)
    bottom=max(bounds(i)[3] for i in row)
    # Retain observed glyphs, including detections removed by confidence
    # filtering. Arithmetic may corroborate one matching digit, not overwrite
    # contradictory, ambiguous or duplicate evidence.
    observed_counts=[]
    for item in all_items:
        if not item.get('box') or not str(item.get('text','')).strip():
            continue
        x1,y1,x2,y2=bounds(item)
        if top <= (y1+y2)/2 <= bottom and count_zone[0] <= (x1+x2)/2 <= count_zone[1]:
            observed_counts.append(str(item['text']).strip())
    if len(observed_counts)>1:
        return None
    if observed_counts and not re.fullmatch(r'[1-9]\d?',observed_counts[0]):
        return None
    amounts=[[],[]]
    for item in row:
        value=re.sub(r'\s+','',item['text']).rstrip('*#원')
        if not re.fullmatch(r'(?:\d{1,3}(?:[,.]\d{3})+|\d{3,7})',value):
            continue
        if item.get('confidence',0)<.9:
            return None
        amount=int(re.sub(r'\D','',value))
        b=bounds(item)
        x=(b[0]+b[2])/2
        for j,(left,right) in enumerate((unit_zone,total_zone)):
            if left<x<right:
                amounts[j].append(amount)
    if any(len(v)!=1 for v in amounts):
        return None
    unit,total=amounts[0][0],amounts[1][0]
    if unit<100 or total<unit or total%unit:
        return None
    count=total//unit
    if observed_counts and int(observed_counts[0]) != count:
        return None
    return count if 1<=count<=99 else None
