"""Repeated name / SKU-count-price-total layouts with explicit tax markers."""
import re
from .receipt_columns import bounds


def row_text(row):
    return ' '.join(i['text'] for i in row)


def numeric_count(row):
    text=re.sub(r'(?<=\d),\s+(?=\d{3})',',',row_text(row))
    match=re.fullmatch(r'\s*\d{6,8}\s+([1-9]\d?)\s+(\d[\d,]*)\s+(\d[\d,]*)\s*T\s*',text)
    if not match or any(i.get('confidence',0)<.9 for i in row):return None
    count,unit,total=(int(v.replace(',','')) for v in match.groups())
    return count if unit>=100 and unit*count==total else None


def pending_numeric_row(row):
    """Retain a damaged numeric line for review, never infer its count."""
    text=re.sub(r'(?<=\d),\s+(?=\d{3})',',',row_text(row))
    match=re.fullmatch(r'\s*([0-9A-Za-z:|]{6,9})\s+[0-9A-Za-z|]{1,3}\s+(\d[\d,]*)\s+(\d[\d,]*)\s*T\s*',text)
    if not match or sum(c.isdigit() for c in match[1])<5:return False
    return all(int(v.replace(',',''))>=100 for v in match.groups()[1:])


def adjacent(name,numeric):
    top=min(bounds(i)[1] for i in numeric)
    bottom=max(bounds(i)[3] for i in name)
    height=max(bounds(i)[3]-bounds(i)[1] for i in name)
    return height>0 and -.4*height<=top-bottom<=1.5*height


def merge_sku_rows(rows):
    pairs={}
    for index in range(len(rows)-1):
        name,numeric=rows[index:index+2]
        text=row_text(name)
        count=numeric_count(numeric)
        if (count is not None and re.search(r'[가-힣A-Za-z]',text)
                and not re.search(r'쿠폰|IRC|합계|소계|승인|회원|거래|Reg\s*#',text,re.I)
                and adjacent(name,numeric)):
            pairs[index]=count
    # A single accidental number sequence must not select a new receipt layout.
    if len(pairs)<2:return rows
    result=[]
    index=0
    while index<len(rows):
        if index+2<len(rows):
            header,name,numeric=rows[index:index+3]
            if ('쿠폰' in re.sub(r'\s+','',row_text(header))
                    and re.search(r'IRC\s*$',row_text(name),re.I)
                    and re.search(r'\d[\d,]*\s*-T\s*$',row_text(numeric))
                    and adjacent(header,name) and adjacent(name,numeric)):
                result.append([dict(i,receipt_adjustment=True) for r in (header,name,numeric) for i in r])
                index+=3
                continue
        pending=(index+1<len(rows) and pending_numeric_row(rows[index+1])
                 and re.search(r'[가-힣A-Za-z]',row_text(rows[index]))
                 and not re.search(r'쿠폰|IRC|합계|소계|승인|회원|거래|Reg\s*#',row_text(rows[index]),re.I)
                 and adjacent(rows[index],rows[index+1]))
        if index in pairs or pending:
            name,numeric=rows[index:index+2]
            result.append([dict(i,sku_count_verified=pairs[index]) if index in pairs else dict(i) for i in name]
                          +[dict(i,receipt_barcode=True) for i in numeric])
            index+=2
        else:
            result.append(rows[index])
            index+=1
    return result
