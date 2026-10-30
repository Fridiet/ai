"""Read-only detector comparison on three previously supplied demo images."""
import json
import os
import time
from pathlib import Path
import cv2
import numpy as np
from fastapi.encoders import jsonable_encoder
from app.ocr import get_engine, _as_list
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items, build_review_candidates

ROOT = Path(__file__).resolve().parents[1]


def main():
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    engine = get_engine()
    folder = Path('C:/Users/your-user/OneDrive/바탕 화면/캡스톤/영수증사진')
    reports = []
    for filename in ['스크린샷 2026-07-17 212620.png',
        'news-p.v1.20230216.4dde0d0e8a2d440595d9e9647501eb5d_Z1.jpg', '3398113_3495546_3424.jpg']:
        image = cv2.imdecode(np.frombuffer((folder/filename).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        for side in [0, 960]:
            started = time.perf_counter()
            options = {'text_det_limit_side_len': side, 'text_det_limit_type': 'max'} if side else {}
            predictions = list(engine.predict(image, **options))
            items = []
            for prediction in predictions:
                payload = prediction.json
                data = payload.get('res', payload)
                for text, score, polygon in zip(_as_list(data.get('rec_texts')), _as_list(data.get('rec_scores')), _as_list(data.get('rec_polys'))):
                    items.append({'text': str(text), 'confidence': float(score), 'box': [[int(x), int(y)] for x, y in polygon]})
            analysis = analyze_ocr_items(items)
            receipt = build_receipt_items(build_inventory_candidates(analysis))
            review = build_review_candidates(analysis)
            record = {'filename': filename, 'max_side': side, 'seconds': time.perf_counter()-started,
                      'items': receipt, 'review': review}
            reports.append(record)
            print(json.dumps({'filename': filename, 'max_side': side, 'seconds': record['seconds'],
                              'names': [r['name'] for r in receipt], 'review_count': len(review)}), flush=True)
    (ROOT/'output/receipt_readiness/detector_examples.json').write_text(json.dumps(jsonable_encoder(reports), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
