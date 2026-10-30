"""Link synthetic COOKED events to meals and consumed historical batches.

Local only, transactional and idempotent. Never consumes today's active stock
or adds recommendation feedback. Historical quantities come from recipe data.
"""
import json
from datetime import timedelta
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session
from scripts.seed_development_data import _resolve_engine
from scripts.seed_shared_taste_demo import KEY
from app.team_recipe import _load_recipes

BACKFILL_KEY = KEY + ':calendar_v1'


def backfill():
    engine = _resolve_engine(None)
    if engine.url.host not in {'localhost','127.0.0.1','::1'}:
        raise RuntimeError('Local database only')
    meta = MetaData()
    t = {n:Table(n,meta,autoload_with=engine) for n in (
        'ai_demo_seed_manifest','calendar','meal_consumption','inventory_activity',
        'inventory_batch','ingredient_master','recipe_recommendation_feedback')}
    marker, batch, feedback = t['ai_demo_seed_manifest'],t['inventory_batch'],t['recipe_recommendation_feedback']
    recipes = {f"{r.get('source') or 'LOCAL'}:{r['source_recipe_id']}":r for r in _load_recipes()}
    with Session(engine) as db, db.begin():
        manifest = json.loads(db.execute(select(marker.c.manifest_json).where(marker.c.seed_key==KEY).with_for_update()).scalar_one())
        previous = db.execute(select(marker.c.manifest_json).where(marker.c.seed_key==BACKFILL_KEY)).scalar_one_or_none()
        if previous:
            return dict(json.loads(previous), already_applied=True)
        fid = manifest['shared_fridge_id']
        uids = [a['user_id'] for a in manifest['accounts']]
        def active():
            return db.execute(select(batch).where(batch.c.fridge_id==fid,batch.c.status=='ACTIVE').order_by(batch.c.batch_id)).all()
        def events():
            return db.execute(select(feedback).where(feedback.c.user_id.in_(uids)).order_by(feedback.c.feedback_id)).all()
        before_stock,before_feedback = active(),events()
        masters = dict(db.execute(select(t['ingredient_master'].c.canonical_name,t['ingredient_master'].c.ingredient_id)).all())
        results = []
        for account in manifest['accounts']:
            uid = account['user_id']
            cooked = db.execute(select(feedback).where(feedback.c.user_id==uid,
                feedback.c.feedback_id.in_(account['feedback_ids']),feedback.c.event_type=='COOKED').order_by(feedback.c.created_at)).mappings().all()
            meal_ids, batch_ids, consumption_ids, activity_ids = [], [], [], []
            for event in cooked:
                recipe = recipes[event['recipe_key']]
                assert recipe['recipe_name']==event['recipe_name']
                occurred = event['created_at']
                meal_id = db.execute(t['calendar'].insert().values(fridge_id=fid,owner_user_id=uid,
                    title=event['recipe_name'],meal_type='LUNCH',eaten_at=occurred,created_at=occurred,
                    note='SYNTHETIC:shared_taste_aug2026_v1; 과거 조리 시연 기록',
                    request_key=f"{BACKFILL_KEY}:{event['feedback_id']}")).inserted_primary_key[0]
                meal_ids.append(meal_id)
                for item in recipe['ingredients']:
                    name,amount,unit = item['canonical_name'],float(item['quantity']),item['unit']
                    if amount<=0: raise ValueError('Invalid recipe quantity')
                    bid = db.execute(batch.insert().values(fridge_id=fid,ingredient_id=masters[name],ingredient_name=name,
                        remaining_quantity=0,unit=unit,unit_type='QUANTITY',status='CONSUMED',
                        consume_by_date=(occurred+timedelta(days=7)).date(),location_category='REFRIGERATED',
                        created_by=uid,created_at=occurred-timedelta(hours=1),updated_at=occurred)).inserted_primary_key[0]
                    batch_ids.append(bid)
                    consumption_ids.append(db.execute(t['meal_consumption'].insert().values(meal_event_id=meal_id,
                        batch_id=bid,quantity_used=amount,unit=unit,created_at=occurred)).inserted_primary_key[0])
                    activity_ids.append(db.execute(t['inventory_activity'].insert().values(user_id=uid,fridge_id=fid,
                        batch_id=bid,ingredient_name=name,event_type='CONSUMED',unit_type='QUANTITY',quantity=amount,
                        unit=unit,occurred_at=occurred,custom_text='SYNTHETIC:'+BACKFILL_KEY)).inserted_primary_key[0])
            results.append(dict(email=account['email'],user_id=uid,meal_ids=meal_ids,
                                batch_ids=batch_ids,consumption_ids=consumption_ids,activity_ids=activity_ids))
        assert active()==before_stock, 'Active stock must not change'
        assert events()==before_feedback, 'Recommendation events must not change'
        none_uid = next(a['user_id'] for a in manifest['accounts'] if a['email']=='devnone@test.com')
        assert not db.execute(select(t['calendar'].c.meal_event_id).where(t['calendar'].c.owner_user_id==none_uid)).first()
        result = dict(synthetic=True,seed_key=BACKFILL_KEY,fridge_id=fid,accounts=results,
                      active_stock_unchanged=True,feedback_unchanged=True)
        db.execute(marker.insert().values(seed_key=BACKFILL_KEY,user_id=uids[0],manifest_json=json.dumps(result)))
        return result


if __name__=='__main__':
    result=backfill()
    print(json.dumps(dict(already_applied=result.get('already_applied',False),
        active_stock_unchanged=result['active_stock_unchanged'],feedback_unchanged=result['feedback_unchanged'],
        accounts=[dict(email=a['email'],meals=len(a['meal_ids']),consumptions=len(a['consumption_ids']))
                  for a in result['accounts']]),ensure_ascii=False,indent=2))
