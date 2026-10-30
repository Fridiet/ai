"""Development-only detector sensitivity comparison; never changes API settings."""
import argparse
import hashlib
import json
import os
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from app.ocr import get_engine, _as_list
from scripts.audit_receipt_readiness import audit_record
from app.product_pipeline import analyze_ocr_items
from app.receipt_count_fusion import supplement_product_counts, needs_secondary_count_recovery
from scripts.benchmark_receipt_api import percentile

ROOT = Path(__file__).resolve().parents[1]


def summarize_runs(runs, time_key):
    summaries = {}
    for variant in sorted({r['variant'] for r in runs}):
        selected = [r for r in runs if r['variant'] == variant]
        counts = Counter()
        for run in selected:
            counts.update(run['audit']['counts'])
        times = [r[time_key] for r in selected]
        summaries[variant] = dict(receipts=len(selected), counts=dict(counts),
                                 sample_p50_seconds=percentile(times, .5),
                                 sample_p95_seconds=percentile(times, .95))
    return summaries


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filenames', nargs='*')
    parser.add_argument('--all-development', action='store_true')
    parser.add_argument('--output', type=Path, default=ROOT/'output/new_receipts/detector_sensitivity.json')
    parser.add_argument('--replay', action='store_true', help='Evaluate cached variants without another OCR run')
    parser.add_argument('--gated', action='store_true', help='Replay the existing missing-food-count trigger')
    args = parser.parse_args()
    if args.gated and not args.replay:
        parser.error('--gated requires --replay')
    partition = json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    entries = {r['filename']: r for r in partition['records']}
    labels = json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))
    labels = {r['filename']: r for r in labels['records']}
    if args.all_development:
        if args.filenames:
            parser.error('Choose filenames or --all-development, not both')
        args.filenames = list(labels)
    if not args.filenames:
        parser.error('Select annotated development images')
    for name in args.filenames:
        if name not in labels or name not in entries or entries[name]['split'] != 'development':
            raise ValueError('Only annotated development images are permitted')
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT/'output'):
        raise ValueError('Experiment output must remain under AI/output')
    if args.replay:
        report = json.loads(output.read_text(encoding='utf-8'))
        fused_reports = []
        for name in args.filenames:
            runs = {r['variant']: r for r in report['results'] if r['filename'] == name}
            primary = analyze_ocr_items(runs['default']['items'])
            for variant in ('sensitive', 'sensitive_960'):
                triggered = not args.gated or needs_secondary_count_recovery(primary)
                secondary = analyze_ocr_items(runs[variant]['items']) if triggered else None
                fused = supplement_product_counts(primary, secondary) if triggered else primary
                audit = audit_record(labels[name], runs['default']['items'], analysis=fused)
                result = dict(filename=name, variant=variant, audit=audit,
                              secondary_triggered=triggered,
                              primary_names_preserved=all(a['match']==b['match'] for a,b in zip(primary['products'],fused['products'])),
                              existing_counts_preserved=all(a['purchase_quantity']==b['purchase_quantity'] for a,b in zip(primary['products'],fused['products']) if a['purchase_quantity'] is not None),
                              measured_ocr_sum_seconds=runs['default']['ocr_seconds']+(runs[variant]['ocr_seconds'] if triggered else 0))
                fused_reports.append(result)
                print(json.dumps({k:v for k,v in result.items() if k!='audit'} | {'counts': audit['counts']}), flush=True)
        key = 'gated_fusion' if args.gated else 'fusion'
        report[key] = fused_reports
        report['summary'] = summarize_runs(report['results'], 'ocr_seconds')
        report[key+'_summary'] = summarize_runs(fused_reports, 'measured_ocr_sum_seconds')
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    start = time.perf_counter()
    engine = get_engine()
    report = {'scope': 'development_experiment_not_independent_accuracy',
              'auto_promotion': False, 'engine_load_seconds': time.perf_counter()-start,
              'results': []}
    variants = [('default', {}), ('sensitive', {'text_det_thresh': .15, 'text_det_box_thresh': .4}),
                ('sensitive_960', {'text_det_thresh': .15, 'text_det_box_thresh': .4,
                                   'text_det_limit_side_len': 960, 'text_det_limit_type': 'max'})]
    for name in args.filenames:
        entry = entries[name]
        source = Path(entry['image_path']).read_bytes()
        if hashlib.sha256(source).hexdigest() != entry['sha256']:
            raise ValueError('Image changed')
        image = cv2.imdecode(np.frombuffer(source, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError('Invalid image')
        for variant, options in variants:
            start = time.perf_counter()
            items = []
            for result in engine.predict(image, **options):
                data = result.json
                data = data.get('res', data)
                for text, score, box in zip(_as_list(data.get('rec_texts')),
                                             _as_list(data.get('rec_scores')), _as_list(data.get('rec_polys'))):
                    items.append(dict(text=str(text), confidence=float(score),
                                      box=[[int(x), int(y)] for x, y in box]))
            seconds = time.perf_counter()-start
            audit = audit_record(labels[name], items)
            report['results'].append(dict(filename=name, variant=variant, options=options,
                                          ocr_seconds=seconds, items=items, audit=audit))
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(dict(filename=name, variant=variant, seconds=seconds,
                                  counts=audit['counts']), ensure_ascii=True), flush=True)


if __name__ == '__main__':
    main()
