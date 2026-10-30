"""Development ablation of footer handling, not independent accuracy."""
import json
import os
import re
from pathlib import Path
from unittest.mock import patch
import app.product_pipeline as pipeline
from app.receipt_mapper import build_review_candidates

ROOT=Path(__file__).resolve().parents[1]


def main():
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED','true')
    from app.ocr_classification import warmup
    warmup()
    partition=json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']
    byname={r['filename']:r for r in partition}
    labels=json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']
    old=json.loads((ROOT/'output/local_receipt_review/assistant_annotations.json').read_text(encoding='utf-8'))['records']
    records=[]
    for scope,group in [('new',labels),('historical',[r for r in old if r.get('store_type') in {'grocery','convenience'}])]:
        for label in group:
            if scope=='new':
                entry=byname[label['filename']]
                if entry['split']!='development': raise ValueError('Frozen image forbidden')
                path=ROOT/'output/new_receipts/raw'/f"{entry['sha256']}.json"
            else:
                path=ROOT/'output/local_receipt_review/raw'/f"{label['image_sha256']}.json"
            raw=json.loads(path.read_text(encoding='utf-8'))['items']
            with patch.object(pipeline,'is_tax_total_row',lambda text: bool(re.match(r'^[(*#\[]*(?:면세|과세)물품(?:가액)?',re.sub(r'\s+','',text)))):
                before=pipeline.analyze_ocr_items(raw)
            after=pipeline.analyze_ocr_items(raw)
            a=build_review_candidates(before); b=build_review_candidates(after)
            records.append({'scope':scope,'filename':label['filename'],'before_review':len(a),'after_review':len(b),
                'removed_review':[r['rawText'] for r in a if r not in b],
                'added_review':[r['rawText'] for r in b if r not in a]})
    report={'scope':'development_ablation','records':records,'summary':{}}
    for scope in ['new','historical']:
        rows=[r for r in records if r['scope']==scope]
        report['summary'][scope]={'receipts':len(rows),'before_review':sum(r['before_review'] for r in rows),
                                'after_review':sum(r['after_review'] for r in rows)}
    (ROOT/'output/receipt_readiness/tax_boundary_comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary']))
    print(json.dumps([r for r in records if r['removed_review'] or r['added_review']],ensure_ascii=True))


if __name__=='__main__':main()
