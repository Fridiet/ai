"""Order cards: separate offering contents from the number purchased.

Only explicit quantities are used. This is not a grams-per-piece estimator.
"""
import re
from decimal import Decimal

from .product_pipeline import _bounds, _select_product_match, normalize_product_name, _ingredient_as_product_match
from .ingredient_matcher import get_ingredient_catalog
from .product_catalog import get_product_catalog
from .ocr_classification import apply_classification

PRICE = re.compile(r'\d[\d,]*\s*원\s*[·.ㆍ]?\s*(\d+)\s*개\s*$')
SIZE = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)\s*(kg|mg|ml|g|l)(?![a-z가-힣])', re.I)
COUNT = re.compile(r'(?<![\d.])(\d+)\s*(개입|입|개|팩|봉|병|캔|세트)(?![가-힣])')
SCALE = {'kg': ('g', Decimal(1000)), 'mg': ('g', Decimal('.001')),
         'g': ('g', Decimal(1)), 'l': ('mL', Decimal(1000)), 'ml': ('mL', Decimal(1))}


def is_order_layout(items):
    return (sum('장바구니' in i['text'] and '담기' in i['text'] for i in items) >= 2
            and sum(bool(PRICE.search(i['text'])) for i in items) >= 2)


def order_cards(items):
    prices = sorted((i for i in items if PRICE.search(i['text'])), key=lambda i: _bounds(i)[1])
    cards = []
    bottom = 0
    for price in prices:
        px, py, _, _ = _bounds(price)
        title = [i for i in items if _bounds(i)[0] >= px - 25
                 and bottom <= _bounds(i)[1] < py - 5
                 and not re.search(r'배송완료|도착|장바구니|담기', i['text'])]
        title.sort(key=lambda i: (_bounds(i)[1], _bounds(i)[0]))
        if title:
            cards.append((title, price))
        bottom = _bounds(price)[3]
    return cards


def package_quantity(title):
    """Quantity in ONE purchased offering; purchase count is applied downstream."""
    unknown = {'quantity': 1.0, 'unit': '개', 'source': 'DEFAULT'}
    # Mixed-kit gross weight is not the net weight of a named constituent.
    if re.search(r'두부', title) and re.search(r'소스|드레싱', title):
        return unknown
    sizes = list(SIZE.finditer(title))
    counts = list(COUNT.finditer(title))
    if sizes:
        if len(sizes) != 1 or re.search(r'내외|약\s*\d|중량변동', title):
            return unknown
        size = sizes[0]
        number = Decimal(size.group(1))
        if number <= 0:
            return unknown
        # Embedded pack count is the contents; final 1개 is the offering count.
        embedded = [c for c in counts if c.group(2) in {'개입', '입'}]
        other = [c for c in counts if c.group(2) != '세트']
        chosen = embedded or other
        if chosen:
            if embedded and any(c.group(1) != '1' for c in counts if c not in embedded):
                return unknown
            factor = Decimal(chosen[0].group(1))
            if factor <= 0 or factor > 999:
                return unknown
            if len(chosen) > 1 and any(c.group(1) != '1' for c in chosen[1:]):
                return unknown
            number *= factor
        unit, scale = SCALE[size.group(2).lower()]
        return {'quantity': float(number * scale), 'unit': unit, 'source': 'ORDER_PACKAGE_CONTENTS'}
    if any(re.fullmatch(r'\s*\d+(?:\.\d+)?(?:k9|m|m1)?\s*', part, re.I)
           for part in title.split(',')[1:]):
        return unknown
    if counts:
        embedded = [c for c in counts if c.group(2) in {'개입', '입'}]
        chosen = embedded or counts
        count = int(chosen[0].group(1))
        if 0 < count <= 999 and all(c.group(1) == '1' for c in chosen[1:]):
            return {'quantity': float(count), 'unit': '개', 'source': 'ORDER_PACKAGE_COUNT'}
    return unknown


