"""Local, manifest-scoped synthetic meal schedule; not real eating behavior."""
import json
import random
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session
from app.team_recipe import _load_recipes
from scripts.seed_development_data import _resolve_engine
from scripts.seed_shared_taste_demo import KEY
from scripts.backfill_shared_demo_meals import BACKFILL_KEY

VERSION = KEY + ':meal_schedule_v2'
OUT = Path(__file__).resolve().parents[1] / 'output/shared_taste_demo'
BREAKFAST = {
    'devmeat@test.com': ['찬밥이용닭죽', '귀리죽', '삼색샌드위치', '아보카도 달걀 샌드위치', '고구마 타락죽'],
    'devfish@test.com': ['홍합죽', '전복죽', '참치두부 주먹밥', '삼색샌드위치', '고구마 타락죽'],
}

def build_schedule(email, pool, catalog, now):
    rng = random.Random(91710 if 'meat' in email else 91711)
    breakfast = [catalog[name] for name in BREAKFAST[email]]
    lunch = [r for r in pool if any(w in r['recipe_name'] for w in ('밥', '볶음', '말이', '롤', '전'))]
    dinner = [r for r in pool if r not in lunch] + [r for r in pool if '볶음' in r['recipe_name']]
    assert len(lunch) >= 3 and len(dinner) >= 3
    schedule, recent = [], []
    day = datetime(2026, 8, 1)
    while day.date() <= now.date():
        weekend = day.weekday() >= 5
        # A day without an app record is not evidence that the person ate nothing.
        if rng.random() >= .06:
            for kind, hour, chance, choices in [
                ('BREAKFAST', 9 if weekend else 7, .78 if weekend else .55, breakfast),
                ('LUNCH', 13 if weekend else 12, .82, lunch),
                ('DINNER', 19, .90, dinner),
            ]:
                if rng.random() > chance: continue
                occurred = day.replace(hour=hour, minute=rng.choice([5, 15, 25, 35, 45]))
                if occurred > now: continue
                candidates = [r for r in choices if r['recipe_name'] not in recent[-4:]] or choices
                recipe = rng.choice(candidates)
                schedule.append((occurred, kind, recipe))
                recent.append(recipe['recipe_name'])
        day += timedelta(days=1)
    assert set(k for _, k, _ in schedule) == {'BREAKFAST', 'LUNCH', 'DINNER'}
    assert len({d.date() for d, _, _ in schedule}) < (now.date()-datetime(2026,8,1).date()).days+1
    return schedule

