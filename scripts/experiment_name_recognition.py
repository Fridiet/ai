"""Batch re-read unresolved product-name crops; diagnostic only, no auto promotion."""
import hashlib
import json
import os
import re
import time
import argparse
from pathlib import Path
import cv2
import numpy as np
from app.product_pipeline import analyze_ocr_items
from app.receipt_columns import bounds

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all-annotated', action='store_true')
    parser.add_argument('--primary-profile', choices=['default','max960'], default='max960')
    parser.add_argument('--include-partial', action='store_true')
    args = parser.parse_args()
    names = {'IMG00014.png','IMG00023.png','IMG00238.png'}
    if args.all_annotated:
        names = {r['filename'] for r in json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']}
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    from paddleocr import TextRecognition
    recognizer = TextRecognition(model_name='korean_PP-OCRv5_mobile_rec', device='cpu')
    partition = json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    summaries = []
    for record in partition['records']:
        if record['filename'] not in names:
            continue
        if record['split'] != 'development':
            raise ValueError('Frozen image forbidden')
        source = Path(record['image_path']).read_bytes()
        if hashlib.sha256(source).hexdigest() != record['sha256']:
            raise ValueError('Image changed')
        image = cv2.imdecode(np.frombuffer(source, dtype=np.uint8), cv2.IMREAD_COLOR)
        cached = ROOT/'output/new_receipts/detector_experiment'/f"{Path(record['filename']).stem}-{args.primary_profile}.json"
        payload = json.loads(cached.read_text(encoding='utf-8'))
        if payload['filename'] != record['filename'] or payload['profile'] != args.primary_profile:
            raise ValueError('Mismatched OCR cache')
        items = payload['items']
        analysis = analyze_ocr_items(items)
        unresolved = [p for p in analysis['products'] if p['match']['classification'] == 'unknown'
            or (args.include_partial and p['match'].get('match_type') in {'contains','ml_classifier'})][:8]
        selected = []
        for index, item in enumerate(items):
            if len(re.findall('[가-힣]', item['text'])) < 2:
                continue
            x1,y1,x2,y2 = bounds(item)
            for product in unresolved:
                px1,py1,px2,py2 = bounds(product)
                if px1 <= (x1+x2)/2 <= px2 and py1 <= (y1+y2)/2 <= py2:
                    selected.append((index, item))
                    break
        crops, metadata = [], []
        for index, item in selected:
            x1,y1,x2,y2 = bounds(item)
            for padding in (0, 4):
                crop = image[max(0,y1-padding):min(image.shape[0],y2+padding),
                             max(0,x1-padding):min(image.shape[1],x2+padding)].copy()
                if crop.size:
                    crops.append(crop)
                    metadata.append({'index':index, 'before':item['text'], 'padding':padding})
        started = time.perf_counter()
        predictions = list(recognizer.predict(crops)) if crops else []
        if len(predictions) != len(metadata):
            raise RuntimeError('Recognition result count mismatch')
        for meta, prediction in zip(metadata, predictions):
            payload = prediction.json
            data = payload.get('res', payload)
            meta.update(after=data['rec_text'], score=float(data['rec_score']))
        result = {'filename':record['filename'], 'sha256':record['sha256'],
                  'raw_cache_sha256':hashlib.sha256(cached.read_bytes()).hexdigest(),
                  'seconds':time.perf_counter()-started,
                  'crop_count':len(crops), 'candidates':metadata}
        summaries.append(result)
        print(json.dumps(result, ensure_ascii=True), flush=True)
    suffix = f'-{args.primary_profile}-all' if args.all_annotated else ''
    suffix += '-partial' if args.include_partial else ''
    (ROOT/f'output/new_receipts/name_recognition_experiment{suffix}.json').write_text(json.dumps(
        {'scope':'development_diagnostic_only','primary_profile':args.primary_profile,
         'auto_promotion':False,'results':summaries},
        ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    main()
