"""Local synthetic comparison: create an unset cold-start account and compare four users."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import bcrypt
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.orm import Session

from scripts.seed_development_data import _resolve_engine, _upsert_fridge_member
from scripts.seed_personalization_demo import PERSONAS, PASSWORD
from scripts.verify_personalization_demo import post
from app.team_recipe import generate_team_recipe
from app.ml.inference import TwoTowerRecommender

COLD_EMAIL = "taste.new@example.invalid"


def create_control(engine):
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("로컬 MySQL 전용입니다.")
    metadata = MetaData()
    tables = {n: Table(n, metadata, autoload_with=engine) for n in (
        "user", "fridge", "inventory_batch", "ai_demo_seed_manifest")}
    user, marker = tables["user"], tables["ai_demo_seed_manifest"]
    key = "taste_demo_v1:" + COLD_EMAIL
    with Session(engine) as session, session.begin():
        previous = session.execute(select(marker.c.manifest_json).where(marker.c.seed_key == key)).scalar_one_or_none()
        if previous:
            return json.loads(previous)
        if session.execute(select(user.c.user_id).where(user.c.email == COLD_EMAIL)).first():
            raise RuntimeError("기존 계정을 덮어쓰지 않습니다.")
        reference = session.execute(select(user.c.main_fridge_id).where(user.c.email == PERSONAS[0][0])).scalar_one()
        now = datetime.now()
        uid = session.execute(user.insert().values(
            email=COLD_EMAIL, name="[합성] 신규 사용자 비교군", social_provider="LOCAL",
            password_hash=bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt()).decode(),
            health_condition=None, health_goal=None, is_deleted=False, created_at=now, updated_at=now,
        )).inserted_primary_key[0]
        fid = reference
        _upsert_fridge_member(session, fridge_id=fid, user_id=uid, role="MEMBER")
        session.execute(user.update().where(user.c.user_id == uid).values(main_fridge_id=fid))
        result = dict(email=COLD_EMAIL, user_id=uid, fridge_id=fid, synthetic=True,
                      seed_version="taste_demo_v1", feedback_ids=[], saved_recipe_ids=[], inventory_batch_ids=[])
        session.execute(marker.insert().values(seed_key=key, user_id=uid, manifest_json=json.dumps(result)))
        return result


def compare():
    engine = _resolve_engine(None)
    control = create_control(engine)
    # All four test users actually share the very same fridge, not copies of its contents.
    shared_fridge = control["fridge_id"]
    with Session(engine) as session, session.begin():
        owner = session.execute(text("SELECT owner_user_id FROM fridge WHERE fridge_id=:fid"), {"fid":shared_fridge}).scalar_one()
        for email in [p[0] for p in PERSONAS] + [COLD_EMAIL]:
            uid = session.execute(text("SELECT user_id FROM `user` WHERE email=:email"), {"email":email}).scalar_one()
            _upsert_fridge_member(session, fridge_id=shared_fridge, user_id=uid, role="OWNER" if uid==owner else "MEMBER")
            session.execute(text("UPDATE `user` SET main_fridge_id=:fid WHERE user_id=:uid"), {"fid":shared_fridge,"uid":uid})
    model = TwoTowerRecommender.load()
    now = datetime.now()
    rows, inventories = [], []
    for email in [p[0] for p in PERSONAS] + [COLD_EMAIL]:
        login = post("/api/auth/login", {"email": email, "password": PASSWORD})
        with engine.connect() as db:
            user = db.execute(text("SELECT user_id, main_fridge_id, health_condition, health_goal FROM `user` WHERE email=:email"), {"email": email}).mappings().one()
            inventory = [dict(x) for x in db.execute(text("SELECT ingredient_name AS ingredientName, consume_by_date AS consumeByDate, remaining_quantity AS remainingQuantity, unit FROM inventory_batch WHERE fridge_id=:fid AND status='ACTIVE' ORDER BY ingredient_name"), {"fid": user["main_fridge_id"]}).mappings()]
            history = [dict(x) for x in db.execute(text("""
                SELECT recipe_key AS recipeKey, recipe_name AS recipeName,
                       event_type AS eventType, created_at AS occurredAt
                FROM recipe_recommendation_feedback
                WHERE user_id=:uid AND created_at>=:cutoff AND (
                    event_type IN ('SAVED', 'COOKED') OR feedback_id IN (
                        SELECT feedback_id FROM (
                            SELECT feedback_id FROM recipe_recommendation_feedback
                            WHERE user_id=:uid AND created_at>=:cutoff
                            ORDER BY created_at DESC LIMIT 50
                        ) recent
                    )
                ) ORDER BY created_at DESC
            """), {"uid": user["user_id"], "cutoff": now.replace(microsecond=0) - timedelta(days=30)}).mappings()]
        inventories.append(inventory)
        positives = [x for x in history if x["eventType"] in {"SAVED", "COOKED"}]
        args = dict(health_condition=user["health_condition"], health_goal=user["health_goal"],
                    active_inventories=[], inventories=inventory, additional_ingredients=[], exclude_ingredients=[], now=now)
        baseline = generate_team_recipe(**args)
        positive_only = generate_team_recipe(**args, recent_feedback=positives)
        full = generate_team_recipe(**args, recent_feedback=history)
        actual = post("/api/recipes/ai/preview", {"fridgeId": user["main_fridge_id"], "targetIngredients": [], "additionalIngredients": [], "excludeIngredients": []}, login["token"])
        assert actual["recipeName"] == full["title"], "로컬 계산과 Spring→AI 결과가 다릅니다."
        prediction_args = dict(inventory_by_name={x["ingredientName"]:[{"consume_by_date":x["consumeByDate"]}] for x in inventory},
                               health_condition=user["health_condition"], health_goal=user["health_goal"], today=now.date(), now=now)
        base_scores = model.predict(**prediction_args)
        taste_scores = model.predict(**prediction_args, recent_feedback=positives)
        unique_names = {x["recipeName"] for x in positives}
        ranks = []
        base_order = sorted(base_scores.scores_by_recipe_id, key=lambda k: -base_scores.scores_by_recipe_id[k].similarity)
        taste_order = sorted(taste_scores.scores_by_recipe_id, key=lambda k: -taste_scores.scores_by_recipe_id[k].similarity)
        for name in sorted(unique_names):
            matches = [r for r in model.recipes if r["recipe_name"] == name]
            if len(matches) != 1:
                continue
            rid = matches[0]["source_recipe_id"]
            ranks.append(dict(recipe=name, baseline_rank=base_order.index(rid)+1, personalized_rank=taste_order.index(rid)+1))
        rows.append(dict(email=email, user_id=user["user_id"], fridge_id=user["main_fridge_id"],
                         health_goal=user["health_goal"], inventory_count=len(inventory),
                         saved_count=sum(x["eventType"]=="SAVED" for x in positives),
                         cooked_count=sum(x["eventType"]=="COOKED" for x in positives),
                         baseline=baseline["title"], positive_only=positive_only["title"], actual=actual["recipeName"],
                         score=actual["recommendationScore"], missing=actual["missingIngredients"],
                         personalization_weight=actual["scoreBreakdown"].get("personalization_weight",0),
                         valid_events=actual["scoreBreakdown"].get("positive_event_count",0), favorite_ranks=ranks))
    assert all(i==inventories[0] for i in inventories)
    assert {r["fridge_id"] for r in rows} == {shared_fridge}
    assert len({r["baseline"] for r in rows})==1
    assert rows[-1]["saved_count"] == rows[-1]["cooked_count"] == 0
    assert rows[-1]["personalization_weight"] == 0
    assert all(r["personalization_weight"]>0 for r in rows[:3])
    assert all(not r["missing"] for r in rows)
    return dict(generated_at=now.isoformat(), synthetic_only=True, control=control,
                identical_inventory=True, shared_fridge_id=shared_fridge, general_or_unset_goal=True, results=rows)


if __name__ == "__main__":
    result = compare()
    output = Path(__file__).resolve().parents[1] / "output" / "personalization_comparison.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
