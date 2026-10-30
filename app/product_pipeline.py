import re
import statistics
import unicodedata
import logging
import time
from collections import Counter
from typing import Any

from .ingredient_matcher import get_ingredient_catalog
from .product_catalog import get_product_catalog
from .ocr_classification import apply_classification
from .receipt_columns import column_anchors, reconstruct_missing_count
from .receipt_quantity_totals import quantity_total_evidence
from .receipt_metadata import is_receipt_metadata


PRICE_PATTERN = re.compile(
    r"(?<![0-9A-Za-z가-힣])(?:\d{1,3}(?:[,.]\d{3})+|\d{3,7})(?:\s*원)?(?![0-9A-Za-z가-힣])"
)
UNIT_PATTERN = re.compile(
    r"(?<!\d)\d+(?:\.\d+)?\s*(?:kg|g|mg|ml|l|개|입|팩|봉|병|캔|박스|포|장|ea)(?![A-Za-z가-힣])",
    re.IGNORECASE,
)
PROMOTION_PATTERN = re.compile(
    r"(?:행사|할인|증정|냉장|냉동|국내산|수입산|신선|유기농|무농약|특가|1\s*\+\s*1|2\s*\+\s*1)",
    re.IGNORECASE,
)
NOISE_KEYWORDS = {
    "영수증",
    "상품명",
    "수량",
    "단가",
    "금액",
    "합계",
    "결제",
    "부가세",
    "과세",
    "면세",
    "카드",
    "현금",
    "승인번호",
    "사업자",
    "전화",
    "매장",
    "구매일시",
    "거래일시",
}


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "｜": "|"}))
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" |_-\t\r\n")


def _bounds(item: dict[str, Any]) -> tuple[float, float, float, float]:
    points = item["box"]
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _intersection_over_union(left: dict[str, Any], right: dict[str, Any]) -> float:
    lx1, ly1, lx2, ly2 = _bounds(left)
    rx1, ry1, rx2, ry2 = _bounds(right)
    width = max(0.0, min(lx2, rx2) - max(lx1, rx1))
    height = max(0.0, min(ly2, ry2) - max(ly1, ry1))
    intersection = width * height
    left_area = max(1.0, (lx2 - lx1) * (ly2 - ly1))
    right_area = max(1.0, (rx2 - rx1) * (ry2 - ry1))
    return intersection / (left_area + right_area - intersection)


def clean_ocr_items(items: list[dict[str, Any]], min_confidence: float = 0.35) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for source in items:
        text = clean_text(str(source.get("text", "")))
        confidence = float(source.get("confidence", 0.0))
        box = source.get("box") or []
        if not text or confidence < min_confidence or len(box) < 4:
            continue
        candidate = {"text": text, "confidence": confidence, "box": box}

        duplicate_index = next(
            (
                index
                for index, existing in enumerate(cleaned)
                if clean_text(existing["text"]).lower() == text.lower()
                and _intersection_over_union(existing, candidate) >= 0.7
            ),
            None,
        )
        if duplicate_index is None:
            cleaned.append(candidate)
        elif confidence > cleaned[duplicate_index]["confidence"]:
            cleaned[duplicate_index] = candidate

    return sorted(cleaned, key=lambda item: ((_bounds(item)[1] + _bounds(item)[3]) / 2, _bounds(item)[0]))


