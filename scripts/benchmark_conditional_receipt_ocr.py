"""Local staged OCR experiment, not an HTTP benchmark or production feature."""
import argparse
import hashlib
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
from app.receipt_mapper import build_receipt_items
from app.receipt_count_fusion import needs_secondary_count_recovery, supplement_product_counts
from scripts.benchmark_receipt_api import percentile

ROOT = Path(__file__).resolve().parents[1]


def recognize(engine, image, secondary=False):
    options = {'text_det_limit_side_len': 960, 'text_det_limit_type': 'max'} if secondary else {}
    items = []
    for prediction in engine.predict(image, **options):
        payload = prediction.json
        data = payload.get('res', payload)
        for text, score, polygon in zip(_as_list(data.get('rec_texts')), _as_list(data.get('rec_scores')), _as_list(data.get('rec_polys'))):
            items.append({'text': str(text), 'confidence': float(score),
                          'box': [[int(x), int(y)] for x, y in polygon]})
    return items


def summarize(records):
    repeats = [r for r in records if r['iteration'] > 0]
    return {'repeat_count': len(repeats),
            'repeat_base_p50_seconds': percentile([r['base_seconds'] for r in repeats], .5),
            'repeat_total_p50_seconds': percentile([r['total_seconds'] for r in repeats], .5),
            'repeat_total_p95_seconds': percentile([r['total_seconds'] for r in repeats], .95),
            'repeat_secondary_count': sum(r['secondary_triggered'] for r in repeats),
            'primary_identity_preserved': all(r['primary_identity_preserved'] for r in records),
            'existing_counts_preserved': all(r['existing_counts_preserved'] for r in records)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 10:
        raise ValueError('repeats must be 1..10')
    manifest = json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    selected = []
    for entry in manifest['records']:
        if entry['filename'] in {'IMG00014.png', 'IMG00051.png', 'IMG00287.png'}:
            if entry['split'] != 'development':
                raise ValueError('Frozen evaluation excluded')
            source = Path(entry['image_path']).read_bytes()
            if hashlib.sha256(source).hexdigest() != entry['sha256']:
                raise ValueError('Source image changed')
            selected.append((entry['filename'], source))
    if len(selected) != 3:
        raise ValueError('Missing development images')
    folder = Path('C:/Users/your-user/OneDrive/바탕 화면/캡스톤/영수증사진')
    for name in ['스크린샷 2026-07-17 212620.png',
                 'news-p.v1.20230216.4dde0d0e8a2d440595d9e9647501eb5d_Z1.jpg', '3398113_3495546_3424.jpg']:
        selected.append((name, (folder/name).read_bytes()))
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    engine = get_engine()
    output = ROOT/'output/receipt_readiness/conditional_ocr_benchmark.json'
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for iteration in range(args.repeats+1):
        for name, source in selected:
            started = time.perf_counter()
            image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError('Invalid image')
            primary = analyze_ocr_items(recognize(engine, image))
            before = build_receipt_items(build_inventory_candidates(primary))
            base_done = time.perf_counter()
            trigger = bool(needs_secondary_count_recovery(primary))
            fused = supplement_product_counts(primary, analyze_ocr_items(recognize(engine, image, True))) if trigger else primary
            after = build_receipt_items(build_inventory_candidates(fused))
            finished = time.perf_counter()
            record = {'filename': name, 'image_sha256': hashlib.sha256(source).hexdigest(),
                      'iteration': iteration, 'secondary_triggered': trigger,
                      'base_seconds': base_done-started, 'additional_seconds': finished-base_done,
                      'total_seconds': finished-started,
                      'primary_identity_preserved': [p['match'] for p in primary['products']] == [p['match'] for p in fused['products']],
                      'existing_counts_preserved': all(a.get('purchase_quantity') is None or a['purchase_quantity']==b['purchase_quantity'] for a,b in zip(primary['products'], fused['products'])),
                      'recovered_counts': [{'raw_text': b['raw_text'], 'quantity': b['purchase_quantity'],
                                            'secondary_evidence': b['secondary_quantity_evidence']}
                                           for a,b in zip(primary['products'], fused['products'])
                                           if a.get('purchase_quantity') is None and b.get('purchase_quantity') is not None],
                      'before': before, 'after': after}
            records.append(record)
            report = {'scope': 'local_serial_development_and_demo_not_independent_accuracy',
                      'http_measured': False, 'model_load_excluded': True, 'production_changed': False,
                      'summary': summarize(records), 'records': records}
            temp = output.with_suffix('.tmp')
            temp.write_text(json.dumps(jsonable_encoder(report), ensure_ascii=False, indent=2), encoding='utf-8')
            temp.replace(output)
            print(json.dumps({k:record[k] for k in ['filename','iteration','secondary_triggered','base_seconds','additional_seconds','total_seconds']} | {'recovered_count':len(record['recovered_counts'])}), flush=True)


if __name__ == '__main__':
    main()
