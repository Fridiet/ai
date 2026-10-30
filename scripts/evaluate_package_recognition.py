"""Offline ablation of agreement-gated package rereads, never an API switch."""
import hashlib
import json
import math
import os
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path

from app.inventory_mapper import parse_inventory_quantity
from scripts.audit_receipt_readiness import audit_record

ROOT = Path(__file__).resolve().parents[1]


def agreement_candidate(candidates):
    if len(candidates) != 2 or {c['padding'] for c in candidates} != {0, 3}:
        return None
    if any(not math.isfinite(c['score']) or c['score'] < .85 for c in candidates):
        return None
    texts = [c['after'] for c in candidates]
    if ''.join(texts[0].split()) != ''.join(texts[1].split()):
        return None
    packages = [parse_inventory_quantity(t) for t in texts]
    if packages[0] != packages[1] or packages[0]['source'] != 'PACKAGE_TEXT':
        return None
    if not math.isfinite(packages[0]['quantity']) or packages[0]['quantity'] <= 0:
        return None
    before = parse_inventory_quantity(candidates[0]['before'])
    if before == packages[0]:
        return None
    return max(candidates, key=lambda c: c['score'])


def main():
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED', 'true')
    experiment = json.loads((ROOT/'output/new_receipts/package_recognition_all_development.json').read_text(encoding='utf-8'))
    labels = {r['filename']: r for r in json.loads(
        (ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']}
    partition = {r['filename']: r for r in json.loads(
        (ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']}
    aggregate = {'baseline':Counter(), 'agreement_trial':Counter()}
    receipts, transitions = [], Counter()
    for result in experiment['results']:
        filename = result['filename']
        entry = partition[filename]
        if entry['split'] != 'development' or entry['sha256'] != result['sha256']:
            raise ValueError('Development provenance mismatch')
        path = (ROOT/result['raw_cache']).resolve()
        if not path.is_relative_to(ROOT/'output'):
            raise ValueError('Cache outside output')
        raw_bytes = path.read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != result['raw_cache_sha256']:
            raise ValueError('Raw cache changed')
        original = json.loads(raw_bytes)['items']
        modified = deepcopy(original)
        grouped = defaultdict(list)
        for candidate in result['candidates']:
            grouped[candidate['index']].append(candidate)
        edits = []
        for index, candidates in grouped.items():
            if any(c['before'] != original[index]['text'] for c in candidates):
                raise ValueError('Crop/cache text mismatch')
            winner = agreement_candidate(candidates)
            if winner is not None:
                modified[index]['text'] = winner['after']
                modified[index]['confidence'] = winner['score']
                edits.append(winner)
        baseline = audit_record(labels[filename], original)
        trial = audit_record(labels[filename], modified)
        aggregate['baseline'].update(baseline['counts'])
        aggregate['agreement_trial'].update(trial['counts'])
        for old,new in zip(baseline['rows'],trial['rows'],strict=True):
            if old['expected'] != new['expected']:
                raise ValueError('Label order changed')
            if old['expected'].get('inventory_total'):
                a,b = old.get('inventory_total_correct'),new.get('inventory_total_correct')
                transitions[f'total_{bool(a)}_to_{bool(b)}'] += 1
            if old.get('predicted_name') != new.get('predicted_name'):
                transitions['name_changed'] += 1
        receipts.append(dict(filename=filename, edits=edits, baseline=baseline, agreement_trial=trial))
    report = dict(scope='development_visual_draft_offline_ablation_not_independent_accuracy',
                  auto_promotion=False, compared_receipts=len(receipts), counts=aggregate,
                  transitions=transitions, receipts=receipts)
    (ROOT/'output/new_receipts/package_recognition_comparison.json').write_text(
        json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(counts=aggregate,transitions=transitions),ensure_ascii=True))


if __name__ == '__main__':
    main()