def refine_order_titles(image, items):
    """Re-read capacity tails from pixels, never replace 9 with g by guessing."""
    import cv2
    from .ocr import recognize_numeric_crops
    result = [dict(i) for i in items]
    targets, crops = [], []
    for lines, _ in order_cards(result):
        for item in lines:
            text = item['text']
            comma = text.find(',')
            if comma < 0 or not re.search(r'\d', text[comma:]):
                continue
            # Re-read only tails lacking a legible mass/volume unit. Count-only
            # tails are retained; crop recognition may supply missing g or mL.
            if SIZE.search(text[comma:]):
                continue
            x0, y0, x1, y1 = map(int, _bounds(item))
            weight = lambda value: sum(2 if '\uac00' <= c <= '\ud7a3' else 1 for c in value)
            start = x0 + int((x1-x0)*weight(text[:comma])/max(1, weight(text))) - 20
            crop = image[max(0,y0-4):min(image.shape[0],y1+4), max(0,start):min(image.shape[1],x1+4)]
            if crop.size:
                full = image[max(0,y0-3):min(image.shape[0],y1+3), max(0,x0-3):min(image.shape[1],x1+3)]
                crops.append(cv2.resize(full, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC))
                crops.append(cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_CUBIC))
                # Include a little preceding text: isolating a g glyph too
                # tightly can make it look like 9. Scale from text-line height.
                width = int((weight(text[comma:])/2 + .5) * (y1-y0))
                contextual = image[max(0,y0-4):min(image.shape[0],y1+4), max(x0,x1-width):min(image.shape[1],x1+4)]
                crops.append(cv2.resize(contextual, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))
                targets.append((item, comma))
    predictions = []
    for start in range(0, len(crops), 24):
        predictions.extend(recognize_numeric_crops(crops[start:start+24]))
    for index, (item, comma) in enumerate(targets):
        original = item['text'][comma:]
        for offset in (0, 1, 2):
            prediction = predictions[index*3+offset]
            tail = prediction['text']
            if offset == 0:
                if ',' not in tail:
                    continue
                tail = tail.split(',', 1)[1]
            size = SIZE.search(tail)
            if prediction['confidence'] < .88 or size is None:
                continue
            digits = size.group(1)
            # Numeric source must agree, but a trailing OCR 9 is not permitted
            # to become an extra digit of a weight (3009 -> 3009g is rejected).
            original_number = re.search(r'\d+(?:\.\d+)?', original)
            if original_number is None:
                continue
            observed = original_number.group()
            if observed.endswith('9') and not re.search(r'\d\s*[a-z]', original, re.I):
                observed = observed[:-1]
            if observed != digits:
                continue
            old_counts = [m.groups() for m in COUNT.finditer(original)]
            new_counts = [m.groups() for m in COUNT.finditer(tail)]
            if old_counts != new_counts:
                continue
            if offset and not COUNT.search(tail[:size.start()]):
                tail = tail[size.start():]
            item['order_original_text'] = item['text']
            item['text'] = item['text'][:comma] + ', ' + tail.strip()
            break
    return result


def analyze_order_items(items):
    products = []
    for index, (lines, price) in enumerate(order_cards(items)):
        title = ' '.join(i['text'] for i in lines)
        title = re.sub(r'^.*?로켓프레시\s*', '', title)
        # Capacity and SKU digits must not be interpreted as receipt prices.
        identity = title.split(',')[0]
        normalized = normalize_product_name(identity)
        catalog = get_ingredient_catalog()
        match = _select_product_match(normalized, product_catalog=get_product_catalog(),
                                      ingredient_catalog=catalog, match_threshold=.72)
        match = apply_classification(identity, match, catalog)
        # Fresh tofu can mention cooking uses; those words are not a ready-made
        # stew. Mixed tofu/sauce kits intentionally do NOT use this shortcut.
        if re.search(r'(?:^|\s|촌)두부(?:\s|$)', identity) and not re.search(r'소스|드레싱|찌개양념|밀키트', identity):
            match = _ingredient_as_product_match(catalog.match('두부'))
        # Breed words identify beef explicitly; do not infer the species of
        # generic 사태 or mixed mince from the country of origin.
        if re.search(r'와규|블랙\s*앵거스', identity) and re.search(r'갈비살|우둔', identity):
            match = _ingredient_as_product_match(catalog.match('소고기'))
        product = dict(normalized_name=normalized, match=match, prices=[],
                       average_confidence=sum(i['confidence'] for i in lines)/len(lines),
                       box=lines[0]['box'])
        product.update(row_index=index, product_name=title,
                       raw_text=' '.join(i.get('order_original_text', i['text']) for i in lines),
                       items=lines, purchase_quantity=int(PRICE.search(price['text']).group(1)),
                       purchase_quantity_source='VERIFIED_ORDER_PURCHASE_COUNT',
                       order_package_quantity=package_quantity(title))
        products.append(product)
    return dict(ocr_count=len(items), cleaned_count=len(items), row_count=len(products),
                product_count=len(products), products=products)
