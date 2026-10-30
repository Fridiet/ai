"""Experimental numeric-only supplementation; never replaces primary OCR text."""
import re
from .receipt_columns import bounds, column_anchors


def supplement_numbers(primary, secondary, rows):
    headers = [i for i, row in enumerate(rows) if re.search(
        r'상품명|제품명', re.sub(r'\s+', '', ''.join(x['text'] for x in row)))]
    if not headers:
        return list(primary)
    start = headers[0]
    anchors = column_anchors(rows[start], rows[start+1:])
    if anchors is None:
        return list(primary)
    p,q,t = anchors
    left, right = p-(q-p)/2, t+(t-q)/2
    body = []
    for row in rows[start+1:]:
        text = ''.join(x['text'] for x in row)
        if re.match(r'^[(*#\[]*(?:합계|총품목|결제|면세|과세|부가세)', re.sub(r'\s+', '', text)):
            break
        if re.search('[가-힣]', text) and re.search(r'\d{3}', text.replace(',', '')):
            body.append(row)
    result = list(primary)
    for candidate in secondary:
        if candidate.get('confidence', 0) < .95 or not re.fullmatch(
                r'(?:\d{1,3}(?:[,.]\d{3})+|\d{1,7})', candidate['text'].strip()):
            continue
        x1,y1,x2,y2 = bounds(candidate)
        cx,cy = (x1+x2)/2, (y1+y2)/2
        if not left < cx < right:
            continue
        aligned = [row for row in body if min(bounds(i)[1] for i in row) <= cy <= max(bounds(i)[3] for i in row)]
        if len(aligned) != 1:
            continue
        conflict = False
        for existing in result:
            a,b,c,d = bounds(existing)
            intersection = max(0,min(x2,c)-max(x1,a))*max(0,min(y2,d)-max(y1,b))
            small_area = max(1,min((x2-x1)*(y2-y1),(c-a)*(d-b)))
            if (a <= cx <= c and b <= cy <= d) or intersection/small_area > .2:
                conflict = True
                break
        if not conflict:
            result.append(dict(candidate, numeric_supplement=True))
    return result
