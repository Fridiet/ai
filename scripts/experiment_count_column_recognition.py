"""Read exact quantity-header column crops, development diagnostic only."""
import argparse
import hashlib
import json
import os
import re
import time
from pathlib import Path
import cv2
import numpy as np
from app.product_pipeline import analyze_ocr_items
from app.receipt_columns import bounds

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filenames', nargs='+')
    args = parser.parse_args()
    manifest = json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    records = {r['filename']: r for r in manifest['records']}
    if any(n not in records or records[n]['split'] != 'development' for n in args.filenames):
        raise ValueError('Development only')
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    from paddleocr import TextRecognition
    recognizer = TextRecognition(model_name='korean_PP-OCRv5_mobile_rec', device='cpu')
    results = []
    for filename in args.filenames:
        record = records[filename]
        raw = json.loads((ROOT/'output/new_receipts/raw'/f"{record['sha256']}.json").read_text(encoding='utf-8'))['items']
        headers = [i for i in raw if re.sub(r'\s+', '', i['text']) == '수량' and i['confidence'] >= .9]
        if len(headers) != 1:
            results.append(dict(filename=filename, skipped='no_unique_exact_quantity_header'))
            continue
        source = Path(record['image_path']).read_bytes()
        if hashlib.sha256(source).hexdigest() != record['sha256']:
            raise ValueError('Source changed')
        image = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
        hx1, hy1, hx2, hy2 = bounds(headers[0])
        crops, metadata = [], []
        for index, product in enumerate(analyze_ocr_items(raw)['products']):
            if product.get('purchase_quantity') is not None or not product.get('prices'):
                continue
            _, top, _, bottom = bounds(product)
            if top <= hy2:
                continue
            # Header width only, no guessed column positions or neighboring rows.
            crop = image[max(0, int(top)):min(image.shape[0], int(bottom)),
                         max(0, int(hx1)):min(image.shape[1], int(hx2))].copy()
            if not crop.size:
                continue
            crops.append(crop)
            metadata.append(dict(product_index=index, before=product['raw_text'],
                                 crop_box=[hx1,top,hx2,bottom]))
        started = time.perf_counter()
        predictions = list(recognizer.predict(crops)) if crops else []
        for meta, prediction in zip(metadata, predictions):
            payload = prediction.json
            data = payload.get('res', payload)
            meta.update(text=data['rec_text'], confidence=float(data['rec_score']))
        results.append(dict(filename=filename, seconds=time.perf_counter()-started, candidates=metadata))
    report = dict(scope='development_diagnostic_only', auto_promotion=False, results=results)
    (ROOT/'output/new_receipts/count_column_recognition.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
