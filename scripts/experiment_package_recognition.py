"""Re-read package-bearing development crops without promoting guessed units."""
import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path

import cv2
import numpy as np
from app.receipt_columns import bounds
from app.product_pipeline import analyze_ocr_items

ROOT = Path(__file__).resolve().parents[1]


def package_candidate(text):
    # Include digit-only suffixes: g/9 and l/1 damage must not be invisible to
    # the experiment. Never interpret these suffixes as actual package sizes.
    return bool(re.search('[가-힣]', text) and re.search(r'\d', text))


def select_product_crops(items, products):
    regions = [bounds(p) for p in products if p.get('prices')]
    selected = []
    for index, item in enumerate(items):
        if not package_candidate(item['text']):
            continue
        x1,y1,x2,y2 = bounds(item)
        cx,cy = (x1+x2)/2, (y1+y2)/2
        if any(left <= cx <= right and top <= cy <= bottom
               for left,top,right,bottom in regions):
            selected.append((index, item))
    return selected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filenames', nargs='*')
    parser.add_argument('--all-development', action='store_true')
    args = parser.parse_args()
    records = {r['filename']: r for r in json.loads(
        (ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']}
    if args.all_development:
        if args.filenames:
            raise ValueError('Choose filenames or --all-development')
        args.filenames = [r['filename'] for r in json.loads(
            (ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']]
    if not args.filenames:
        raise ValueError('No images selected')
    if any(name not in records or records[name]['split'] != 'development' for name in args.filenames):
        raise ValueError('Only development images allowed')
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from paddleocr import TextRecognition
    started = time.perf_counter()
    recognizer = TextRecognition(model_name='korean_PP-OCRv5_mobile_rec', device='cpu')
    load_seconds = time.perf_counter()-started
    results = []
    for filename in args.filenames:
        record = records[filename]
        source = Path(record['image_path']).read_bytes()
        if hashlib.sha256(source).hexdigest() != record['sha256']:
            raise ValueError('Image changed')
        image = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
        cached = ROOT/'output/new_receipts/detector_experiment'/f'{Path(filename).stem}-default.json'
        if not cached.exists():
            cached = ROOT/'output/new_receipts/raw'/f"{record['sha256']}.json"
        items = json.loads(cached.read_text(encoding='utf-8'))['items']
        crops, metadata = [], []
        products = analyze_ocr_items(items)['products']
        for index, item in select_product_crops(items, products):
            x1,y1,x2,y2 = bounds(item)
            for padding in (0, 3):
                crop = image[max(0,y1-padding):min(image.shape[0],y2+padding),
                             max(0,x1-padding):min(image.shape[1],x2+padding)].copy()
                if crop.size:
                    crops.append(crop)
                    metadata.append(dict(index=index, before=item['text'], padding=padding))
        started = time.perf_counter()
        predictions = list(recognizer.predict(crops)) if crops else []
        if len(predictions) != len(metadata):
            raise RuntimeError('Recognition result count mismatch')
        for meta, prediction in zip(metadata, predictions):
            payload = prediction.json
            data = payload.get('res', payload)
            meta.update(after=data['rec_text'], score=float(data['rec_score']))
        result = dict(filename=filename, sha256=record['sha256'],
                      raw_cache=str(cached.relative_to(ROOT)),
                      raw_cache_sha256=hashlib.sha256(cached.read_bytes()).hexdigest(),
                      seconds=time.perf_counter()-started, candidates=metadata)
        results.append(result)
        print(json.dumps(dict(filename=filename, seconds=result['seconds'],
                              crop_count=len(metadata)), ensure_ascii=True), flush=True)
    output = ROOT/'output/new_receipts'/('package_recognition_all_development.json'
        if args.all_development else 'package_recognition_experiment.json')
    output.write_text(json.dumps(dict(scope='development_diagnostic_only', auto_promotion=False,
        load_seconds=load_seconds, results=results), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
