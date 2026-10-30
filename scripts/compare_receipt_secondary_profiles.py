"""Compare secondary detector caches on identical development images only."""
import json
import os
import argparse
import hashlib
import time
import cv2
import numpy as np
from collections import Counter
from pathlib import Path

from app.product_pipeline import analyze_ocr_items
from app.receipt_count_fusion import needs_secondary_count_recovery, supplement_product_counts
from app.receipt_numeric_cells import supplement_numeric_cells
from app.receipt_numeric_runtime import recover_numeric_counts
from scripts.audit_receipt_readiness import audit_record

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--existing-only', action='store_true')
    parser.add_argument('--numeric-cells',action='store_true')
    parser.add_argument('--numeric-runtime',action='store_true')
    parser.add_argument('--primary-profile', choices=['cached', 'default', 'max3200'], default='cached')
    parser.add_argument('--header-runtime', action='store_true')
    args = parser.parse_args()
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    source = json.loads((ROOT/'output/new_receipts/detector_sensitivity_all.json').read_text(encoding='utf-8'))
    partition = json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    entries = {r['filename']: r for r in partition['records']}
    labels = json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']
    profiles = ('primary', 'existing_960') if args.existing_only else ('primary', 'existing_960', 'sensitive_960')
    numeric={}
    if args.numeric_runtime:
        if not args.existing_only or args.numeric_cells:
            raise ValueError('Runtime replay needs --existing-only without --numeric-cells')
        profiles=profiles+('numeric_runtime',)
    if args.numeric_cells:
        if not args.existing_only:raise ValueError('Numeric-cell replay requires --existing-only')
        profiles=profiles+('numeric_cells',)
        numeric={r['filename']:r.get('candidates',[]) for r in json.loads((ROOT/'output/new_receipts/calibrated_numeric_all_development.json').read_text(encoding='utf-8'))['results']}
    totals = {k: Counter() for k in profiles}
    rows, skipped = [], []
    for label in labels:
        name = label['filename']
        if entries[name]['split'] != 'development':
            raise ValueError('Development only')
        old_path = ROOT/'output/new_receipts/detector_experiment'/f'{Path(name).stem}-max960.json'
        if not old_path.exists():
            skipped.append(name)
            continue
        old = json.loads(old_path.read_text(encoding='utf-8'))
        if old['filename'] != name or old['profile'] != 'max960':
            raise ValueError('Mismatched secondary cache')
        runs = {r['variant']: r for r in source['results'] if r['filename'] == name}
        if not args.existing_only and not {'default','sensitive_960'}.issubset(runs):
            skipped.append(name)
            continue
        if args.primary_profile != 'cached':
            primary_cache = json.loads((ROOT/'output/new_receipts/detector_experiment'/
                f'{Path(name).stem}-{args.primary_profile}.json').read_text(encoding='utf-8'))
            if primary_cache['filename'] != name or primary_cache['profile'] != args.primary_profile:
                raise ValueError('Mismatched primary cache')
            raw = primary_cache['items']
        elif 'default' in runs:
            raw = runs['default']['items']
        else:
            raw = json.loads((ROOT/'output/new_receipts/detector_experiment'/f'{Path(name).stem}-default.json').read_text(encoding='utf-8'))['items']
        header_seconds = None
        header_added = 0
        if args.header_runtime:
            from app.receipt_header_recovery import recover_header, header_seed
            from app import ocr
            from dataclasses import replace
            from unittest.mock import patch
            if header_seed(raw) is not None:
                payload = Path(entries[name]['image_path']).read_bytes()
                if hashlib.sha256(payload).hexdigest() != entries[name]['sha256']:
                    raise ValueError('Image changed')
                image = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
                if image is None:raise ValueError('Image decode failed')
                with patch.object(ocr, 'settings', replace(ocr.settings,
                        ocr_detection_max_side=3200 if args.primary_profile == 'max3200' else 0)):
                    started = time.perf_counter()
                    recovered = recover_header(image, raw)
                    header_seconds = time.perf_counter()-started
                header_added = len(recovered)-len(raw)
                raw = recovered
        primary = analyze_ocr_items(raw)
        trigger = needs_secondary_count_recovery(primary)
        analyses = {'primary': primary}
        secondary_inputs = [('existing_960', old['items'])]
        if not args.existing_only:
            secondary_inputs.append(('sensitive_960', runs['sensitive_960']['items']))
        for key, items in secondary_inputs:
            analyses[key] = supplement_product_counts(primary, analyze_ocr_items(items)) if trigger else primary
        if args.numeric_cells:
            analyses['numeric_cells']=supplement_numeric_cells(analyses['existing_960'],analyze_ocr_items(old['items']),numeric.get(name,[])) if trigger else primary
        numeric_seconds = None
        if args.numeric_runtime:
            payload=Path(entries[name]['image_path']).read_bytes()
            if hashlib.sha256(payload).hexdigest()!=entries[name]['sha256']:
                raise ValueError('Image changed')
            image=cv2.imdecode(np.frombuffer(payload,np.uint8),cv2.IMREAD_COLOR)
            if image is None:raise ValueError('Image decode failed')
            started=time.perf_counter()
            analyses['numeric_runtime']=recover_numeric_counts(image,analyses['existing_960'],
                analyze_ocr_items(old['items']),old['items']) if trigger else primary
            numeric_seconds=time.perf_counter()-started
        results = {key: audit_record(label, raw, analysis=analysis) for key, analysis in analyses.items()}
        for key, result in results.items():
            totals[key].update(result['counts'])
        rows.append(dict(filename=name, secondary_triggered=trigger, profiles=results,
                         header_runtime_seconds=header_seconds, header_added=header_added,
                         numeric_runtime_seconds=numeric_seconds))
        if args.numeric_runtime:print(json.dumps(dict(filename=name,seconds=numeric_seconds)),flush=True)
    report = dict(scope='matched_development_cache_comparison_not_independent_accuracy',
                  primary_profile=args.primary_profile, header_runtime=args.header_runtime,
                  classifier_enabled=os.getenv('OCR_CLASSIFIER_ENABLED','false').lower() in {'1','true','yes'},
                  labels_sha256=hashlib.sha256((ROOT/'evaluation/new_receipts_development.json').read_bytes()).hexdigest(),
                  timing_measured=False, compared_receipts=len(rows), missing_existing_cache=skipped,
                  counts={k: dict(v) for k,v in totals.items()}, receipts=rows)
    output_name = 'existing_secondary_comparison.json' if args.existing_only else 'secondary_profile_comparison.json'
    if args.numeric_cells:output_name='numeric_cell_fusion_comparison.json'
    if args.numeric_runtime:output_name='numeric_runtime_comparison.json'
    if args.primary_profile != 'cached' or args.header_runtime:
        output_name=Path(output_name).stem+'-'+args.primary_profile+('-header' if args.header_runtime else '')+'.json'
    (ROOT/'output/new_receipts'/output_name).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='receipts'}))


if __name__ == '__main__':
    main()
