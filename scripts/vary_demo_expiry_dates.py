"""Synthetic dates only: not manufacturer expiry dates or food safety advice."""
import json
import random
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session
from scripts.seed_development_data import _resolve_engine
from scripts.seed_shared_taste_demo import KEY

VERSION = KEY + ':expiry_v2'
OUT = Path(__file__).resolve().parents[1] / 'output/shared_taste_demo'

def demo_days(name, location):
    # Illustrative remaining-day ranges for a staged demo, NOT shelf-life rules.
    if location == 'FROZEN': return (21, 75, '냉동 보관 시연')
    if name in {'고등어','연어','참치','새우','오징어','닭고기'}: return (1, 2, '냉장 생선·닭고기 우선 소비 시연')
    if name in {'소고기','돼지고기'}: return (1, 3, '냉장 육류 우선 소비 시연')
    if name in {'상추','시금치','미나리','콩나물','팽이버섯','느타리버섯','버섯','양송이버섯'}: return (1, 4, '잎채소·콩나물·버섯 시연')
    if name in {'표고버섯','브로콜리','가지','애호박','오이','대파','두부','생크림','화이트크림','우유'}: return (2, 6, '냉장 신선식품 시연')
    if name in {'토마토','파프리카','피망','청양고추','배추'}: return (4, 9, '채소 시연')
    if name in {'당근','무','양배추','비트','감자','양파','마늘','생강','레몬','오렌지'}: return (7, 18, '뿌리채소·통과일 시연')
    if name == '달걀': return (8, 18, '냉장 달걀 시연')
    if name in {'버터','치즈'}: return (10, 25, '유제품 시연')
    if name in {'간장','고추장','고춧가루','소금','후추','맛술','물엿','밀가루','부침가루','설탕','식용유','식초','쌀','오레가노','올리고당','올리브유','와인','참기름','참깨','칠리소스'}:
        return (30, 120, '조미료·건조식품 시연; 제품 표시 확인 필요')
    raise ValueError('Unreviewed demo ingredient: '+name)

def main():
    engine = _resolve_engine(None)
    assert engine.url.host in {'localhost','127.0.0.1','::1'}
    meta=MetaData()
    marker=Table('ai_demo_seed_manifest',meta,autoload_with=engine)
    batch=Table('inventory_batch',meta,autoload_with=engine)
    with Session(engine) as db, db.begin():
        seed=json.loads(db.execute(select(marker.c.manifest_json).where(marker.c.seed_key==KEY).with_for_update()).scalar_one())
        previous=db.execute(select(marker.c.manifest_json).where(marker.c.seed_key==VERSION)).scalar_one_or_none()
        if previous:
            print('Already applied'); return
        replenishment=json.loads((OUT/'replenishment.json').read_text(encoding='utf-8'))
        assert replenishment['fridge_id']==seed['shared_fridge_id']
        ids=seed['inventory_batch_ids']+[x['batch_id'] for x in replenishment['added']]
        rows=list(db.execute(select(batch).where(batch.c.batch_id.in_(ids),batch.c.fridge_id==seed['shared_fridge_id'],batch.c.status=='ACTIVE').with_for_update()).mappings())
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'before_expiry_v2.json').write_text(json.dumps([dict(x) for x in rows],ensure_ascii=False,default=str,indent=2),encoding='utf-8')
        changes=[]
        today=date.today()
        for row in rows:
            lo,hi,reason=demo_days(row['ingredient_name'],row['location_category'])
            days=random.Random(917000+row['batch_id']).randint(lo,hi)
            value=today+timedelta(days=days)
            db.execute(batch.update().where(batch.c.batch_id==row['batch_id']).values(consume_by_date=value,updated_at=datetime.now()))
            changes.append(dict(batch_id=row['batch_id'],name=row['ingredient_name'],location=row['location_category'],old=str(row['consume_by_date']),new=str(value),days=days,reason=reason))
        after=list(db.execute(select(batch).where(batch.c.batch_id.in_([r['batch_id'] for r in rows]))).mappings())
        before_by_id={r['batch_id']:r for r in rows}
        for row in after:
            assert all(row[k]==v for k,v in before_by_id[row['batch_id']].items() if k not in {'consume_by_date','updated_at'})
        summary=dict(synthetic=True,version=VERSION,reference_date=str(today),count=len(changes),distinct_dates=len({x['new'] for x in changes}),
            upcoming_3_days=sum(x['days']<=3 for x in changes),note='시연용 추정 날짜. 구매일/개봉일/제조사 표시기한 미확인. 실제 소비기한으로 사용 금지.')
        db.execute(marker.insert().values(seed_key=VERSION,user_id=seed['accounts'][0]['user_id'],manifest_json=json.dumps(summary,ensure_ascii=False)))
    (OUT/'expiry_v2.json').write_text(json.dumps(dict(summary,changes=changes),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(changes[:15],ensure_ascii=False,indent=2))

if __name__=='__main__': main()
