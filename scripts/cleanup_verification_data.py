"""Remove only explicitly marked verification data, with a row backup."""
import json
from datetime import datetime
from pathlib import Path
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine

def main():
    engine = _resolve_engine(None)
    if engine.url.host not in ('127.0.0.1', 'localhost'):
        raise RuntimeError('Local database only')
    def marked(column):
        return f"({column} LIKE '[검증]%' OR {column} LIKE '[수량 검증]%')"
    def ids(values):
        return ','.join(str(int(v)) for v in values) or 'NULL'
    with engine.begin() as c:
        fridge_ids = c.execute(text('SELECT fridge_id FROM fridge WHERE '+marked('name')+' FOR UPDATE')).scalars().all()
        batches = c.execute(text(f'SELECT batch_id FROM inventory_batch WHERE {marked("ingredient_name")} OR fridge_id IN ({ids(fridge_ids)}) FOR UPDATE')).scalars().all()
        meals = c.execute(text(f'SELECT meal_event_id FROM calendar WHERE {marked("title")} OR fridge_id IN ({ids(fridge_ids)}) FOR UPDATE')).scalars().all()
        carts = c.execute(text(f'SELECT list_id FROM cart WHERE {marked("name")} OR fridge_id IN ({ids(fridge_ids)}) FOR UPDATE')).scalars().all()
        # A shared container must not disappear just because it referenced old test stock.
        containers = c.execute(text(f'SELECT container_id FROM container WHERE current_batch_id IN ({ids(batches)}) AND fridge_id NOT IN ({ids(fridge_ids)})')).scalars().all() if fridge_ids else []
        if containers: raise RuntimeError('A non-test container references test inventory; inspect before cleanup')
        if c.execute(text(f'SELECT COUNT(*) FROM user WHERE main_fridge_id IN ({ids(fridge_ids)})')).scalar():
            raise RuntimeError('A verification fridge is a main fridge; inspect before cleanup')
        filters = [
            ('meal_consumption', f'meal_event_id IN ({ids(meals)}) OR batch_id IN ({ids(batches)})'),
            ('inventory_activity', f'batch_id IN ({ids(batches)}) OR fridge_id IN ({ids(fridge_ids)}) OR {marked("ingredient_name")}'),
            ('recipe_recommendation_feedback', f'fridge_id IN ({ids(fridge_ids)}) OR {marked("recipe_name")}'),
            ('cart_item', f'list_id IN ({ids(carts)}) OR purchased_batch_id IN ({ids(batches)}) OR {marked("item_name")}'),
            ('container', f'fridge_id IN ({ids(fridge_ids)})'),
            ('calendar', f'meal_event_id IN ({ids(meals)})'),
            ('inventory_batch', f'batch_id IN ({ids(batches)})'),
            ('cart', f'list_id IN ({ids(carts)})'),
            ('fridge_member', f'fridge_id IN ({ids(fridge_ids)})'),
            ('fridge', f'fridge_id IN ({ids(fridge_ids)})'),
        ]
        backup = {}
        totals = {}
        for table, where in filters:
            totals[table] = c.execute(text(f'SELECT COUNT(*) FROM `{table}`')).scalar()
            backup[table] = [dict(r) for r in c.execute(text(f'SELECT * FROM `{table}` WHERE {where} FOR UPDATE')).mappings()]
        path = Path(__file__).resolve().parents[1]/'output'/('verification_cleanup_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(backup, ensure_ascii=False, indent=2, default=lambda x: {'hex':x.hex()} if isinstance(x,bytes) else str(x)),encoding='utf-8')
        counts = {}
        for table, where in filters:
            counts[table] = c.execute(text(f'DELETE FROM `{table}` WHERE {where}')).rowcount
            assert counts[table] == len(backup[table])
            assert c.execute(text(f'SELECT COUNT(*) FROM `{table}`')).scalar() == totals[table]-counts[table]
        assert c.execute(text('SELECT COUNT(*) FROM fridge WHERE fridge_id=11')).scalar()==1
    print(json.dumps({'backup':str(path),'deleted':counts},ensure_ascii=False))

if __name__=='__main__': main()