def main():
    engine = _resolve_engine(None)
    assert engine.url.host in {'localhost', '127.0.0.1', '::1'}
    meta = MetaData()
    t = {n: Table(n, meta, autoload_with=engine) for n in ('ai_demo_seed_manifest', 'calendar',
        'meal_consumption', 'inventory_activity', 'inventory_batch', 'ingredient_master', 'recipe_recommendation_feedback')}
    marker, batch, feedback = t['ai_demo_seed_manifest'], t['inventory_batch'], t['recipe_recommendation_feedback']
    recipes = _load_recipes()
    catalog = {r['recipe_name']:r for r in recipes}
    bykey = {f"{r.get('source') or 'LOCAL'}:{r['source_recipe_id']}":r for r in recipes}
    now = datetime.now().replace(microsecond=0)
    with Session(engine) as db, db.begin():
        seed = json.loads(db.execute(select(marker.c.manifest_json).where(marker.c.seed_key == KEY).with_for_update()).scalar_one())
        existing = db.execute(select(marker.c.manifest_json).where(marker.c.seed_key == VERSION)).scalar_one_or_none()
        if existing:
            print(existing); return
        hist = json.loads(db.execute(select(marker.c.manifest_json).where(marker.c.seed_key == BACKFILL_KEY)).scalar_one())
        menus = json.loads(db.execute(select(marker.c.manifest_json).where(marker.c.seed_key == KEY+':varied_api_v1')).scalar_one())['menus']
        plans = {email:build_schedule(email, [bykey[r['recipeKey']] for r in pool], catalog, now) for email,pool in menus.items()}
        masters = dict(db.execute(select(t['ingredient_master'].c.canonical_name,t['ingredient_master'].c.ingredient_id)).all())
        assert {i['canonical_name'] for plan in plans.values() for _,_,r in plan for i in r['ingredients']} <= masters.keys()
        def stock():
            return db.execute(select(batch).where(batch.c.fridge_id==seed['shared_fridge_id'], batch.c.status=='ACTIVE').order_by(batch.c.batch_id)).all()
        before = stock()
        backup = {'seed':seed, 'history':hist}
        for name,field in [('meal_consumption','consumption_ids'),('inventory_activity','activity_ids'),('inventory_batch','batch_ids'),('calendar','meal_ids')]:
            table = t[name]
            ids = [i for a in hist['accounts'] for i in a[field]]
            backup[name] = [dict(r) for r in db.execute(select(table).where(list(table.primary_key)[0].in_(ids))).mappings()]
        backup['feedback'] = [dict(r) for r in db.execute(select(feedback).where(feedback.c.feedback_id.in_([i for a in seed['accounts'] for i in a['feedback_ids']]))).mappings()]
        OUT.mkdir(parents=True,exist_ok=True)
        (OUT/'before_meal_schedule_v2.json').write_text(json.dumps(backup,ensure_ascii=False,default=str,indent=2),encoding='utf-8')
        summaries = []
        for account in seed['accounts']:
            email, uid = account['email'], account['user_id']
            if email not in plans: continue
            owned = next(a for a in hist['accounts'] if a['user_id']==uid)
            for name,field in [('meal_consumption','consumption_ids'),('inventory_activity','activity_ids'),('calendar','meal_ids'),('inventory_batch','batch_ids')]:
                table=t[name]
                if name=='inventory_batch':
                    assert all(s=='CONSUMED' for s in db.execute(select(batch.c.status).where(batch.c.batch_id.in_(owned[field]))).scalars())
                db.execute(table.delete().where(list(table.primary_key)[0].in_(owned[field])))
                owned[field]=[]
            old_cooked = list(db.execute(select(feedback.c.feedback_id).where(feedback.c.feedback_id.in_(account['feedback_ids']),feedback.c.event_type=='COOKED')).scalars())
            db.execute(feedback.delete().where(feedback.c.feedback_id.in_(old_cooked)))
            account['feedback_ids']=[i for i in account['feedback_ids'] if i not in old_cooked]
            for occurred,kind,r in plans[email]:
                rk=f"{r.get('source') or 'LOCAL'}:{r['source_recipe_id']}"
                eid=db.execute(feedback.insert().values(user_id=uid,fridge_id=seed['shared_fridge_id'],recipe_name=r['recipe_name'],recipe_key=rk,event_type='COOKED',created_at=occurred)).inserted_primary_key[0]
                account['feedback_ids'].append(eid)
                mid=db.execute(t['calendar'].insert().values(fridge_id=seed['shared_fridge_id'],owner_user_id=uid,title=r['recipe_name'],meal_type=kind,
                    eaten_at=occurred,created_at=occurred,note='SYNTHETIC: 시간대별 식사 시연; 기록 없는 끼니는 미기록/외식/거름을 구분하지 않음',request_key=f'{BACKFILL_KEY}:{eid}')).inserted_primary_key[0]
                owned['meal_ids'].append(mid)
                for i in r['ingredients']:
                    name,qty,unit=i['canonical_name'],float(i['quantity']),i['unit']
                    bid=db.execute(batch.insert().values(fridge_id=seed['shared_fridge_id'],ingredient_id=masters[name],ingredient_name=name,remaining_quantity=0,
                        unit=unit,unit_type='QUANTITY',status='CONSUMED',consume_by_date=(occurred+timedelta(days=7)).date(),location_category='REFRIGERATED',
                        created_by=uid,created_at=occurred-timedelta(hours=1),updated_at=occurred)).inserted_primary_key[0]
                    owned['batch_ids'].append(bid)
                    owned['consumption_ids'].append(db.execute(t['meal_consumption'].insert().values(meal_event_id=mid,batch_id=bid,quantity_used=qty,unit=unit,created_at=occurred)).inserted_primary_key[0])
                    owned['activity_ids'].append(db.execute(t['inventory_activity'].insert().values(user_id=uid,fridge_id=seed['shared_fridge_id'],batch_id=bid,ingredient_name=name,
                        event_type='CONSUMED',unit_type='QUANTITY',quantity=qty,unit=unit,occurred_at=occurred,custom_text='SYNTHETIC:'+VERSION)).inserted_primary_key[0])
            account['cooked_count']=len(plans[email])
            counts=Counter(k for _,k,_ in plans[email])
            days=Counter(d.date().isoformat() for d,_,_ in plans[email])
            summaries.append(dict(email=email,meals=len(plans[email]),types=dict(counts),unique_menus=len({r['recipe_name'] for _,_,r in plans[email]}),
                days_by_meal_count=dict(Counter(days.values())),unrecorded_days=(now.date()-datetime(2026,8,1).date()).days+1-len(days)))
        assert stock()==before
        for key,data in [(KEY,seed),(BACKFILL_KEY,hist)]:
            db.execute(marker.update().where(marker.c.seed_key==key).values(manifest_json=json.dumps(data,ensure_ascii=False,separators=(',',':'))))
        result=dict(synthetic=True,version=VERSION,accounts=summaries,active_stock_unchanged=True)
        db.execute(marker.insert().values(seed_key=VERSION,user_id=seed['accounts'][0]['user_id'],manifest_json=json.dumps(result,ensure_ascii=False)))
    (OUT/'meal_schedule_v2.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
