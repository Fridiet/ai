"""Two-column receipt quantity evidence validated against printed grand totals."""
import re


def quantity_total_evidence(rows):
    """Return row-index evidence only for a complete contiguous balanced block.

    This does not infer missing digits from prices. Every count must be printed
    in a separate OCR box; gifted products have a literal 증정 amount box.
    """
    totals = []
    for index, row in enumerate(rows):
        if len(row) != 3:
            continue
        label, count, amount = row
        if re.sub(r'\s+', '', label['text']) != '총구매액':
            continue
        if min(count.get('confidence', 0), amount.get('confidence', 0)) < .9:
            continue
        if not re.fullmatch(r'\d{1,3}', count['text'].strip()) or not re.fullmatch(r'\d{1,3}(?:,\d{3})+', amount['text'].strip()):
            continue
        totals.append((index, int(count['text']), int(amount['text'].replace(',', ''))))
    if len(totals) != 1:
        return {}
    end, total_count, total_amount = totals[0]
    found = {}
    for index in range(end-1, -1, -1):
        row = rows[index]
        if len(row) != 3:
            break
        name, count, amount = row
        if not re.search(r'[가-힣A-Za-z]', name['text']):
            break
        if min(count.get('confidence', 0), amount.get('confidence', 0)) < .9:
            break
        if not re.fullmatch(r'[1-9]\d{0,2}', count['text'].strip()):
            break
        value = amount['text'].strip()
        gift = value == '증정'
        if not gift and not re.fullmatch(r'\d{1,3}(?:,\d{3})+', value):
            break
        found[index] = dict(count=int(count['text']), amount=0 if gift else int(value.replace(',', '')),
                            transaction_type='gift' if gift else 'purchase')
    if (len(found) < 2 or sum(v['count'] for v in found.values()) != total_count
            or sum(v['amount'] for v in found.values()) != total_amount):
        return {}
    return found
