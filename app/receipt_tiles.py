"""Experimental overlapping OCR tiles; not enabled in the serving path."""
from .ocr import _as_list


def tile_ranges(height, core_height=900, overlap=100):
    if height <= 0 or core_height <= 0 or overlap < 0:
        raise ValueError('Invalid tile dimensions')
    for start in range(0, height, core_height):
        end = min(start + core_height, height)
        yield start, end, max(0, start-overlap), min(height, end+overlap)


def predict_tiles(engine, image, core_height=900, overlap=100):
    """Retain only each tile's core centers, preserving separate identical rows."""
    height = image.shape[0]
    items = []
    for start, end, top, bottom in tile_ranges(height, core_height, overlap):
        crop = image[top:bottom].copy()
        for prediction in engine.predict(crop, text_det_limit_side_len=960, text_det_limit_type='max'):
            payload = prediction.json
            data = payload.get('res', payload)
            for text, score, polygon in zip(_as_list(data.get('rec_texts')),
                    _as_list(data.get('rec_scores')), _as_list(data.get('rec_polys'))):
                box = [[int(x), int(y)+top] for x, y in polygon]
                center = (min(p[1] for p in box) + max(p[1] for p in box))/2
                if start <= center < end:
                    items.append({'text': str(text), 'confidence': float(score), 'box': box})
    return items
