"""Development-only snapshot audit. No training, DB writes, or image retransmission.

Historical AI labels may be coarser than today's taxonomy. Family-only agreement
is reported separately and never counted as exact correctness. Text alignment is
approximate; inspect failures before using these numbers as performance claims.
"""
import argparse
from collections import Counter
import hashlib
import json
import os
import re
from pathlib import Path
import time

from app.ingredient_taxonomy import family
from app.ingredient_matcher import get_ingredient_catalog, lookup_key
from app.product_pipeline import analyze_ocr_items, clean_ocr_items, group_product_rows
from app.inventory_mapper import build_inventory_candidates
from scripts.evaluate_receipts import greedy_text_matches
from scripts.inventory_quantity_evaluation import total_equal

ROOT = Path(__file__).resolve().parents[1]


def transaction_compatible(expected, raw_text):
    """Use printed gift evidence, never predicted quantity, to align duplicates."""
    kind = expected.get('transaction_type')
    if kind not in {'gift', 'purchase'}:
        return True
    printed_gift = bool(re.search(r'(?:^|\s)증정\s*$', raw_text))
    return printed_gift if kind == 'gift' else not printed_gift


def catalog_alias_agreement(expected, predicted):
    """Separate vocabulary diagnostic, not independent proof of food identity."""
    if not expected or not predicted or expected == predicted:
        return False
    def identities(value):
        key = lookup_key(value)
        return {entry.name for entry in get_ingredient_catalog().entries
                if key in {lookup_key(entry.name), *(lookup_key(a) for a in entry.aliases)}}
    a, b = identities(expected), identities(predicted)
    return len(a) == 1 and a == b


def audit_record(record, raw, *, analysis=None):
    started = time.perf_counter()
    analysis = analyze_ocr_items(raw) if analysis is None else analysis
    products = analysis['products']
    candidates = {r['row_index']: r for r in build_inventory_candidates(analysis)}
    labels = record['expected_items']
    texts = [r['product_name'] for r in labels]
    matches = greedy_text_matches(texts, [[p['product_name'], p['raw_text']] for p in products], threshold=.55,
        compatible=lambda i,j: transaction_compatible(labels[i], products[j]['raw_text']))
    raw_rows = [' '.join(i['text'] for i in row) for row in group_product_rows(clean_ocr_items(raw, 0))]
    raw_matches = greedy_text_matches(texts, [[r] for r in raw_rows], threshold=.55,
        compatible=lambda i,j: transaction_compatible(labels[i], raw_rows[j]))
    counts, details = Counter(), []
    mapped = set()
    for n, expected in enumerate(labels):
        m = matches.get(n)
        product = products[m[0]] if m else None
        candidate = candidates.get(product['row_index']) if product else None
        name = candidate['ingredient_master_candidate']['canonical_name'] if candidate else None
        if candidate:
            mapped.add(product['row_index'])
        if expected.get('is_food') is not True or expected.get('transaction_type') == 'return':
            if candidate and expected.get('is_food') is False:
                counts['nonfood_accepted'] += 1
            continue
        counts['food_rows'] += 1
        if name:
            counts['food_accepted'] += 1
        wanted = expected.get('canonical_name')
        counts['name_labeled_food_rows'] += int(bool(wanted))
        counts['name_labeled_accepted'] += int(bool(wanted and name))
        counts['quantity_labeled_food_rows'] += int(expected.get('quantity') is not None)
        counts['joint_labeled_food_rows'] += int(bool(wanted) and expected.get('quantity') is not None)
        exact = bool(wanted and name == wanted)
        alias_agreement = catalog_alias_agreement(wanted, name)
        counts['catalog_alias_name_agreements'] += int(alias_agreement)
        coarse = bool(wanted and name and not exact and family(wanted) == family(name))
        count_ok = bool(product and expected.get('quantity') is not None and
                        product.get('purchase_quantity') == expected['quantity'])
        counts['exact_names'] += int(exact)
        counts['family_only_requires_reannotation'] += int(coarse)
        counts['correct_purchase_counts'] += int(count_ok)
        counts['name_and_count_correct'] += int(exact and count_ok)
        expected_total = expected.get('inventory_total')
        batch = candidate.get('inventory_batch_candidate', {}) if candidate else {}
        total_ok = bool(expected_total and candidate and total_equal(
            batch.get('remaining_quantity'), batch.get('unit'), expected_total))
        if expected_total:
            counts['inventory_total_labeled_food_rows'] += 1
            counts['inventory_total_correct'] += int(total_ok)
            counts['name_and_inventory_total_correct'] += int(exact and total_ok)
            # A default purchase count of one can accidentally yield the right
            # total. Report that separately from totals backed by OCR evidence.
            supported = bool(product and product.get('purchase_quantity') is not None
                             and product.get('purchase_quantity_source') in {
                                 'VERIFIED_RECEIPT_COLUMNS', 'RECONSTRUCTED_PRICE_COLUMNS',
                                 'SECONDARY_VERIFIED_RECEIPT_COLUMNS', 'VERIFIED_RECEIPT_TOTALS',
                                 'NUMERIC_CELLS_VERIFIED'})
            counts['inventory_total_with_purchase_evidence_correct'] += int(total_ok and supported)
        counts['wrong_asserted_purchase_counts'] += int(bool(product and
            expected.get('quantity') is not None and product.get('purchase_quantity') is not None
            and not count_ok))
        warnings = candidate.get('warnings', []) if candidate else []
        for warning in warnings:
            counts['warning:' + warning] += 1
        confirmation = [w for w in warnings if 'CONFIRMATION' in w or w in {
            'QUANTITY_DEFAULTED_TO_ONE', 'PURCHASE_QUANTITY_RECONSTRUCTED_FROM_PRICES'}]
        counts['accepted_food_with_confirmation_warning'] += int(bool(candidate and confirmation))
        counts['correct_name_count_with_confirmation_warning'] += int(bool(exact and count_ok and confirmation))
        failure = ('ocr_or_alignment' if n not in raw_matches else 'row_filter_or_alignment') if not product else (
            'classification_abstention' if not name else 'stale_label_or_specificity' if coarse else
            'wrong_name' if not exact else 'quantity' if not count_ok else 'ok')
        if not wanted:
            failure = 'label_unresolved'
        counts[failure] += 1
        details.append({'expected': expected, 'predicted_name': name,
                        'catalog_alias_agreement': alias_agreement,
                        'predicted_count': product.get('purchase_quantity') if product else None,
                        'quantity_source': product.get('purchase_quantity_source') if product else None,
                        'raw_text': product['raw_text'] if product else None,
                        'predicted_inventory_quantity': batch.get('remaining_quantity'),
                        'predicted_inventory_unit': batch.get('unit'),
                        'inventory_total_correct': total_ok if expected_total else None,
                        'warnings': warnings, 'confirmation_warnings': confirmation,
                        'source': product['match'].get('source') if product else None,
                        'alignment_score': m[1] if m else None, 'failure': failure})
    unaligned = [r['raw_text'] for k, r in candidates.items() if k not in mapped]
    counts['unaligned_accepted_requires_review'] = len(unaligned)
    return {'filename': record['filename'], 'counts': dict(counts), 'rows': details,
            'unaligned': unaligned, 'postprocessing_seconds': time.perf_counter()-started}


