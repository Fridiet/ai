"""Exercise real Spring save/cook retries on a synthetic user only.

Leaves real test SAVED/COOKED rows on taste.meat@example.invalid. Never use the
cold-start control account; these test writes are not real-user evaluation data.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen

from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine
from scripts.seed_personalization_demo import PASSWORD


def verify():
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("로컬 DB에서만 실행할 수 있습니다.")
    token = None

    def post(path, payload):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = Request("http://localhost:8080" + path, data=json.dumps(payload).encode(), headers=headers)
        with urlopen(req, timeout=120) as response:
            body = response.read()
            return json.loads(body) if body else None

    email = "taste.meat@example.invalid"
    with engine.connect() as db:
        marker = db.execute(text("SELECT manifest_json FROM ai_demo_seed_manifest WHERE seed_key=:key"),
                            {"key": "taste_demo_v1:" + email}).scalar_one()
        manifest = json.loads(marker)
    login = post("/api/auth/login", {"email": email, "password": PASSWORD})
    assert login["userId"] == manifest["user_id"]
    token = login["token"]
    fid = login["mainFridgeId"]
    recipe = post("/api/recipes/ai/preview", {
        "fridgeId": fid, "targetIngredients": [], "additionalIngredients": [], "excludeIngredients": []})
    assert recipe["recipeKey"]
    save = {k: recipe[k] for k in ("recipeKey", "recipeName", "cookTime", "description", "stepsJson", "ingredients")}
    save["fridgeId"] = fid
    with ThreadPoolExecutor(max_workers=4) as pool:
        saved = list(pool.map(lambda _: post("/api/recipes/ai/save", save), range(4)))
    assert len({r["recipeId"] for r in saved}) == 1, "중복 저장이 발생했습니다."
    event = {"fridgeId": fid, "recipeKey": recipe["recipeKey"], "recipeName": recipe["recipeName"], "eventType": "COOKED"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: post("/api/recipes/ai/feedback", event), range(4)))
    with engine.begin() as db:
        events = db.execute(text("""
            SELECT feedback_id, event_type, event_key FROM recipe_recommendation_feedback
            WHERE user_id=:uid AND recipe_key=:key
        """), {"uid": login["userId"], "key": recipe["recipeKey"]}).mappings().all()
        saved_events = [r for r in events if r["event_type"] == "SAVED"]
        assert len(saved_events) == 1
        cooked_today = db.execute(text("""
            SELECT count(*) FROM recipe_recommendation_feedback
            WHERE user_id=:uid AND recipe_key=:key AND event_type='COOKED'
              AND event_key IS NOT NULL AND DATE(created_at)=:today
        """), {"uid": login["userId"], "key": recipe["recipeKey"],
               "today": datetime.now(ZoneInfo("Asia/Seoul")).date()}).scalar_one()
        assert cooked_today == 1
        manifest["verification_feedback_ids"] = sorted(set(manifest.get("verification_feedback_ids", [])) | {r["feedback_id"] for r in events})
        manifest["verification_recipe_ids"] = sorted(set(manifest.get("verification_recipe_ids", [])) | {saved[0]["recipeId"]})
        db.execute(text("UPDATE ai_demo_seed_manifest SET manifest_json=:data WHERE seed_key=:key"),
                   {"data": json.dumps(manifest), "key": "taste_demo_v1:" + email})
    return {"synthetic_only": True, "recipe_key": recipe["recipeKey"],
            "parallel_save_requests": 4, "distinct_saved_recipe_ids": 1,
            "parallel_cook_requests": 4, "cooked_today": cooked_today,
            "saved_event_count": len(saved_events)}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
