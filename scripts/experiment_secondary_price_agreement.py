"""Offline test of dual-pass price agreement; never changes production sources."""
import json
import os
import re
from collections import Counter
from pathlib import Path

from app.product_pipeline import analyze_ocr_items
from app.receipt_count_fusion import compatible, supplement_product_counts, needs_secondary_count_recovery
from scripts.audit_receipt_readiness import audit_record

ROOT=Path(__file__).resolve().parents[1]


def reconstructed_compatible(primary, secondary):
    if secondary.get('purchase_quantity_source')!='RECONSTRUCTED_PRICE_COLUMNS':
        return False
    text=re.sub(r'(?<=\d),\s+(?=\d{3}(?:\D|$))',',',primary.get('raw_text',''))
    observed=re.search(r'(?:^|\s)(\d[\d,]*)\s+(\d[\d,]*)\s*[*#]{0,2}\s*$',text)
    if not observed:
        return False
    prices=[int(s.replace(',','')) for s in observed.groups()]
    if prices[0]<100 or prices!=secondary.get('prices',[])[-2:]:
        return False
    if prices[0]*secondary.get('purchase_quantity',0)!=prices[1]:
        return False
    # Reuse all existing identity, geometry and contradiction checks without
    # relabeling the actual secondary evidence or its output provenance.
    return compatible(primary,dict(secondary,purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS'))


def trial_supplement(primary,secondary):
    baseline=supplement_product_counts(primary,secondary)
    options={i:[j for j,s in enumerate(secondary['products']) if
                compatible(p,s) or reconstructed_compatible(p,s)]
             for i,p in enumerate(primary['products'])}
    products=[dict(p) for p in baseline['products']]
    for i,p in enumerate(products):
        if p.get('purchase_quantity') is not None or len(options[i])!=1:
            continue
        j=options[i][0]
        if sum(j in matches for matches in options.values())!=1:
            continue
        s=secondary['products'][j]
        if reconstructed_compatible(primary['products'][i],s):
            p.update(purchase_quantity=s['purchase_quantity'],
                     purchase_quantity_source='EXPERIMENT_DUAL_PRICE_AGREEMENT',
                     secondary_quantity_evidence=s['raw_text'])
    return dict(baseline,products=products)


def main():
    os.environ.setdefault('OCR_CLASSIFIER_ENABLED','true')
    labels=json.loads((ROOT/'evaluation/new_receipts_development.json').read_text(encoding='utf-8'))['records']
    entries={r['filename']:r for r in json.loads((ROOT/'output/new_receipts/partition.json').read_text(encoding='utf-8'))['records']}
    runs={r['filename']:r['items'] for r in json.loads((ROOT/'output/new_receipts/detector_sensitivity_all.json').read_text(encoding='utf-8'))['results'] if r['variant']=='default'}
    totals={'baseline':Counter(),'trial':Counter()}
    receipts=[]
    blocked=[]
    for label in labels:
        name=label['filename']
        if entries[name]['split']!='development':
            raise ValueError('Development only')
        base=ROOT/'output/new_receipts/detector_experiment'
        raw=runs[name] if name in runs else json.loads((base/f'{Path(name).stem}-default.json').read_text(encoding='utf-8'))['items']
        secondary_raw=json.loads((base/f'{Path(name).stem}-max960.json').read_text(encoding='utf-8'))['items']
        primary=analyze_ocr_items(raw)
        secondary=analyze_ocr_items(secondary_raw)
        triggered=needs_secondary_count_recovery(primary)
        baseline=supplement_product_counts(primary,secondary) if triggered else primary
        for p in baseline['products']:
            if p.get('purchase_quantity') is not None:
                continue
            for s in secondary['products']:
                if s.get('purchase_quantity_source')=='RECONSTRUCTED_PRICE_COLUMNS' and compatible(
                        p,dict(s,purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS')):
                    blocked.append(dict(filename=name,primary=p['raw_text'],secondary=s['raw_text'],
                        primary_prices=p.get('prices'),secondary_prices=s.get('prices'),
                        trial_eligible=reconstructed_compatible(p,s)))
        trial=trial_supplement(primary,secondary) if triggered else primary
        reports={key:audit_record(label,raw,analysis=value) for key,value in [('baseline',baseline),('trial',trial)]}
        for key,r in reports.items(): totals[key].update(r['counts'])
        receipts.append(dict(filename=name,profiles=reports))
    report=dict(scope='development_offline_trial_not_independent_accuracy',auto_promotion=False,
                counts=totals,receipts=receipts,reconstructed_source_candidates=blocked)
    (ROOT/'output/new_receipts/secondary_price_agreement.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(totals))


if __name__=='__main__':main()
