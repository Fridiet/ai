"""Diagnostic rereads of calibrated price/count cells, development-only."""
import hashlib
import argparse
import json
import os
import time
from pathlib import Path
import cv2
import numpy as np
from app.product_pipeline import analyze_ocr_items,clean_ocr_items,group_product_rows,merge_price_continuations,is_product_table_header
from app.receipt_columns import bounds,column_anchors
from app.receipt_count_fusion import compatible,supplement_product_counts

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--all-development',action='store_true')
    args=parser.parse_args()
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED','true')
    records={r['filename']:r for r in json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']}
    runs={r['filename']:r['items'] for r in json.loads((ROOT/'output/new_receipts/detector_sensitivity_all.json').read_text(encoding='utf-8'))['results'] if r['variant']=='default'}
    labels=json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']
    names=[r['filename'] for r in labels] if args.all_development else ['IMG00238.png','IMG00240.png']
    from paddleocr import TextRecognition
    recognizer=TextRecognition(model_name='korean_PP-OCRv5_mobile_rec',device='cpu')
    results=[]
    for filename in names:
        record=records[filename]
        if record['split']!='development': raise ValueError('Development only')
        source=Path(record['image_path']).read_bytes()
        if hashlib.sha256(source).hexdigest()!=record['sha256']: raise ValueError('Source changed')
        image=cv2.imdecode(np.frombuffer(source,np.uint8),cv2.IMREAD_COLOR)
        raw=json.loads((ROOT/'output/new_receipts/detector_experiment'/f'{Path(filename).stem}-max960.json').read_text(encoding='utf-8'))['items']
        rows=merge_price_continuations(group_product_rows(clean_ocr_items(raw)))
        headers=[i for i,r in enumerate(rows) if is_product_table_header(r)]
        anchors=column_anchors(rows[headers[0]],rows[headers[0]+1:]) if headers else None
        if anchors is None:
            results.append(dict(filename=filename,skipped='no_verified_column_geometry'))
            continue
        primary_raw=runs[filename] if filename in runs else json.loads((ROOT/'output/new_receipts/detector_experiment'/f'{Path(filename).stem}-default.json').read_text(encoding='utf-8'))['items']
        primary=analyze_ocr_items(primary_raw); secondary=analyze_ocr_items(raw)
        baseline=supplement_product_counts(primary,secondary)
        p,q,t=anchors
        zones=[('unit_price',p-(q-p)/2,(p+q)/2),('count',(p+q)/2,(q+t)/2),('total',(q+t)/2,t+(t-q)/2)]
        crops,metadata=[],[]
        for i,s in enumerate(secondary['products']):
            if not s.get('prices'):continue
            if not args.all_development and s.get('purchase_quantity_source')!='RECONSTRUCTED_PRICE_COLUMNS':continue
            matches=[a for a in baseline['products'] if a.get('purchase_quantity') is None
                     and compatible(a,dict(s,purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS'))]
            if not args.all_development and len(matches)!=1:continue
            _,top,_,bottom=bounds(s)
            for margin in [0,.15]:
                for field,left,right in zones:
                    y1=max(0,int(top-(bottom-top)*margin)); y2=min(image.shape[0],int(bottom+(bottom-top)*margin))
                    x1=max(0,int(left)); x2=min(image.shape[1],int(right))
                    crop=image[y1:y2,x1:x2].copy()
                    if not crop.size:raise ValueError('Empty calibrated crop')
                    crops.append(crop)
                    metadata.append(dict(row=i,field=field,margin=margin,box=[x1,y1,x2,y2],
                        primary=matches[0]['raw_text'] if len(matches)==1 else None,
                        secondary=s['raw_text'],existing_count=s.get('purchase_quantity')))
        started=time.perf_counter()
        predictions=list(recognizer.predict(crops)) if crops else []
        if len(predictions)!=len(metadata):raise RuntimeError('Output count mismatch')
        for meta,prediction in zip(metadata,predictions):
            payload=prediction.json; data=payload.get('res',payload)
            meta.update(text=data['rec_text'],confidence=float(data['rec_score']))
        result=dict(filename=filename,image_sha256=record['sha256'],anchors=anchors,seconds=time.perf_counter()-started,candidates=metadata)
        results.append(result)
        print(json.dumps(dict(filename=filename,crop_count=len(crops),seconds=result['seconds'])),flush=True)
    output='calibrated_numeric_all_development.json' if args.all_development else 'calibrated_numeric_crops.json'
    (ROOT/'output/new_receipts'/output).write_text(json.dumps(dict(
        scope='development_diagnostic_only',auto_promotion=False,results=results),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
