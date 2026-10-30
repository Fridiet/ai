"""Verify synthetic accounts through Spring -> AI, without exposing login tokens.

The preview endpoint creates SHOWN records as part of normal service behavior.
"""
import json
from urllib.request import Request, urlopen

from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine
from scripts.seed_personalization_demo import PERSONAS, PASSWORD
from app.team_recipe import generate_team_recipe


def post(path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request("http://localhost:8080" + path, data=json.dumps(payload).encode(), headers=headers)
    with urlopen(request, timeout=120) as response:
        return json.load(response)


def verify():
    engine = _resolve_engine(None)
    results = []
    snapshots = []
    for email, _, _ in PERSONAS:
        login = post("/api/auth/login", {"email": email, "password": PASSWORD})
        fridge_id = login["mainFridgeId"]
        with engine.connect() as db:
            inventory = db.execute(text("SELECT ingredient_name AS ingredientName, consume_by_date AS consumeByDate, remaining_quantity AS remainingQuantity, unit FROM inventory_batch WHERE fridge_id=:fid AND status='ACTIVE' ORDER BY ingredient_name"), {"fid": fridge_id}).mappings().all()
        snapshots.append([dict(row) for row in inventory])
        baseline = generate_team_recipe(
            health_condition=None, health_goal="GENERAL", active_inventories=[],
            inventories=snapshots[-1], additional_ingredients=[], exclude_ingredients=[],
        )
        personalized = post("/api/recipes/ai/preview", {
            "fridgeId": fridge_id, "targetIngredients": [],
            "additionalIngredients": [], "excludeIngredients": [],
        }, login["token"])
        breakdown = personalized["scoreBreakdown"]
        assert breakdown["personalization_weight"] > 0
        results.append({
            "email": email, "fridge_id": fridge_id,
            "without_history": baseline["title"], "with_history": personalized["recipeName"],
            "score": personalized["recommendationScore"], "breakdown": breakdown,
            "reasons": personalized["recommendationReasons"],
        })
    assert snapshots[0] == snapshots[1] == snapshots[2], "비교용 재고 조건이 다릅니다."
    assert len({r["without_history"] for r in results}) == 1
    assert len({r["with_history"] for r in results}) == 3, "사용자별 추천 차이를 확인하지 못했습니다."
    assert "두부" in results[0]["with_history"]
    assert any(name in results[1]["with_history"] for name in ("닭", "돼지", "소고기"))
    assert any(name in results[2]["with_history"] for name in ("참치", "새우", "오징어", "고등어", "생선"))
    return {"synthetic_demo_only": True, "same_inventory_and_goal": True, "results": results}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
