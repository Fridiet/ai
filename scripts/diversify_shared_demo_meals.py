"""Replace only manifest-owned synthetic history with real API menu selections."""
import json
from datetime import datetime, timedelta
from pathlib import Path
import requests
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session
from scripts.seed_development_data import _resolve_engine
from scripts.seed_shared_taste_demo import KEY
from scripts.backfill_shared_demo_meals import BACKFILL_KEY

VERSION = KEY + ':varied_api_v1'

def main():
    engine = _resolve_engine(None)
    assert engine.url.host in {'localhost', '127.0.0.1', '::1'}
    meta = MetaData()
    tables = {n: Table(n, meta, autoload_with=engine) for n in
              ('ai_demo_seed_manifest', 'calendar', 'meal_consumption', 'inventory_activity',
               'inventory_batch', 'ingredient_master', 'recipe_recommendation_feedback')}
    marker, batch, feedback = [tables[n] for n in
        ('ai_demo_seed_manifest', 'inventory_batch', 'recipe_recommendation_feedback')]
    with engine.connect() as db:
        if db.execute(select(marker.c.seed_key).where(marker.c.seed_key == VERSION)).first():
            print('Already applied'); return
        seed = json.loads(db.execute(select(marker.c.manifest_json).where(marker.c.seed_key == KEY)).scalar_one())
        history = json.loads(db.execute(select(marker.c.manifest_json).where(marker.c.seed_key == BACKFILL_KEY)).scalar_one())
        stock = list(db.execute(select(batch).where(batch.c.fridge_id == seed['shared_fridge_id'], batch.c.status == 'ACTIVE')).mappings())
    inv = [dict(ingredientName=x['ingredient_name'], remainingQuantity=float(x['remaining_quantity']),
                unit=x['unit'], consumeByDate=str(x['consume_by_date'])) for x in stock]
    pools = {}
    for email, proteins in {'devmeat@test.com': ['소고기', '돼지고기', '닭고기'],
                            'devfish@test.com': ['고등어', '연어', '새우', '오징어', '참치']}.items():
        found = {}
        for side in [None, '양파', '감자', '당근', '대파', '달걀', '양배추', '쌀', '브로콜리', '두부']:
            for protein in proteins:
                r = requests.post('http://127.0.0.1:8000/api/recipes/ai',
                    json={'inventories': inv, 'targetIngredients': [protein] + ([side] if side else [])}, timeout=120)
                if r.status_code == 404: continue
                r.raise_for_status()
                item = r.json()
                found[item['recipeKey']] = item
            if len(found) >= 12: break
        if len(found) < 8:
            raise RuntimeError(f'Not enough actual API menus: {email}, {len(found)}')
        pools[email] = list(found.values())
        print(email, len(found), [x['title'] for x in found.values()], flush=True)
    output = Path(__file__).resolve().parents[1] / 'output/shared_taste_demo'
    output.mkdir(parents=True, exist_ok=True)
    with Session(engine) as db, db.begin():
        db.execute(select(marker).where(marker.c.seed_key == KEY).with_for_update()).one()
        assert not db.execute(select(marker.c.seed_key).where(marker.c.seed_key == VERSION)).first()
        before = db.execute(select(batch).where(batch.c.fridge_id == seed['shared_fridge_id'], batch.c.status == 'ACTIVE').order_by(batch.c.batch_id)).all()
        masters = dict(db.execute(select(tables['ingredient_master'].c.canonical_name, tables['ingredient_master'].c.ingredient_id)).all())
        backups = {}
        for name, field in [('calendar','meal_ids'), ('meal_consumption','consumption_ids'), ('inventory_activity','activity_ids'), ('inventory_batch','batch_ids')]:
            ids = [i for a in history['accounts'] for i in a[field]]
            table = tables[name]
            backups[name] = [dict(x) for x in db.execute(select(table).where(list(table.primary_key)[0].in_(ids))).mappings()]
        ids = [i for a in seed['accounts'] for i in a['feedback_ids']]
        backups['feedback'] = [dict(x) for x in db.execute(select(feedback).where(feedback.c.feedback_id.in_(ids))).mappings()]
        backups['seed_manifest'], backups['calendar_manifest'] = seed, history
        (output/'before_varied_history.json').write_text(json.dumps(backups, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        for account in seed['accounts']:
            if account['email'] not in pools: continue
            pool = pools[account['email']]
            hist = next(x for x in history['accounts'] if x['user_id'] == account['user_id'])
            for name, field in [('meal_consumption','consumption_ids'), ('inventory_activity','activity_ids'), ('inventory_batch','batch_ids')]:
                table = tables[name]
                if name == 'inventory_batch':
                    assert all(x == 'CONSUMED' for x in db.execute(select(batch.c.status).where(batch.c.batch_id.in_(hist[field]))).scalars())
                db.execute(table.delete().where(list(table.primary_key)[0].in_(hist[field])))
                hist[field] = []
            events = db.execute(select(feedback).where(feedback.c.feedback_id.in_(account['feedback_ids'])).order_by(feedback.c.created_at, feedback.c.feedback_id)).mappings().all()
            cooked_index = 0
            for index, event in enumerate(events):
                recipe = pool[(cooked_index if event['event_type'] == 'COOKED' else index) % len(pool)]
                db.execute(feedback.update().where(feedback.c.feedback_id == event['feedback_id']).values(recipe_name=recipe['title'], recipe_key=recipe['recipeKey']))
                if event['event_type'] != 'COOKED': continue
                cooked_index += 1
                occurred = event['created_at']
                cal = tables['calendar']
                mid = db.execute(select(cal.c.meal_event_id).where(cal.c.request_key == f"{BACKFILL_KEY}:{event['feedback_id']}", cal.c.meal_event_id.in_(hist['meal_ids']))).scalar_one()
                db.execute(cal.update().where(cal.c.meal_event_id == mid).values(title=recipe['title']))
                for ingredient in recipe['ingredients']:
                    name, quantity, unit = ingredient['nameText'], ingredient['quantity'], ingredient['unit']
                    bid = db.execute(batch.insert().values(fridge_id=seed['shared_fridge_id'], ingredient_id=masters[name], ingredient_name=name,
                        remaining_quantity=0, unit=unit, unit_type='QUANTITY', status='CONSUMED', consume_by_date=(occurred+timedelta(days=7)).date(),
                        location_category='REFRIGERATED', created_by=account['user_id'], created_at=occurred-timedelta(hours=1), updated_at=occurred)).inserted_primary_key[0]
                    hist['batch_ids'].append(bid)
                    hist['consumption_ids'].append(db.execute(tables['meal_consumption'].insert().values(meal_event_id=mid, batch_id=bid, quantity_used=quantity, unit=unit, created_at=occurred)).inserted_primary_key[0])
                    hist['activity_ids'].append(db.execute(tables['inventory_activity'].insert().values(user_id=account['user_id'], fridge_id=seed['shared_fridge_id'], batch_id=bid,
                        ingredient_name=name, event_type='CONSUMED', unit_type='QUANTITY', quantity=quantity, unit=unit, occurred_at=occurred, custom_text='SYNTHETIC:'+VERSION)).inserted_primary_key[0])
            account['favorites'] = [x['title'] for x in pool]
        after = db.execute(select(batch).where(batch.c.fridge_id == seed['shared_fridge_id'], batch.c.status == 'ACTIVE').order_by(batch.c.batch_id)).all()
        assert before == after
        for key, data in [(KEY,seed), (BACKFILL_KEY,history)]:
            db.execute(marker.update().where(marker.c.seed_key == key).values(manifest_json=json.dumps(data, ensure_ascii=False)))
        result = dict(synthetic=True, version=VERSION, menus=pools, active_stock_unchanged=True)
        compact = dict(result, menus={email: [dict(recipeKey=r['recipeKey'], title=r['title']) for r in recipes] for email, recipes in pools.items()})
        db.execute(marker.insert().values(seed_key=VERSION, user_id=seed['accounts'][0]['user_id'], manifest_json=json.dumps(compact, ensure_ascii=False)))
    (output/'varied_history.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Applied: existing synthetic meal dates/counts preserved; active stock unchanged.')

if __name__ == '__main__':
    main()
