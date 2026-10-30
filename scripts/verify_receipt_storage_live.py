"""Paid GPT smoke test using invented product-name fixtures, never DB writes."""
import asyncio
import json
import time
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from app.receipt_shelf_life import fill_missing_dates, finalize_registration_items


async def main():
    load_dotenv('.env')
    fixtures = [
        ('피자', '냉동 콤비네이션 피자', 'processed_food'),
        ('만두', '냉동 왕교자 만두', 'processed_food'),
        ('아이스크림', '미개봉 아이스크림', 'processed_food'),
        ('감자', '냉동 감자튀김', 'vegetable'),
        ('소고기', '냉장 소고기', 'meat'),
        ('피자', '조리된 남은 피자', 'processed_food'),
        ('맥주', '미개봉 캔 맥주', 'beverage'),
        ('라면', '미개봉 봉지 라면', 'processed_food'),
        ('우유', '냉장 우유', 'dairy'),
        ('두유', '미개봉 멸균 두유', 'beverage'),
    ]
    today = date.today()
    original = [dict(name=name, _product_name=product, category=category,
                     _storage_source='UNKNOWN', _date_source='CATEGORY_FALLBACK',
                     location_category='unknown', consume_by_date=today+timedelta(days=3),
                     quantity=2, unit='개', review_notes=[]) for name, product, category in fixtures]
    started = time.perf_counter()
    result = await fill_missing_dates(original, include_existing=True)
    rows = [dict(product=before['_product_name'], name=after['name'],
                 source=after.get('_date_source'), days=(after['consume_by_date']-today).days,
                 consumeByDate=after['consume_by_date'].isoformat(),
                 locationCategory=after['location_category']) for before, after in zip(original, result)]
    report = dict(seconds=time.perf_counter()-started, rows=rows,
                  gpt_count=sum(r['source']=='GPT' for r in rows))
    # No food-safety accuracy claim: these are smoke-test fixtures, not ground truth.
    assert all(after['quantity']==before['quantity'] and after['unit']==before['unit']
               for before, after in zip(original, result))
    assert all(i['consume_by_date'] and not i['review_notes'] for i in finalize_registration_items(result))
    path = Path('output/receipt_llm/product_storage_live.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    print('report:', path.resolve())
    if report['gpt_count'] != len(fixtures):
        raise SystemExit('Not all fixtures accepted: inspect sources, never count fallback as GPT.')


if __name__ == '__main__':
    asyncio.run(main())
