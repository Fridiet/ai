"""Prepare locked development/evaluation receipts; refuse AI-only ground truth.

python -m scripts.evaluate_receipt_llm --prepare
python -m scripts.evaluate_receipt_llm --manifest output/receipt_llm/manifest.json
python -m scripts.evaluate_receipt_llm --manifest ... --live
"""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import time

from dotenv import load_dotenv
from app.receipt_llm import correct_receipt, request_decisions, select_rows
from app.ingredient_matcher import get_ingredient_catalog
from app.product_pipeline import analyze_ocr_items

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def prepare(path):
    if path.exists():
        raise ValueError('Manifest already exists; refusing to overwrite reviewed labels.')
    folder = ROOT / 'output/local_receipt_review'
    annotations = json.loads((folder/'assistant_annotations.json').read_text(encoding='utf-8'))['records']
    reviews = {r['filename']: r for r in json.loads((folder/'review.json').read_text(encoding='utf-8'))['records']}
    receipts, seen = [], set()
    for record in sorted(annotations, key=lambda r: r['filename']):
        group = record.get('duplicate_group') or record['image_sha256']
        if group in seen or record.get('store_type') != 'grocery' or record['filename'] not in reviews:
            continue
        seen.add(group)
        raw_path = folder / 'raw' / (record['image_sha256'] + '.json')
        if not raw_path.exists():
            continue
        products = analyze_ocr_items(json.loads(raw_path.read_text(encoding='utf-8'))['items'], .35, .72)['products']
        if not products:
            continue
        receipts.append({'receipt_id': record['filename'], 'image_sha256': record['image_sha256'],
            'duplicate_group': group, 'split': 'dev' if len(receipts) < 10 else 'eval',
            'human_verified': False, 'verified_by': '', 'critical_row_ids': [],
            'analysis': {'products': products},
            'expected_rows': [{'row_id': p['row_index'], 'canonical_name': None, 'verified': False} for p in products],
            'reference_suggestions': record['expected_items']})
        if len(receipts) == 30:
            break
    if len(receipts) < 30:
        raise ValueError('Not enough unique grocery receipts.')
    write_json(path, {'version': 1, 'provenance': 'Previously seen development images; not independent test accuracy.',
        'instructions': 'Review each row against the source image. null means nonfood/unknown/excluded, not unfilled truth. Verify all rows and add reviewer identifier before live evaluation.',
        'receipts': receipts})
    return {'status': 'pending_human_verification', 'receipts': len(receipts), 'verified_receipts': 0, 'enable_allowed': False}


def validate_manifest(manifest):
    receipts = manifest['receipts']
    if len(receipts) != 30 or sum(r['split'] == 'dev' for r in receipts) != 10 or sum(r['split'] == 'eval' for r in receipts) != 20:
        raise ValueError('Requires fixed 10 development and 20 evaluation receipts.')
    if len({r['duplicate_group'] for r in receipts}) != 30 or len({r['image_sha256'] for r in receipts}) != 30:
        raise ValueError('Duplicate transactions/images cannot span receipts.')
    catalog_names = {e.name for e in get_ingredient_catalog().entries}
    for r in receipts:
        if r.get('human_verified') is not True or not r.get('verified_by', '').strip():
            raise ValueError('Human-verified ground truth missing; AI annotation is not ground truth.')
        ids = [p['row_index'] for p in r['analysis']['products']]
        labels = r['expected_rows']
        if len(ids) != len(set(ids)) or sorted(ids) != sorted(l['row_id'] for l in labels):
            raise ValueError('Every product row needs exactly one ground-truth label.')
        if any(l.get('verified') is not True or (l['canonical_name'] is not None and l['canonical_name'] not in catalog_names) for l in labels):
            raise ValueError('Labels must be verified and use the current dictionary.')
        if any(i not in ids for i in r.get('critical_row_ids', [])):
            raise ValueError('Unknown critical row identifier.')


