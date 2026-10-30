"""Create three local-only synthetic personas. Re-running never duplicates the seed.

Usage: python -m scripts.seed_personalization_demo
The sidecar manifest marks synthetic accounts/events for exclusion from real evaluation.
"""
import json
from datetime import datetime, timedelta

import bcrypt
from sqlalchemy import Column, Integer, MetaData, String, Table, Text, select
from sqlalchemy.orm import Session

from scripts.seed_development_data import _resolve_engine, _upsert_fridge_member
from app.team_recipe import _load_recipes


PASSWORD = "local-persona-placeholder"
PERSONAS = (
    ("taste.tofu@example.invalid", "[합성] 두부·달걀 선호", ["두부구이", "우유 달걀찜", "두부 달걀전", "된장 두부국"]),
    ("taste.meat@example.invalid", "[합성] 육류 선호", ["닭고기 채소볶음", "돼지고기 양배추볶음", "소고기 버섯볶음", "닭고기 채소볶음"]),
    ("taste.fish@example.invalid", "[합성] 생선·해산물 선호", ["고등어 무조림", "오징어 채소볶음", "참치 달걀덮밥", "새우 브로콜리볶음"]),
)


def seed():
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("이 스크립트는 로컬 MySQL에만 합성 데이터를 생성합니다.")
    metadata = MetaData()
    marker = Table(
        "ai_demo_seed_manifest", metadata,
        Column("seed_key", String(100), primary_key=True),
        Column("user_id", Integer, nullable=False),
        Column("manifest_json", Text, nullable=False),
    )
    metadata.create_all(engine, tables=[marker])
    tables = {name: Table(name, metadata, autoload_with=engine) for name in (
        "user", "fridge", "inventory_batch", "ingredient_master",
        "recipe_recommendation_feedback", "recipe", "recipe_ingredient",
    )}
    recipes = {r["recipe_name"]: r for r in _load_recipes()}
    selected = [recipes[name] for _, _, names in PERSONAS for name in names]
    inventory_names = sorted({i["canonical_name"] for r in selected for i in r["ingredients"]})
    now = datetime.now().replace(microsecond=0)
    results = []
    with Session(engine) as session, session.begin():
        masters = dict(session.execute(select(tables["ingredient_master"].c.canonical_name, tables["ingredient_master"].c.ingredient_id)).all())
        if set(inventory_names) - masters.keys():
            raise RuntimeError("ingredient_master에 시연 재료가 누락되었습니다.")
        for number, (email, name, favorites) in enumerate(PERSONAS, 1):
            key = f"taste_demo_v1:{email}"
            previous = session.execute(select(marker.c.manifest_json).where(marker.c.seed_key == key)).scalar_one_or_none()
            if previous:
                results.append({**json.loads(previous), "already_seeded": True})
                continue
            user = tables["user"]
            if session.execute(select(user.c.user_id).where(user.c.email == email)).first():
                raise RuntimeError(f"동일 이메일의 기존 계정이 있어 변경하지 않습니다: {email}")
            uid = session.execute(user.insert().values(
                email=email, name=name, password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
                social_provider="LOCAL", health_condition=None, health_goal="GENERAL",
                is_deleted=False, created_at=now, updated_at=now,
            )).inserted_primary_key[0]
            fid = session.execute(tables["fridge"].insert().values(
                name=f"{name} 시연 냉장고", owner_user_id=uid, invite_code=f"TST{number:03}", created_at=now,
            )).inserted_primary_key[0]
            _upsert_fridge_member(session, fridge_id=fid, user_id=uid, role="OWNER")
            session.execute(user.update().where(user.c.user_id == uid).values(main_fridge_id=fid))
            batch_ids = []
            for ingredient in inventory_names:
                pantry = {"소금", "간장", "된장", "고추장", "고춧가루", "참기름", "식용유", "올리브유", "밀가루"}
                location = "ROOM_TEMP" if ingredient in pantry else "REFRIGERATED"
                unit = "개" if ingredient == "달걀" else ("ml" if ingredient in {"우유", "식용유", "참기름", "올리브유", "간장"} else "g")
                batch_ids.append(session.execute(tables["inventory_batch"].insert().values(
                    fridge_id=fid, ingredient_id=masters[ingredient], ingredient_name=ingredient,
                    location_category=location, consume_by_date=(now + timedelta(days=7)).date(),
                    remaining_quantity=10 if unit == "개" else 1000, unit_type="QUANTITY", unit=unit, status="ACTIVE",
                    created_by=uid, created_at=now, updated_at=now,
                )).inserted_primary_key[0])
            event_ids, saved_ids = [], []
            def record(recipe_name, kind, occurred):
                event_ids.append(session.execute(tables["recipe_recommendation_feedback"].insert().values(
                    user_id=uid, fridge_id=fid, recipe_name=recipe_name, event_type=kind, created_at=occurred,
                )).inserted_primary_key[0])
            for offset, favorite in enumerate(dict.fromkeys(favorites)):
                recipe = recipes[favorite]
                occurred = now - timedelta(days=28 - offset)
                rid = session.execute(tables["recipe"].insert().values(
                    owner_user_id=uid, recipe_name=favorite, cook_time=recipe.get("cook_time") or 30,
                    is_generated=True, description=recipe.get("description"), memo="SYNTHETIC:taste_demo_v1",
                    steps_json=json.dumps([{"stepNumber": i, "description": s} for i,s in enumerate(recipe["steps"],1)], ensure_ascii=False),
                    created_at=occurred, updated_at=occurred,
                )).inserted_primary_key[0]
                saved_ids.append(rid)
                for ingredient in recipe["ingredients"]:
                    session.execute(tables["recipe_ingredient"].insert().values(
                        recipe_id=rid, ingredient_id=masters[ingredient["canonical_name"]],
                        name_text=ingredient["canonical_name"], quantity=ingredient["quantity"],
                        unit=ingredient["unit"], optional=ingredient.get("optional",False),
                    ))
                record(favorite, "SAVED", occurred)
            for day in range(1, 21):
                record(favorites[(day - 1) % len(favorites)], "COOKED", now - timedelta(days=day))
            result = dict(email=email, user_id=uid, fridge_id=fid, synthetic=True,
                          seed_version="taste_demo_v1", created_at=now.isoformat(),
                          favorites=list(dict.fromkeys(favorites)), feedback_ids=event_ids,
                          saved_recipe_ids=saved_ids, inventory_batch_ids=batch_ids)
            session.execute(marker.insert().values(seed_key=key, user_id=uid, manifest_json=json.dumps(result, ensure_ascii=False)))
            results.append(result)
    return results


if __name__ == "__main__":
    print(json.dumps(seed(), ensure_ascii=False, indent=2))