def diagnostic_rates(counts):
    """Conditional diagnostics, not an independent end-to-end accuracy claim."""
    def ratio(n, d):
        return round(n / d, 4) if d else None
    joint = counts.get('joint_labeled_food_rows', 0)
    return {
        'exact_name_recall_on_labeled_food': ratio(counts.get('exact_names', 0), counts.get('name_labeled_food_rows', 0)),
        'exact_name_precision_on_aligned_labeled_food_only': ratio(counts.get('exact_names', 0), counts.get('name_labeled_accepted', 0)),
        'purchase_count_accuracy_on_labeled_food': ratio(counts.get('correct_purchase_counts', 0), counts.get('quantity_labeled_food_rows', 0)),
        'name_or_count_correction_needed_on_joint_labels': ratio(joint - counts.get('name_and_count_correct', 0), joint),
        'scope_note': 'Approximate text alignment and draft labels. Correction excludes storage, dates and package contents. Conditional precision excludes unaligned outputs and nonfood; inspect their separate counts.',
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=ROOT/'output/receipt_readiness/baseline.json')
    p.add_argument('--limit', type=int)
    args = p.parse_args()
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    from app.ocr_classification import warmup
    warmup()
    path = ROOT/'output/local_receipt_review/assistant_annotations.json'
    records = json.loads(path.read_text(encoding='utf-8'))['records']
    selected = [r for r in records if r.get('store_type') in {'grocery', 'convenience'}]
    if args.limit:
        selected = selected[:args.limit]
    results, total = [], Counter()
    for record in selected:
        rawpath = ROOT/'output/local_receipt_review/raw'/f"{record['image_sha256']}.json"
        raw = json.loads(rawpath.read_text(encoding='utf-8'))['items']
        result = audit_record(record, raw)
        results.append(result)
        total.update(result['counts'])
        print(f"{len(results)}/{len(selected)} {record['filename']} {result['counts']}", flush=True)
    report = {'scope': 'development_snapshot_diagnostic_not_independent_accuracy',
              'human_verified': False, 'image_recognition_timing_measured': False,
              'labels_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'counts': dict(total), 'diagnostic_rates': diagnostic_rates(total), 'receipts': results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(total, ensure_ascii=False))


if __name__ == '__main__':
    main()
