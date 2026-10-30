"""Compare max960 detection against historical raw snapshots; development only."""
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from app.ocr import get_engine, _as_list
from scripts.audit_receipt_readiness import audit_record, diagnostic_rates

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = {'text_det_limit_side_len': 960, 'text_det_limit_type': 'max'}


def main():
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    labels_path = ROOT/'output/local_receipt_review/assistant_annotations.json'
    labels = json.loads(labels_path.read_text(encoding='utf-8'))['records']
    labels = [r for r in labels if r.get('store_type') in {'grocery', 'convenience'}]
    output = ROOT/'output/receipt_readiness/detection_history'
    output.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    results = []
    totals = {'historical_default': Counter(), 'max960': Counter()}
    for i, label in enumerate(labels, 1):
        sha = label['image_sha256']
        baseline = json.loads((ROOT/'output/local_receipt_review/raw'/f'{sha}.json').read_text(encoding='utf-8'))['items']
        path = Path('C:/Users/your-user/Downloads/test/test/images')/label['filename']
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != sha:
            raise ValueError('Source changed: '+path.name)
        cache = output/f'{sha}-max960.json'
        if cache.exists():
            record = json.loads(cache.read_text(encoding='utf-8'))
            if record['sha256'] != sha or record['options'] != OPTIONS:
                raise ValueError('Cache identity mismatch')
        else:
            image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
            started = time.perf_counter()
            predictions = list(engine.predict(image, **OPTIONS))
            elapsed = time.perf_counter()-started
            items = []
            for prediction in predictions:
                payload = prediction.json
                data = payload.get('res', payload)
                for text, score, polygon in zip(_as_list(data.get('rec_texts')),
                        _as_list(data.get('rec_scores')), _as_list(data.get('rec_polys'))):
                    items.append({'text': str(text), 'confidence': float(score),
                                  'box': [[int(x), int(y)] for x, y in polygon]})
            record = {'sha256': sha, 'options': OPTIONS, 'items': items,
                      'inference_seconds': elapsed, 'detector': 'PP-OCRv5_server_det',
                      'recognizer': 'korean_PP-OCRv5_mobile_rec'}
            temp = cache.with_suffix('.tmp')
            temp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(cache)
        pair = {name: audit_record(label, items) for name, items in
                [('historical_default', baseline), ('max960', record['items'])]}
        for name, audit in pair.items():
            totals[name].update(audit['counts'])
        results.append({'filename': label['filename'], 'audit': pair,
                        'max960_inference_seconds': record['inference_seconds']})
        report = {'scope': 'historical_development_not_independent',
                  'baseline_timing_comparable': False, 'processed': i, 'total': len(labels),
                  'labels_sha256': hashlib.sha256(labels_path.read_bytes()).hexdigest(),
                  'counts': {k: dict(v) for k, v in totals.items()},
                  'rates': {k: diagnostic_rates(v) for k, v in totals.items()}, 'receipts': results}
        (output/'comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'progress': f'{i}/{len(labels)}', 'filename': label['filename'],
                          'counts': {k: v['counts'] for k, v in pair.items()}}, ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
