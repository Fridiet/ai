"""Local development-only detector comparison. Never changes production defaults."""
import json
import argparse
import os
import time
from pathlib import Path

import cv2
import numpy as np

from app.ocr import get_engine, _as_list
from app.receipt_tiles import predict_tiles
from scripts.audit_receipt_readiness import audit_record

ROOT = Path(__file__).resolve().parents[1]


def main():
    profiles = {'default': {}, 'sensitive': {'text_det_thresh': .2, 'text_det_box_thresh': .4},
                'max1600': {'text_det_limit_side_len': 1600, 'text_det_limit_type': 'max'},
                'max2400': {'text_det_limit_side_len': 2400, 'text_det_limit_type': 'max'},
                'max3200': {'text_det_limit_side_len': 3200, 'text_det_limit_type': 'max'},
                'max960': {'text_det_limit_side_len': 960, 'text_det_limit_type': 'max'},
                'tiles900': {'core_height': 900, 'overlap': 100}}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', nargs='+', choices=profiles, default=['default', 'sensitive'])
    parser.add_argument('--all-annotated', action='store_true')
    parser.add_argument('--filenames', nargs='+')
    args = parser.parse_args()
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    partition = json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    records = {r['filename']: r for r in partition['records']}
    labels = json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']
    if args.filenames:
        if set(args.filenames) - {r['filename'] for r in labels}:
            raise ValueError('Only annotated development filenames are allowed')
        labels = [r for r in labels if r['filename'] in args.filenames]
    elif not args.all_annotated:
        labels = [r for r in labels if r['filename'] in {'IMG00014.png', 'IMG00023.png', 'IMG00238.png'}]
    engine = get_engine()
    output = ROOT/'output/new_receipts/detector_experiment'
    output.mkdir(parents=True, exist_ok=True)
    summaries = []
    for label in labels:
        record = records[label['filename']]
        if record['split'] != 'development':
            raise ValueError('Frozen evaluation image is forbidden')
        source = Path(record['image_path']).read_bytes()
        import hashlib
        if hashlib.sha256(source).hexdigest() != record['sha256']:
            raise ValueError('Source image changed')
        image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
        for profile in args.profiles:
            options = profiles[profile]
            started = time.perf_counter()
            tiled = predict_tiles(engine, image, **options) if profile == 'tiles900' else None
            results = [] if tiled is not None else list(engine.predict(image, **options))
            elapsed = time.perf_counter() - started
            items = tiled if tiled is not None else []
            for result in results:
                payload = result.json
                data = payload.get('res', payload)
                for text, score, polygon in zip(_as_list(data.get('rec_texts')),
                        _as_list(data.get('rec_scores')), _as_list(data.get('rec_polys'))):
                    items.append({'text': str(text), 'confidence': float(score),
                                  'box': [[int(x), int(y)] for x, y in polygon]})
            audit = audit_record(label, items)
            entry = {'filename': label['filename'], 'profile': profile, 'options': options,
                     'inference_seconds': elapsed, 'ocr_boxes': len(items), 'audit': audit}
            (output/f"{Path(label['filename']).stem}-{profile}.json").write_text(
                json.dumps(dict(entry, items=items), ensure_ascii=False, indent=2), encoding='utf-8')
            summaries.append(entry)
            print(json.dumps({k: v for k, v in entry.items() if k != 'audit'} |
                             {'counts': audit['counts']}, ensure_ascii=True), flush=True)
    suffix = '-subset-'+'-'.join(Path(n).stem for n in args.filenames) if args.filenames else ''
    (output/('summary-'+'-'.join(args.profiles)+suffix+'.json')).write_text(json.dumps({'scope': 'development_not_independent',
        'production_defaults_changed': False, 'results': summaries}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
