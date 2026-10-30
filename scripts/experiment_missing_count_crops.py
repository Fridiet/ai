"""Development-only row crops for missed quantity detection; no API mutation."""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import cv2
import numpy as np
from app.ocr import get_engine, _as_list
from app.product_pipeline import analyze_ocr_items
from app.receipt_columns import bounds

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filenames', nargs='+')
    args = parser.parse_args()
    partition = json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    records = {r['filename']: r for r in partition['records']}
    if any(name not in records or records[name]['split'] != 'development' for name in args.filenames):
        raise ValueError('Only development images allowed')
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    engine = get_engine()
    results = []
    for filename in args.filenames:
        record = records[filename]
        source = Path(record['image_path']).read_bytes()
        if hashlib.sha256(source).hexdigest() != record['sha256']:
            raise ValueError('Source changed')
        image = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
        raw = json.loads((ROOT/'output/new_receipts/raw'/f"{record['sha256']}.json").read_text(encoding='utf-8'))['items']
        products = analyze_ocr_items(raw)['products']
        for index, product in enumerate(products):
            if product.get('purchase_quantity') is not None or not product.get('prices'):
                continue
            left, top, right, bottom = bounds(product)
            height = bottom-top
            if height <= 0 or right <= left:
                continue
            for margin in (0.2, 0.6):
                y1, y2 = max(0, int(top-height*margin)), min(image.shape[0], int(bottom+height*margin))
                crop = image[y1:y2, :].copy()
                started = time.perf_counter()
                predictions = list(engine.predict(crop, text_det_limit_side_len=1600, text_det_limit_type='max'))
                texts = []
                for prediction in predictions:
                    payload = prediction.json
                    data = payload.get('res', payload)
                    for text, score in zip(_as_list(data.get('rec_texts')), _as_list(data.get('rec_scores'))):
                        texts.append({'text': str(text), 'confidence': float(score)})
                result = dict(filename=filename, product_index=index, before=product['raw_text'],
                              margin=margin, crop_y=[y1,y2], seconds=time.perf_counter()-started, texts=texts)
                results.append(result)
                print(json.dumps(result, ensure_ascii=True), flush=True)
    output = ROOT/'output/new_receipts/missing_count_crops.json'
    output.write_text(json.dumps({'scope':'development_diagnostic_only', 'auto_promotion':False,
                                 'results':results}, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