def group_product_rows(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    if not items:
        return []
    heights = [max(1.0, _bounds(item)[3] - _bounds(item)[1]) for item in items]
    median_height = statistics.median(heights)
    rows: list[list[dict[str, Any]]] = []
    row_centers: list[float] = []
    center_tolerance = median_height * 0.5

    for item in items:
        _, top, _, bottom = _bounds(item)
        center = (top + bottom) / 2
        candidates = [
            (abs(center - row_center), index)
            for index, row_center in enumerate(row_centers)
            if abs(center - row_center) <= center_tolerance
        ]
        if not candidates:
            rows.append([item])
            row_centers.append(center)
        else:
            _, best_index = min(candidates)
            rows[best_index].append(item)
            # Keep a stable baseline for the row. Expanding top/bottom bounds
            # lets adjacent receipt lines overlap transitively and collapse
            # several products into one row on tightly printed receipts.
            row_centers[best_index] = statistics.median(
                (_bounds(member)[1] + _bounds(member)[3]) / 2
                for member in rows[best_index]
            )

    rows.sort(key=lambda row: min(_bounds(item)[1] for item in row))
    for row in rows:
        row.sort(key=lambda item: _bounds(item)[0])
    return rows


def price_matches(text: str):
    """Printed contents such as 340 ml are not currency columns."""
    units = [match.span() for match in UNIT_PATTERN.finditer(text)]
    return (match for match in PRICE_PATTERN.finditer(text)
            if not any(start <= match.start() < end for start, end in units))


def parse_prices(text: str) -> list[int]:
    text = re.sub(r'(?<=\d),\s+(?=\d{3}(?:\D|$))', ',', text)
    prices: list[int] = []
    for match in price_matches(text):
        value = int(re.sub(r"\D", "", match.group()))
        if 100 <= value <= 9_999_999:
            prices.append(value)
    return prices


def receipt_purchase_quantity(text: str) -> int | None:
    """Accept purchase count only when the final price/count/total columns balance."""
    text = re.sub(r'(?<=\d),\s+(?=\d{3}(?:\D|$))', ',', text)
    match = re.search(r"(?:^|\s)(\d[\d,]*)\s+(\d{1,3})\s+(\d[\d,]*)\s*원?\s*[*#]?\s*$", text)
    if not match:
        return None
    price, count, total = (int(value.replace(',', '')) for value in match.groups())
    return count if price >= 100 and 1 <= count <= 999 and price * count == total else None


def is_adjustment_row(text: str) -> bool:
    # POS promotion detail without a product name; OCR may lose its minus sign.
    # M+N identifies a promotion detail. Damaged 1+1 may read 11-1 or 111;
    # require the terminal 덤 and one amount, never consume a product name.
    if re.fullmatch(r'\s*\[M\s*\+\s*N\]\s*[\d+\-\s]+덤\s*[-−]?\s*\d[\d, .]*\s*', text, flags=re.I):
        return True
    if re.fullmatch(r'\s*\[[^\]]+\]\s*(?:\d+%|\d+\s*\+\s*\d+(?:덤)?)\s*[-−]?\s*[\d, .]+\s*', text):
        return True
    if re.search(r"(?:^|\s)[−-]\s*\d[\d,]*\s*$", text):
        return True
    # Discount detail rows often lose their minus sign during recognition.
    if receipt_purchase_quantity(text) is None:
        return bool(re.search(r"할인|에누리|쿠폰|포인트|[Ss][- ]?POINT|\d+월\s*\d+\s*(?:주|추).*점", text))
    return False


def strip_receipt_identifiers(text: str) -> str:
    """Remove explicit SKU fields, not arbitrary numbers inside product names."""
    value = clean_text(text)
    # POS row numbers such as '003 롯데...' were previously parsed as a price,
    # truncating the entire product name before classification could run.
    value = re.sub(r'^0\d{2}\s*(?=[가-힣A-Za-z])', '', value)
    value = re.sub(r"^\s*\*?\s*\(\d{2,7}\)\s*(?=[가-힣A-Za-z])", "", value)
    value = re.sub(r"\[\s*\d{6,14}\s*\]", " ", value)
    value = re.sub(r"^\s*[,;]*\s*\d{1,3}\s*[*.)]\s*", "", value)
    value = re.sub(r"\b\d{8,14}\b", " ", value)
    return value.strip(" ,;")


def extract_product_name(text: str) -> str:
    value = strip_receipt_identifiers(text)
    value = re.sub(r'(?<=\d)([,.])\s+(?=\d{3}(?:\D|$))', r'\1', value)
    first_price = next(price_matches(value), None)
    return (value[:first_price.start()] if first_price else value).strip(" ,;")


def normalize_product_name(text: str) -> str:
    value = strip_receipt_identifiers(text)
    value = re.sub(r"^\s*\d{1,3}(?:[.)]|(?=\s))\s*", "", value)
    value = re.sub(r"\b\d{8,14}\b", " ", value)
    value = UNIT_PATTERN.sub(" ", value)
    value = PRICE_PATTERN.sub(" ", value)
    value = re.sub(r"\b\d+\s*[x×*]\s*\d+\b", " ", value, flags=re.IGNORECASE)
    value = UNIT_PATTERN.sub(" ", value)
    value = PROMOTION_PATTERN.sub(" ", value)
    value = re.sub(r"[\[({<]\s*(?:행사|할인|증정|특가|냉장|냉동)[^\])}>]*[\])}>]", " ", value)
    value = re.sub(r"[^0-9A-Za-z가-힣\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _is_noise_row(raw_text: str, normalized_name: str) -> bool:
    if is_receipt_metadata(raw_text):
        return True
    compact = re.sub(r"\s+", "", raw_text)
    if any(keyword in compact for keyword in NOISE_KEYWORDS):
        return True
    if not re.search(r"[A-Za-z가-힣]", normalized_name):
        return True
    letters = re.sub(r"[^A-Za-z가-힣]", "", normalized_name)
    if len(letters) >= 2:
        return False
    if letters in {e.name for e in get_ingredient_catalog().entries if len(e.name) == 1}:
        return False
    # Unknown single glyphs are not guessed, but a balanced product row should
    # remain visible for review instead of silently disappearing.
    return receipt_purchase_quantity(raw_text) is None


def _row_box(row: list[dict[str, Any]]) -> list[list[int]]:
    bounds = [_bounds(item) for item in row]
    left = int(min(bound[0] for bound in bounds))
    top = int(min(bound[1] for bound in bounds))
    right = int(max(bound[2] for bound in bounds))
    bottom = int(max(bound[3] for bound in bounds))
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def _ingredient_as_product_match(ingredient_match: dict[str, Any]) -> dict[str, Any]:
    return {
        "standard_food_name": ingredient_match["standard_name"],
        "food_type": "원재료",
        "classification": "ingredient",
        "manufacturer": None,
        "score": ingredient_match["score"],
        "match_type": ingredient_match["match_type"],
        "source": "ingredient_dictionary",
        "matched_name": ingredient_match["matched_alias"],
    }


def _select_product_match(
    normalized_name: str,
    *,
    product_catalog: Any,
    ingredient_catalog: Any,
    match_threshold: float,
) -> dict[str, Any]:
    product_match = product_catalog.match(normalized_name, threshold=match_threshold)
    ingredient_match = ingredient_catalog.match(normalized_name, threshold=match_threshold)
    if product_match.get('inventory_identity_verified'):
        # An exact existing specific identity (감자칩) is more informative than
        # a catalog's legal class (과자). Partial raw matches are not equivalent.
        if ingredient_match.get('standard_name') and ingredient_match.get('match_type') in {'exact', 'alias'}:
            return _ingredient_as_product_match(ingredient_match)
        return product_match
    if ingredient_match["standard_name"] is None:
        return product_match

    # The curated seed catalog contains product-level exceptions such as
    # "오징어짬뽕 -> 라면" and must keep priority. K-FIND/MFDS often returns a
    # broad legal food type (for example "향신료") for an otherwise explicit
    # ingredient such as 다진마늘, so the ingredient dictionary is more useful
    # for inventory registration in that case.
    if (
        product_match["standard_food_name"] is not None
        and product_match["source"] == "seed_product_catalog"
    ):
        return product_match
    return _ingredient_as_product_match(ingredient_match)


def is_internal_product_code(text):
    return bool(re.fullmatch(r'\$?\*\d{6}', text.strip()))


def is_product_table_header(row):
    # Footer prose mentioning 상품명 is not a table header.
    for item in row:
        key = re.sub(r'\s+', '', item['text']).lower()
        if key in {'상품명', '품명'} or re.fullmatch(r'(?:no[.]?)?(?:상품명|품명)(?:단가)?수량금액', key):
            return True
    return False


def is_price_continuation(row):
    """Allow an OCR-damaged barcode/count only in a two-price layout.

    No digit repair is performed: uncertain quantity glyphs remain unchanged.
    """
    text = ' '.join(item['text'] for item in row)
    if re.fullmatch(r'[\d\s,.*#]+', text):
        return len(parse_prices(text)) >= 2
    # This establishes row attachment, not a verified quantity. Preserve OCR
    # suffixes and uncertain count glyphs for the stricter arithmetic stage.
    prices = [i for i in row if re.fullmatch(r'(?:\d{1,3}(?:[,.]\d{3})+|\d{3})[*#%]{0,2}', i['text'].strip())
              and i.get('confidence', 0) >= .8]
    if len(prices) != 2:
        return False
    prices.sort(key=lambda i: _bounds(i)[0])
    left_price, right_price = prices
    if _bounds(left_price)[2] >= _bounds(right_price)[0]:
        return False
    extras = [i for i in row if i is not left_price and i is not right_price]
    barcodes = counts = 0
    for item in extras:
        value = item['text'].strip()
        x1, _, x2, _ = _bounds(item)
        if ((is_internal_product_code(value) or
             (re.fullmatch(r'[0-9A-Za-z]{8,14}', value) and sum(c.isdigit() for c in value) >= 8))
                and x2 < _bounds(left_price)[0]):
            barcodes += 1
        elif (re.fullmatch(r'[0-9A-Za-z|\]\-~*]{1,3}', value)
              and x1 > _bounds(left_price)[2] and x2 < _bounds(right_price)[0]):
            counts += 1
        else:
            return False
    return barcodes == 1 and counts <= 1


def merge_price_leading_rows(rows):
    """Detect repeated barcode/price then name pairs immediately after a header.

    Require three consecutive pairs before selecting this layout. Reorder only
    the evidence list (name then price); original polygons remain unchanged.
    """
    headers = [i for i, row in enumerate(rows) if is_product_table_header(row)]
    if not headers:
        return None
    start = headers[0] + 1
    pairs = []
    index = start
    while index + 1 < len(rows):
        numeric, name = rows[index:index+2]
        ordered = sorted(numeric, key=lambda x: _bounds(x)[0])
        if not ordered or not re.fullmatch(r'\d{8,14}', ordered[0]['text'].strip()):
            break
        price_text = ' '.join(x['text'] for x in ordered[1:])
        if receipt_purchase_quantity(price_text) is None:
            break
        name_text = ' '.join(x['text'] for x in name)
        if (not re.search(r'[가-힣A-Za-zА-Яа-я]', name_text)
                or re.search(r'\d[,.]\d{3}', name_text)
                or is_adjustment_row(name_text)
                or _is_noise_row(name_text, normalize_product_name(name_text))):
            break
        height = statistics.median(_bounds(x)[3]-_bounds(x)[1] for x in numeric)
        gap = min(_bounds(x)[1] for x in name)-max(_bounds(x)[3] for x in numeric)
        if not -max(height, 1)*.4 <= gap <= max(height, 1)*1.5:
            break
        evidence = [dict(ordered[0], receipt_barcode=True)] + ordered[1:]
        pairs.append(name + evidence)
        index += 2
    if len(pairs) < 3:
        return None
    return rows[:start] + pairs + rows[index:]


def merge_price_continuations(rows):
    """Join a nearby numeric price line to a name-only line, never two products.

    Preserve OCR boxes/text. At most one numeric continuation is consumed, and
    the continuation must contain at least two actual prices (not just a SKU).
    """
    from .receipt_sku_rows import merge_sku_rows
    rows = merge_sku_rows(rows)
    leading = merge_price_leading_rows(rows)
    if leading is not None:
        return leading
    merged = []
    index = 0
    while index < len(rows):
        row = rows[index]
        text = ' '.join(item['text'] for item in row)
        if index + 1 < len(rows):
            following = rows[index + 1]
            next_text = ' '.join(item['text'] for item in following)
            stripped = strip_receipt_identifiers(text)
            numeric = is_price_continuation(following)
            name_only = (bool(re.search(r'[가-힣A-Za-z]', stripped))
                         and not parse_prices(stripped)
                         and (not _is_noise_row(text, normalize_product_name(stripped))
                              or bool(re.fullmatch(r'[가-힣]', stripped))))
            bottom = max(_bounds(item)[3] for item in row)
            top = min(_bounds(item)[1] for item in following)
            height = statistics.median(_bounds(item)[3]-_bounds(item)[1] for item in row)
            if (numeric and name_only and len(parse_prices(next_text)) >= 2
                    # Slanted text polygons may overlap vertically even for
                    # separate baselines. Permit a small overlap, not a full row.
                    and -max(1, height)*.4 <= top-bottom <= max(1, height)*1.5):
                # Keep damaged SKU text in raw evidence, not product identity.
                following = [dict(item, receipt_barcode=True)
                             if is_internal_product_code(item['text']) or
                             (re.fullmatch(r'[0-9A-Za-z]{8,14}', item['text'].strip())
                              and sum(c.isdigit() for c in item['text']) >= 8)
                             else item for item in following]
                row = row + following
                index += 1
        merged.append(row)
        index += 1
    return merged


def is_tax_total_row(text):
    """A tax label plus one total ends the product table, despite punctuation noise."""
    amounts = parse_prices(text)
    if len(amounts) != 1:
        return False
    key = re.sub(r'[^가-힣0-9]', '', text)
    return bool(re.fullmatch(r'(?:면세|과세)물품(?:가액|금액)?\d{2,10}', key))


def is_purchase_total_row(text):
    """Explicit quantity/amount total in receipts without a product header."""
    match = re.fullmatch(r'\s*총\s*구\s*매\s*액\s+(\d{1,3})\s+(\d{1,3}(?:,\s*\d{3})+)\s*원?\s*', text)
    return bool(match and int(match.group(1)) > 0)


def analyze_ocr_items(
    items: list[dict[str, Any]],
    min_confidence: float = 0.35,
    match_threshold: float = 0.72,
) -> dict[str, Any]:
    started = time.perf_counter()
    cleaned = clean_ocr_items(items, min_confidence=min_confidence)
    rows = merge_price_continuations(group_product_rows(cleaned))
    total_quantities = quantity_total_evidence(rows)
    ingredient_catalog = get_ingredient_catalog()
    product_catalog = get_product_catalog()
    products: list[dict[str, Any]] = []

    # Only restrict the region when an explicit product-table header is present.
    # Otherwise cropped receipts must retain their original fallback behavior.
    headers = [i for i, row in enumerate(rows) if is_product_table_header(row)]
    table_start = headers[0] if headers else None
    columns = column_anchors(rows[table_start], rows[table_start+1:]) if table_start is not None else None

    for row_index, row in enumerate(rows):
        raw_text = " ".join(item["text"] for item in row)
        if re.match(r"^(?:총품목수량|결제대상금액)", re.sub(r"\s+", "", raw_text)):
            break
        if is_purchase_total_row(raw_text):
            break
        if table_start is not None:
            if row_index <= table_start:
                continue
            # Tax totals end the product table; do not classify card/footer rows.
            if is_tax_total_row(raw_text):
                break
        if any(i.get('receipt_adjustment') for i in row) or is_adjustment_row(raw_text):
            continue
        identity_text = ' '.join(item['text'] for item in row if not item.get('receipt_barcode'))
        product_name = extract_product_name(identity_text)
        normalized_name = normalize_product_name(product_name)
        if _is_noise_row(raw_text, normalized_name):
            continue
        product_match = _select_product_match(
            normalized_name,
            product_catalog=product_catalog,
            ingredient_catalog=ingredient_catalog,
            match_threshold=match_threshold,
        )
        if (len(re.sub(r'[^가-힣A-Za-z]', '', normalized_name)) == 1
                and normalized_name not in {e.name for e in ingredient_catalog.entries if len(e.name)==1}):
            product_match = dict(product_match, standard_food_name=None, classification='unknown',
                                 source='single_glyph_requires_review')
        else:
            product_match = apply_classification(product_name, product_match, ingredient_catalog)

        purchase_quantity = next((i['sku_count_verified'] for i in row if i.get('sku_count_verified')), None)
        if purchase_quantity is None:
            purchase_quantity = receipt_purchase_quantity(raw_text)
        quantity_source = 'VERIFIED_RECEIPT_COLUMNS' if purchase_quantity is not None else None
        if purchase_quantity is None and row_index in total_quantities:
            purchase_quantity = total_quantities[row_index]['count']
            quantity_source = 'VERIFIED_RECEIPT_TOTALS'
        if purchase_quantity is None:
            purchase_quantity = reconstruct_missing_count(row, columns, items)
            if purchase_quantity is not None:
                quantity_source = 'RECONSTRUCTED_PRICE_COLUMNS'

        products.append(
            {
                "row_index": row_index,
                "raw_text": raw_text,
                "product_name": product_name,
                "normalized_name": normalized_name,
                "average_confidence": round(
                    sum(item["confidence"] for item in row) / len(row), 4
                ),
                "box": _row_box(row),
                "prices": parse_prices(raw_text),
                "purchase_quantity": purchase_quantity,
                "purchase_quantity_source": quantity_source,
                "match": product_match,
                "items": row,
            }
        )

    logging.getLogger("uvicorn.error.ocr_pipeline").info(
        "OCR postprocessing seconds=%.3f products=%d sources=%s",
        time.perf_counter() - started, len(products), dict(Counter(p["match"].get("source") for p in products)))
    return {
        "ocr_count": len(items),
        "cleaned_count": len(cleaned),
        "row_count": len(rows),
        "product_count": len(products),
        "products": products,
    }