async def evaluate(manifest):
    catalog = get_ingredient_catalog()
    stats = {}
    for split in ('dev', 'eval'):
        s = {'receipts': 0, 'target_rows': 0, 'before_correct': 0, 'after_correct': 0,
             'accepted': 0, 'accepted_correct': 0, 'unresolved_before': 0, 'unresolved_resolved_correct': 0,
             'regressions': 0, 'critical_regressions': 0, 'input_tokens': 0, 'output_tokens': 0, 'fallback_receipts': 0}
        durations = []
        for receipt in [r for r in manifest['receipts'] if r['split'] == split]:
            a = receipt['analysis']
            truth = {r['row_id']: r['canonical_name'] for r in receipt['expected_rows']}
            selected = {r['row_id'] for r in select_rows(a, catalog)}
            async def caller(rows, key, model):
                decisions, usage = await request_decisions(rows, key, model)
                s['input_tokens'] += int(usage.get('input_tokens', 0))
                s['output_tokens'] += int(usage.get('output_tokens', 0))
                return decisions, usage
            started = time.perf_counter()
            b = await correct_receipt(a, enabled=True, caller=caller, catalog=catalog)
            durations.append(time.perf_counter()-started)
            s['receipts'] += 1
            s['fallback_receipts'] += int(bool(selected) and b is a)
            for before, after in zip(a['products'], b['products']):
                row = before['row_index']
                old, new = before['match'].get('standard_food_name'), after['match'].get('standard_food_name')
                expected = truth[row]
                if row in selected:
                    s['target_rows'] += 1
                    s['before_correct'] += int(old == expected)
                    s['after_correct'] += int(new == expected)
                    s['unresolved_before'] += int(old is None and expected is not None)
                    s['unresolved_resolved_correct'] += int(old is None and expected is not None and new == expected)
                if after.get('llm_corrected'):
                    s['accepted'] += 1
                    s['accepted_correct'] += int(new == expected)
                regression = old == expected and new != expected
                s['regressions'] += int(regression)
                s['critical_regressions'] += int(regression and row in receipt.get('critical_row_ids', []))
                for field in ('prices', 'purchase_quantity', 'purchase_quantity_source', 'raw_text', 'product_name'):
                    if before.get(field) != after.get(field):
                        raise ValueError('Immutable receipt evidence changed.')
        s['auto_accept_precision'] = s['accepted_correct']/s['accepted'] if s['accepted'] else None
        s['before_accuracy'] = s['before_correct']/s['target_rows'] if s['target_rows'] else None
        s['after_accuracy'] = s['after_correct']/s['target_rows'] if s['target_rows'] else None
        s['resolution_rate'] = s['unresolved_resolved_correct']/s['unresolved_before'] if s['unresolved_before'] else None
        s['p95_extra_seconds'] = sorted(durations)[math.ceil(.95*len(durations))-1]
        # Exact billing may differ; prices must be supplied from current account pricing.
        s['estimated_cost_usd'] = None
        if os.getenv('OCR_EVAL_INPUT_USD_PER_M') and os.getenv('OCR_EVAL_OUTPUT_USD_PER_M'):
            s['estimated_cost_usd'] = (s['input_tokens']*float(os.environ['OCR_EVAL_INPUT_USD_PER_M']) +
                                      s['output_tokens']*float(os.environ['OCR_EVAL_OUTPUT_USD_PER_M']))/1_000_000
        stats[split] = s
    e = stats['eval']
    return {'status': 'evaluated', 'provenance': manifest.get('provenance'), 'metrics': stats,
        'enable_allowed': bool(e['accepted'] and e['auto_accept_precision'] >= .95 and
            e['after_correct'] > e['before_correct'] and not e['regressions'] and e['p95_extra_seconds'] <= 8.1),
        'note': 'Enable only after reviewing regression tests. This tool never changes .env. Tokens exclude unreported/cancelled responses; cost is not a billing guarantee.'}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--manifest', type=Path, default=ROOT/'output/receipt_llm/manifest.json')
    parser.add_argument('--live', action='store_true', help='Paid text-only API calls, only with human-verified labels')
    args = parser.parse_args()
    load_dotenv(ROOT/'.env')
    report_path = args.manifest.with_name('report.json')
    try:
        if args.prepare:
            report = prepare(args.manifest)
        else:
            manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
            validate_manifest(manifest)
            if not args.live:
                report = {'status': 'ready_for_live_evaluation', 'enable_allowed': False}
            elif not os.getenv('OPENAI_API_KEY', '').strip():
                raise ValueError('OPENAI_API_KEY is missing.')
            else:
                report = asyncio.run(evaluate(manifest))
    except (ValueError, KeyError, OSError) as exc:
        report = {'status': 'blocked', 'reason': str(exc), 'enable_allowed': False}
    write_json(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] != 'blocked' else 2


if __name__ == '__main__':
    raise SystemExit(main())
