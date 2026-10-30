"""Add stock to the owned synthetic fridge using actual recommendation responses."""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import requests
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session
from scripts.seed_development_data import _resolve_engine
from scripts.seed_shared_taste_demo import KEY

TARGETS = ['고등어','연어','소고기','돼지고기','닭고기','두부','달걀','새우','오징어','참치',
           '브로콜리','감자','김치','가지','시금치','애호박','팽이버섯','표고버섯','청양고추','배추','콩나물','오이']


def main():
    engine = _resolve_engine(None)
    assert engine.url.host in {'localhost','127.0.0.1','::1'}
    meta = MetaData()
    tables = {n:Table(n,meta,autoload_with=engine) for n in ('ai_demo_seed_manifest','inventory_batch','ingredient_master','fridge')}
    batch = tables['inventory_batch']
    with engine.connect() as db:
        manifest = json.loads(db.execute(select(tables['ai_demo_seed_manifest'].c.manifest_json).where(tables['ai_demo_seed_manifest'].c.seed_key==KEY)).scalar_one())
        fid = manifest['shared_fridge_id']
        owner = db.execute(select(tables['fridge'].c.owner_user_id).where(tables['fridge'].c.fridge_id==fid)).scalar_one()
        assert owner == manifest['accounts'][0]['user_id']
        existing = list(db.execute(select(batch).where(batch.c.fridge_id==fid, batch.c.status=='ACTIVE')).mappings())
    inv = [dict(ingredientName=x['ingredient_name'],remainingQuantity=float(x['remaining_quantity']),unit=x['unit'],consumeByDate=str(x['consume_by_date'])) for x in existing]
    recipes, errors = [], []
    required = defaultdict(float)
    for target in TARGETS:
        response = requests.post('http://127.0.0.1:8000/api/recipes/ai',json={'inventories':inv,'targetIngredients':[target]},timeout=120)
        if response.status_code==404:
            errors.append(target)
            continue
        response.raise_for_status()
        recipe = response.json()
        recipes.append(dict(target=target,title=recipe['title'],ingredients=recipe['ingredients'],missing_before=recipe['missingIngredients']))
        for item in recipe['ingredients']:
            # Keep units separate: never guess conversion between pieces and grams.
            unit = item['unit']
            if unit not in {'g','ml','개','kg','l','장'}:
                raise RuntimeError('Unsupported stocking unit: '+unit)
            required[(item['nameText'],unit)] = max(required[(item['nameText'],unit)],float(item['quantity'])*8)
    now = datetime.now().replace(microsecond=0)
    added = []
    with Session(engine) as db, db.begin():
        masters = dict(db.execute(select(tables['ingredient_master'].c.canonical_name,tables['ingredient_master'].c.ingredient_id)).all())
        missing_master = {name for name,unit in required} - masters.keys()
        if missing_master:
            raise RuntimeError('Missing canonical masters: '+str(sorted(missing_master)))
        current = list(db.execute(select(batch).where(batch.c.fridge_id==fid,batch.c.status=='ACTIVE').with_for_update()).mappings())
        for (name,unit), amount in sorted(required.items()):
            have = sum(float(x['remaining_quantity'] or 0) for x in current if x['ingredient_name']==name and x['unit']==unit and x['consume_by_date']>=now.date())
            floor = {'g':1000,'ml':500,'개':10,'kg':1,'l':1,'장':10}[unit]
            shortage = max(amount,floor)-have
            if shortage<=0: continue
            pantry = {'간장','소금','후추','설탕','참기름','식용유','올리브유','고추장','고춧가루','참깨','쌀','밀가루','된장','올리고당','칠리소스','와인'}
            location = 'SEASONING' if name in pantry else ('FROZEN' if name in {'소고기','돼지고기','닭고기','고등어','연어','새우','오징어','참치'} else 'REFRIGERATED')
            bid = db.execute(batch.insert().values(fridge_id=fid,ingredient_id=masters[name],ingredient_name=name,
                remaining_quantity=round(shortage,3),unit=unit,unit_type='QUANTITY',consume_by_date=(now+timedelta(days=7)).date(),
                location_category=location,status='ACTIVE',created_by=owner,created_at=now,updated_at=now)).inserted_primary_key[0]
            added.append(dict(batch_id=bid,name=name,quantity=round(shortage,3),unit=unit))
    result = dict(synthetic=True,fridge_id=fid,created_at=now.isoformat(),recipes=recipes,unsupported_targets=errors,added=added,
                  note='Synthetic replenishment, eight recipe quantities per supported unit. Dates are test values. No history changes or unit conversions.')
    output = Path(__file__).resolve().parents[1]/'output/shared_taste_demo'
    output.mkdir(parents=True,exist_ok=True)
    (output/'replenishment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
