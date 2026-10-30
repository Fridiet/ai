"""List name recovery proposals from development caches, without applying them."""
import json
import os
from pathlib import Path
from app.product_pipeline import analyze_ocr_items
from app.receipt_name_recovery import propose_names

ROOT=Path(__file__).resolve().parents[1]


def main():
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED','true')
    from app.ocr_classification import warmup
    warmup()
    partition=json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']
    lookup={r['filename']:r for r in partition}
    labels=json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']
    old=json.loads((ROOT/'output/local_receipt_review/assistant_annotations.json').read_text(encoding='utf-8'))['records']
    records=[]
    for scope,selected in [('new',labels),('historical',[r for r in old if r.get('store_type') in {'grocery','convenience'}])]:
        for r in selected:
            if scope=='new':
                entry=lookup[r['filename']]
                if entry['split']!='development': raise ValueError('Frozen images forbidden')
                first=ROOT/'output/new_receipts/raw'/f"{entry['sha256']}.json"
                second=ROOT/'output/new_receipts/detector_experiment'/f"{Path(r['filename']).stem}-max960.json"
            else:
                first=ROOT/'output/local_receipt_review/raw'/f"{r['image_sha256']}.json"
                second=ROOT/'output/receipt_readiness/detection_history'/f"{r['image_sha256']}-max960.json"
            a=analyze_ocr_items(json.loads(first.read_text(encoding='utf-8'))['items'])
            b=analyze_ocr_items(json.loads(second.read_text(encoding='utf-8'))['items'])
            proposals=propose_names(a,b)
            records.append({'scope':scope,'filename':r['filename'],'proposals':proposals})
            if proposals: print(json.dumps(records[-1],ensure_ascii=True),flush=True)
    report={'scope':'development_diagnostic_not_independent_accuracy','applied_to_runtime':False,
            'records':records,'receipt_count':len(records),'proposal_count':sum(len(r['proposals']) for r in records)}
    (ROOT/'output/receipt_readiness/name_recovery_proposals.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'receipt_count':report['receipt_count'],'proposal_count':report['proposal_count']}),flush=True)


if __name__=='__main__': main()
