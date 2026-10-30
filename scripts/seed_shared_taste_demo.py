"""Local synthetic shared-fridge experiment; never modifies pre-existing accounts."""
import json
from datetime import datetime, timedelta
from pathlib import Path
import bcrypt
from sqlalchemy import MetaData, Table, select
from sqlalchemy.orm import Session
from scripts.seed_development_data import _resolve_engine, _upsert_fridge_member
from app.team_recipe import _load_recipes

KEY = 'shared_taste_aug2026_v1'
PERSONAS = {
    'devmeat@test.com': ['닭고기 채소볶음', '돼지고기 양배추볶음', '소고기 버섯볶음'],
    'devfish@test.com': ['고등어 무조림', '연어 채소샐러드', '연어오븐구이'],
    'devnone@test.com': [],
}


def seed():
    engine = _resolve_engine(None)
    if engine.url.host not in {'localhost', '127.0.0.1', '::1'}:
        raise RuntimeError('Local MySQL only')
    metadata = MetaData()
    tables = {n: Table(n, metadata, autoload_with=engine) for n in (
        'user', 'fridge', 'inventory_batch', 'ingredient_master',
        'recipe_recommendation_feedback', 'ai_demo_seed_manifest')}
    user, marker = tables['user'], tables['ai_demo_seed_manifest']
    recipes = {r['recipe_name']: r for r in _load_recipes()}
    names = sorted({i['canonical_name'] for favorites in PERSONAS.values()
                    for name in favorites for i in recipes[name]['ingredients']}
                   | {'두부', '달걀', '우유', '양파', '당근', '감자', '대파'})
    now = datetime.now().replace(microsecond=0)
    start = datetime(2026, 8, 1, 12)
    if now <= start:
        raise RuntimeError('Experiment must be after August 1, 2026')
    results = []
    with Session(engine) as session, session.begin():
        previous = session.execute(select(marker.c.manifest_json).where(marker.c.seed_key == KEY)).scalar_one_or_none()
        if previous:
            return json.loads(previous)
        if session.execute(select(user.c.user_id).where(user.c.email.in_(PERSONAS))).first():
            raise RuntimeError('Existing account collision: will not overwrite')
        masters = dict(session.execute(select(tables['ingredient_master'].c.canonical_name,
                                               tables['ingredient_master'].c.ingredient_id)).all())
        missing = set(names) - masters.keys()
        if missing:
            raise RuntimeError('Missing ingredient masters: ' + ', '.join(sorted(missing)))
        ids = {}
        for email in PERSONAS:
            ids[email] = session.execute(user.insert().values(
                email=email, name='[합성] ' + email.split('@')[0],
                password_hash=bcrypt.hashpw(b'local-demo-placeholder', bcrypt.gensalt()).decode(),
                social_provider='LOCAL', health_condition=None, health_goal=None,
                is_deleted=False, created_at=start, updated_at=now)).inserted_primary_key[0]
        owner = ids['devmeat@test.com']
        fid = session.execute(tables['fridge'].insert().values(
            name='[합성] 동일 재고 개인화 비교', owner_user_id=owner,
            invite_code='TST917', created_at=start)).inserted_primary_key[0]
        for email, uid in ids.items():
            _upsert_fridge_member(session, fridge_id=fid, user_id=uid,
                                  role='OWNER' if uid == owner else 'MEMBER')
            session.execute(user.update().where(user.c.user_id == uid).values(main_fridge_id=fid))
        batch_ids = []
        for name in names:
            unit = '개' if name == '달걀' else ('ml' if name in {'우유', '간장', '식용유', '올리브유', '참기름'} else 'g')
            batch_ids.append(session.execute(tables['inventory_batch'].insert().values(
                fridge_id=fid, ingredient_id=masters[name], ingredient_name=name,
                remaining_quantity=30 if unit == '개' else 2000, unit=unit, unit_type='QUANTITY',
                consume_by_date=(now + timedelta(days=7)).date(), location_category='REFRIGERATED',
                status='ACTIVE', created_by=owner, created_at=now, updated_at=now)).inserted_primary_key[0])
        for email, favorites in PERSONAS.items():
            event_ids = []
            events = [(start + timedelta(days=i), name, 'SAVED') for i, name in enumerate(favorites)]
            day = start
            while favorites and day < now:
                index = (day - start).days
                events.append((day, favorites[index % len(favorites)], 'COOKED'))
                day += timedelta(days=1)
            for occurred, name, kind in events:
                recipe = recipes[name]
                event_ids.append(session.execute(tables['recipe_recommendation_feedback'].insert().values(
                    user_id=ids[email], fridge_id=fid, recipe_name=name,
                    recipe_key=f"{recipe.get('source') or 'LOCAL'}:{recipe['source_recipe_id']}",
                    event_type=kind, created_at=occurred)).inserted_primary_key[0])
            results.append(dict(email=email, user_id=ids[email], fridge_id=fid,
                                favorites=favorites, feedback_ids=event_ids,
                                saved_count=len(favorites), cooked_count=len(events)-len(favorites)))
        result = dict(synthetic=True, seed_key=KEY, created_at=now.isoformat(),
                      history_start=start.isoformat(), shared_fridge_id=fid,
                      inventory_names=names, inventory_batch_ids=batch_ids, accounts=results,
                      caveat='Synthetic demo only; inventory dates/storage are controlled test values, not food safety guidance.')
        session.execute(marker.insert().values(seed_key=KEY, user_id=owner,
                                               manifest_json=json.dumps(result, ensure_ascii=False)))
    return result


if __name__ == '__main__':
    result = seed()
    print(json.dumps(result, ensure_ascii=False, indent=2))
