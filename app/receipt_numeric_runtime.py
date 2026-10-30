"""Bounded numeric rereads; no additional full-image detection pass."""
from .receipt_columns import bounds, column_anchors
from .receipt_count_fusion import compatible
from .product_pipeline import (clean_ocr_items, group_product_rows,
    merge_price_continuations, is_product_table_header)
from .receipt_numeric_cells import supplement_numeric_cells


def prepare_numeric_crops(image, primary, secondary, raw_items, max_rows=4):
    if not 1 <= max_rows <= 8:
        raise ValueError('numeric crop row limit must be between 1 and 8')
    rows = merge_price_continuations(group_product_rows(clean_ocr_items(raw_items)))
    headers = [i for i, row in enumerate(rows) if is_product_table_header(row)]
    anchors = column_anchors(rows[headers[0]], rows[headers[0]+1:]) if headers else None
    if anchors is None:
        return [], []
    p, q, t = anchors
    zones = [('unit_price', p-(q-p)/2, (p+q)/2),
             ('count', (p+q)/2, (q+t)/2), ('total', (q+t)/2, t+(t-q)/2)]
    crops, metadata = [], []
    for index, product in enumerate(secondary['products']):
        prices = product.get('prices', [])
        if len(prices) < 2 or prices[-2] <= 0:
            continue
        count = prices[-1] / prices[-2]
        evidence = dict(product, purchase_quantity=count,
                        purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS')
        if not compatible(product, evidence):
            continue
        matches = [x for x in primary['products'] if compatible(x, evidence)]
        if len(matches) != 1 or matches[0].get('purchase_quantity') is not None:
            continue
        if not matches[0].get('match', {}).get('standard_food_name'):
            continue
        _, top, _, bottom = bounds(product)
        row_crops, row_metadata = [], []
        for field, left, right in zones:
            x1, x2 = max(0, int(left)), min(image.shape[1], int(right))
            y1, y2 = max(0, int(top)), min(image.shape[0], int(bottom))
            if x2 <= x1 or y2 <= y1:
                break
            row_crops.append(image[y1:y2, x1:x2].copy())
            row_metadata.append(dict(row=index, field=field, margin=0,
                                     secondary=product['raw_text']))
        if len(row_crops) == 3:
            crops.extend(row_crops)
            metadata.extend(row_metadata)
        if len(crops) >= max_rows * 3:
            break
    return crops, metadata


def recover_numeric_counts(image, primary, secondary, raw_items):
    from .ocr import recognize_numeric_crops
    crops, metadata = prepare_numeric_crops(image, primary, secondary, raw_items)
    if not crops:
        return primary
    readings = recognize_numeric_crops(crops)
    if len(readings) != len(metadata):
        raise ValueError('Numeric recognition result count mismatch')
    cells = [dict(meta, **reading) for meta, reading in zip(metadata, readings)]
    return supplement_numeric_cells(primary, secondary, cells)
