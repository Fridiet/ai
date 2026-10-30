"""Development-only header crop experiment; never modifies runtime defaults."""
import argparse
import hashlib
import json
import time
import os
import re
from pathlib import Path
import cv2
import numpy as np
from app.ocr import run_ocr
from app.product_pipeline import clean_ocr_items,group_product_rows,is_product_table_header,analyze_ocr_items
from app.receipt_count_fusion import needs_secondary_count_recovery,supplement_product_counts
from app.receipt_numeric_cells import supplement_numeric_cells
from app.receipt_columns import bounds,column_anchors
from app.receipt_header_recovery import supplement_header_labels
from scripts.audit_receipt_readiness import audit_record

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--filenames',nargs='+',default=['IMG00218.png'])
    parser.add_argument('--all-eligible',action='store_true')
    args=parser.parse_args()
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED','true')
    entries={r['filename']:r for r in json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']}
    results=[]
    labels={r['filename']:r for r in json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']}
    primary_cache={r['filename']:r['items'] for r in json.loads((ROOT/'output/new_receipts/detector_sensitivity_all.json').read_text(encoding='utf-8'))['results'] if r['variant']=='default'}
    numeric={r['filename']:r.get('candidates',[]) for r in json.loads((ROOT/'output/new_receipts/calibrated_numeric_all_development.json').read_text(encoding='utf-8'))['results']}
    for filename in (labels if args.all_eligible else args.filenames):
        entry=entries[filename]
        if entry['split']!='development':raise ValueError('Development images only')
        raw=primary_cache.get(filename)
        if raw is None:
            raw=json.loads((ROOT/'output/new_receipts/detector_experiment'/f'{Path(filename).stem}-default.json').read_text(encoding='utf-8'))['items']
        rows=group_product_rows(clean_ocr_items(raw))
        headers=[r for r in rows if is_product_table_header(r)]
        if not headers:
            results.append(dict(filename=filename,skipped='no_header_seed'))
            continue
        header=headers[0]
        fields={re.sub(r'\s+','',i['text']) for i in header}&{'단가','수량','금액'}
        if args.all_eligible and (len(headers)!=1 or len(fields)!=2 or column_anchors(header) is not None):
            results.append(dict(filename=filename,skipped='not_missing_single_column'))
            continue
        payload=Path(entry['image_path']).read_bytes()
        if hashlib.sha256(payload).hexdigest()!=entry['sha256']:raise ValueError('Image changed')
        image=cv2.imdecode(np.frombuffer(payload,np.uint8),cv2.IMREAD_COLOR)
        top=min(bounds(i)[1] for i in header);bottom=max(bounds(i)[3] for i in header)
        height=bottom-top
        for margin in ([.25] if args.all_eligible else [.25,.6]):
            y1=max(0,int(top-height*margin));y2=min(image.shape[0],int(bottom+height*margin))
            started=time.perf_counter()
            reread=run_ocr(image[y1:y2].copy())
            for item in reread:
                item['box']=[[x,y+y1] for x,y in item['box']]
            new_rows=group_product_rows(clean_ocr_items(reread))
            new_headers=[r for r in new_rows if is_product_table_header(r)]
            anchors=column_anchors(new_headers[0]) if len(new_headers)==1 else None
            record=dict(filename=filename,image_sha256=entry['sha256'],margin=margin,
                crop=[0,y1,image.shape[1],y2],seconds=time.perf_counter()-started,
                items=reread,anchors=anchors)
            combined=supplement_header_labels(raw,reread)
            record['added_labels']=len(combined)-len(raw)
            if filename in labels:
                record['baseline_counts']=audit_record(labels[filename],raw)['counts']
                record['reread_counts']=audit_record(labels[filename],combined)['counts']
                secondary_raw=json.loads((ROOT/'output/new_receipts/detector_experiment'/f'{Path(filename).stem}-max960.json').read_text(encoding='utf-8'))['items']
                secondary=analyze_ocr_items(secondary_raw)
                def full(data):
                    analysis=analyze_ocr_items(data)
                    if needs_secondary_count_recovery(analysis):
                        analysis=supplement_product_counts(analysis,secondary)
                        analysis=supplement_numeric_cells(analysis,secondary,numeric.get(filename,[]))
                    return audit_record(labels[filename],data,analysis=analysis)['counts']
                record['baseline_full_counts']=full(raw)
                record['reread_full_counts']=full(combined)
            results.append(record)
            print(json.dumps({k:v for k,v in record.items() if k!='items'}|{
                'texts':[(i['text'],round(i['confidence'],3)) for i in reread]},ensure_ascii=True),flush=True)
    output='header_recovery_all_eligible.json' if args.all_eligible else 'header_recovery_experiment.json'
    (ROOT/'output/new_receipts'/output).write_text(json.dumps(
        dict(scope='development_diagnostic_not_production',results=results),ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
