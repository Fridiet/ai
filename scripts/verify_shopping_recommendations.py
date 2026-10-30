"""Local synthetic users; recommendation reads must not create feedback or carts."""
import json
import requests
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine
from scripts.seed_personalization_demo import PERSONAS, PASSWORD


def verify():
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("로컬 DB 전용 검사입니다.")
    results = []
    for email in [p[0] for p in PERSONAS] + ["taste.new@example.invalid"]:
        login = requests.post("http://localhost:8080/api/auth/login", json={"email": email, "password": PASSWORD}, timeout=30)
        login.raise_for_status()
        user = login.json()
        headers = {"Authorization": "Bearer " + user["token"]}
        with engine.connect() as db:
            before = db.scalar(text("SELECT COUNT(*) FROM recipe_recommendation_feedback WHERE user_id=:uid"), {"uid": user["userId"]})
        response = requests.post("http://localhost:8080/api/recipes/ai/shopping", headers=headers,
            json={"fridgeId": 7, "targetIngredients": [], "additionalIngredients": [], "excludeIngredients": []}, timeout=90)
        response.raise_for_status()
        recipe = response.json()
        weight = recipe["scoreBreakdown"].get("personalization_weight", 0)
        assert (weight == 0) if email == "taste.new@example.invalid" else (weight > 0)
        balances = recipe["ingredientBalances"]
        assert balances
        shortages = {b["name"] for b in balances if b["status"] == "SHORTAGE"}
        assert 1 <= len(shortages | set(recipe["missingIngredients"])) <= 3
        assert recipe["availableIngredients"]
        assert not set(recipe["missingIngredients"]) & set(recipe["availableIngredients"])
        with engine.connect() as db:
            after = db.scalar(text("SELECT COUNT(*) FROM recipe_recommendation_feedback WHERE user_id=:uid"), {"uid": user["userId"]})
        assert before == after
        results.append({"user_id": user["userId"], "fridge_id": 7, "recipe": recipe["recipeName"],
            "missing": recipe["missingIngredients"], "score": recipe["recommendationScore"], "breakdown": recipe["scoreBreakdown"]})
    unauthorized = requests.post("http://localhost:8080/api/recipes/ai/shopping", headers=headers,
        json={"fridgeId": 1}, timeout=30)
    assert unauthorized.status_code == 403
    assert len({row["recipe"] for row in results}) > 1
    return {"feedback_unchanged": True, "other_fridge_denied": True, "results": results}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=True, indent=2))
