"""Measure numeric crop acceptance against draft labels and observed controls."""
import json
import math
import os
import re
from app.receipt_numeric_cells import verified_triplet
from collections import Counter,defaultdict
from pathlib import Path
from scripts.audit_receipt_readiness import audit_record

ROOT=Path(__file__).resolve().parents[1]


def main():
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED','true')
    experiment=json.loads((ROOT/'output/new_receipts/calibrated_numeric_all_development.json').read_text(encoding='utf-8'))
    labels={r['filename']:r for r in json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']}
    totals=Counter(); reports=[]
    for result in experiment['results']:
        if result.get('skipped'):
            totals['skipped_receipts']+=1; continue
        name=result['filename']
        raw=json.loads((ROOT/'output/new_receipts/detector_experiment'/f'{Path(name).stem}-max960.json').read_text(encoding='utf-8'))['items']
        audit=audit_record(labels[name],raw)
        by_text=defaultdict(list)
        for row in audit['rows']:by_text[row.get('raw_text')].append(row)
        grouped=defaultdict(list)
        for cell in result['candidates']:grouped[(cell['row'],cell['margin'])].append(cell)
        rows=[]
        totals['processed_receipts']+=1
        for (index,margin),cells in grouped.items():
            accepted=verified_triplet(cells)
            matches=by_text[cells[0]['secondary']]
            expected=matches[0]['expected'].get('quantity') if len(matches)==1 else None
            prefix=f'margin_{margin}_'
            totals[prefix+'rows']+=1
            if expected is None:totals[prefix+'unlabeled_or_ambiguous_alignment']+=1
            if accepted is not None:
                totals[prefix+'accepted']+=1
                if expected is not None:
                    totals[prefix+('correct' if accepted['count']==expected else 'wrong')]+=1
                else:totals[prefix+'accepted_without_label']+=1
                existing=cells[0]['existing_count']
                if existing is not None and accepted['count']!=existing:
                    totals[prefix+'conflict_existing_count']+=1
            rows.append(dict(row=index,margin=margin,raw=cells[0]['secondary'],expected=expected,
                             existing=cells[0]['existing_count'],accepted=accepted))
        reports.append(dict(filename=name,rows=rows))
    report=dict(scope='development_draft_numeric_cells_not_inventory_readiness',auto_promotion=False,
                counts=totals,receipts=reports)
    (ROOT/'output/new_receipts/calibrated_numeric_evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(totals))


if __name__=='__main__':main()
