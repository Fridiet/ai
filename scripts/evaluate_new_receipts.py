"""Evaluate visually annotated development snapshots, enforcing frozen exclusion."""
import json
import os
from collections import Counter
from pathlib import Path
from scripts.audit_receipt_readiness import audit_record, diagnostic_rates

ROOT=Path(__file__).resolve().parents[1]


def main():
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED','true')
    from app.ocr_classification import warmup
    warmup()
    partition=json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))
    by_name={r['filename']:r for r in partition['records']}
    labels=json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))
    reports=[]
    counts=Counter()
    for record in labels['records']:
        entry=by_name[record['filename']]
        if entry['split']!='development':
            raise ValueError('Only development images may be used here')
        raw=json.loads((ROOT/'output/new_receipts/raw'/f"{entry['sha256']}.json").read_text(encoding='utf-8'))['items']
        result=audit_record(record,raw)
        # The unresolved single glyph is not an exact-name ground truth.
        for row in result['rows']:
            if row['expected'].get('canonical_name') is None:
                row['failure']='label_unresolved'
        counts.update(result['counts'])
        reports.append(result)
    report={'scope':'development_visual_draft_not_independent_accuracy','human_verified':False,
            'counts':dict(counts),'diagnostic_rates':diagnostic_rates(counts),'receipts':reports}
    (ROOT/'output/new_receipts/evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(counts))


if __name__=='__main__':
    main()
