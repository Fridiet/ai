"""Paid, curated text regression checks; NOT held-out receipt accuracy."""
import asyncio
import json
import os
from pathlib import Path
import time

from dotenv import load_dotenv

from app.ingredient_matcher import get_ingredient_catalog
from app.inventory_mapper import build_inventory_candidates
from app.receipt_llm import correct_receipt

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ('농심 올리브짜파게', '라면'), ('농심 오징어짬뽕124g', '라면'),
    ('친환경경주미니양파', '양파'), ('송송어린대파', '대파'),
    ('강원 햇 당근', '당근'), ('양상추', '양상추'), ('청양고추', '청양고추'),
    ('팽이버섯', '팽이버섯'), ('양송이버섯', '양송이버섯'), ('새송이버섯', '새송이버섯'),
    ('표고버섯', '표고버섯'), ('느타리버섯', '느타리버섯'), ('가시오이', '가시오이'),
    ('애호박', '애호박'), ('파인애플', '파인애플'), ('고등어', '고등어'),
    ('양파 500g', '양파'), ('양파 1kg', '양파'), ('달걀 0.5개', '달걀'),
    ('소고기', '소고기'), ('돼지고기', '돼지고기'), ('닭고기', '닭고기'),
    ('땅콩버터', '땅콩버터'), ('농심 신라면', '라면'), ('감자칩', '감자칩'),
    ('참치캔', '참치캔'), ('우유', '우유'), ('두부', '두부'),
    ('소금', '소금'), ('간장', '간장'), ('풀무원 그릭 무가', None),
    # Existing explicit dictionary alias 오이스터블 -> 부채살, never 오이.
    ('호주곡물오이스터블', '부채살'),
]


def make_product(index, text):
    match = get_ingredient_catalog().match(text)
    return dict(row_index=index, product_name=text, normalized_name=text,
                raw_text=text+' 3,980 2 7,960', prices=[3980, 7960],
                purchase_quantity=2, purchase_quantity_source='VERIFIED_RECEIPT_COLUMNS',
                match=dict(standard_food_name=match['standard_name'], match_type=match['match_type'],
                           matched_name=match['matched_alias'], score=match['score'],
                           food_type='원재료', classification='ingredient' if match['standard_name'] else 'unknown',
                           manufacturer=None, source='ingredient_dictionary'))


async def main():
    load_dotenv(ROOT/'.env')
    if not os.getenv('OPENAI_API_KEY'):
        raise RuntimeError('OPENAI_API_KEY not configured')
    records, durations = [], []
    for offset in range(0, len(CASES), 16):
        group = CASES[offset:offset+16]
        original = {'products': [make_product(offset+i, text) for i, (text, _) in enumerate(group)]}
        started = time.perf_counter()
        corrected = await correct_receipt(original, enabled=True)
        durations.append(time.perf_counter()-started)
        for (text, expected), before, after in zip(group, original['products'], corrected['products']):
            for field in ('raw_text', 'product_name', 'prices', 'purchase_quantity', 'purchase_quantity_source'):
                assert before[field] == after[field], field
            old_batch = build_inventory_candidates({'products': [before]})
            new_batch = build_inventory_candidates({'products': [after]})
            if old_batch and new_batch:
                for field in ('remaining_quantity', 'unit', 'quantity_source'):
                    assert old_batch[0]['inventory_batch_candidate'][field] == new_batch[0]['inventory_batch_candidate'][field]
            records.append(dict(row_id=before['row_index'], text=text, expected=expected,
                                before=before['match']['standard_food_name'], after=after['match']['standard_food_name'],
                                corrected=bool(after.get('llm_corrected'))))
    accepted = [row for row in records if row['corrected']]
    report = dict(scope='32 curated text cases; dictionary baseline, not full OCR or independent receipt accuracy',
                  cases=len(records), before_correct=sum(r['before']==r['expected'] for r in records),
                  after_correct=sum(r['after']==r['expected'] for r in records),
                  accepted=len(accepted), accepted_correct=sum(r['after']==r['expected'] for r in accepted),
                  regressions=sum(r['before']==r['expected'] and r['after']!=r['expected'] for r in records),
                  seconds=durations, immutable_evidence_and_quantity_preserved=True,
                  rows=records)
    path = ROOT/'output/receipt_llm/live_regression.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='rows'}, ensure_ascii=False))
    print('report:', path)
    print('failed_row_ids:', [r['row_id'] for r in records if r['after']!=r['expected']])


if __name__ == '__main__':
    asyncio.run(main())
