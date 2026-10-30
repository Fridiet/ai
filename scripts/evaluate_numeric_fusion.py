"""Replay existing development caches; no OCR calls, no production change."""
import json
import os
import argparse
from pathlib import Path
from collections import Counter
from app.product_pipeline import clean_ocr_items, group_product_rows, analyze_ocr_items
from app.receipt_count_fusion import supplement_product_counts, needs_secondary_count_recovery
from app.receipt_numeric_fusion import supplement_numbers
from scripts.audit_receipt_readiness import audit_record

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', action='store_true')
    parser.add_argument('--new-development', action='store_true')
    parser.add_argument('--gated', action='store_true', help='Run secondary analysis only for named food with missing count and prices')
    args=parser.parse_args()
    if args.gated and not args.rows:
        parser.error('--gated requires --rows')
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED','true')
    from app.ocr_classification import warmup
    warmup()
    labels=json.loads((ROOT/'output/local_receipt_review/assistant_annotations.json').read_text(encoding='utf-8'))['records']
    labels=[r for r in labels if r.get('store_type') in {'grocery','convenience'}]
    if args.new_development:
        labels=json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']
        partition=json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']
        manifest={r['filename']:r for r in partition}
    totals=Counter()
    results=[]
    for label in labels:
        if args.new_development:
            entry=manifest[label['filename']]
            if entry['split']!='development':
                raise ValueError('Frozen images forbidden')
            first=ROOT/'output/new_receipts/raw'/f"{entry['sha256']}.json"
            second=ROOT/'output/new_receipts/detector_experiment'/f"{Path(label['filename']).stem}-max960.json"
        else:
            sha=label['image_sha256']
            first=ROOT/'output/local_receipt_review/raw'/f'{sha}.json'
            second=ROOT/'output/receipt_readiness/detection_history'/f'{sha}-max960.json'
        primary=json.loads(first.read_text(encoding='utf-8'))['items']
        secondary=json.loads(second.read_text(encoding='utf-8'))['items']
        combined=supplement_numbers(primary,secondary,group_product_rows(clean_ocr_items(primary)))
        if args.rows:
            first_analysis = analyze_ocr_items(primary)
            triggered = not args.gated or needs_secondary_count_recovery(first_analysis)
            fused=supplement_product_counts(first_analysis,analyze_ocr_items(secondary)) if triggered else first_analysis
            audit=audit_record(label,primary,analysis=fused)
            audit['secondary_triggered'] = bool(triggered)
            audit['supplemented_counts']=sum(p.get('purchase_quantity_source') == 'SECONDARY_VERIFIED_RECEIPT_COLUMNS' for p in fused['products'])
        else:
            audit=audit_record(label,combined)
        audit['supplemented_boxes']=len(combined)-len(primary)
        results.append(audit)
        totals.update(audit['counts'])
    report={'scope':'development_cached_row_count_fusion' if args.rows else 'development_cached_numeric_fusion','counts':dict(totals),'receipts':results}
    filename='count_fusion.json' if args.rows else 'numeric_fusion.json'
    if args.new_development:
        filename='new_'+filename
    if args.gated:
        filename='gated_'+filename
        report['secondary_triggered_receipts'] = sum(r['secondary_triggered'] for r in results)
        report['total_receipts'] = len(results)
        report['timing_measured'] = False
    (ROOT/'output/receipt_readiness'/filename).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(totals)),flush=True)


if __name__=='__main__': main()
