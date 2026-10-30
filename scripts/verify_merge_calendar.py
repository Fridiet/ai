"""Opt-in localhost HTTP/MySQL merge regression; removes only its own fixture."""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal

import requests
from sqlalchemy import text

from scripts.seed_development_data import _resolve_engine


def main():
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("Local database required")
    base = "http://127.0.0.1:8080"
    session = requests.Session()
    login = session.post(base + "/api/auth/login", json={
        "email": "devmeat@test.com", "password": os.environ["DEMO_PASSWORD"],
    }, timeout=15)
    login.raise_for_status()
    auth = login.json()
    assert auth["mainFridgeId"] is not None, "Do not change an unset main fridge"
    session.headers["Authorization"] = "Bearer " + auth["token"]

    def api(method, path, body=None, expected=None):
        response = session.request(method, base + path, json=body, timeout=90)
        if expected is not None:
            assert response.status_code == expected, (method, path, response.status_code)
        else:
            assert response.ok, (method, path, response.status_code)
        return response.json() if response.content and response.ok else None

    name = "임시 병합 회귀 검증 " + uuid.uuid4().hex
    fixture = api("POST", "/api/fridges", {"name": name})
    fridge_id = fixture.get("id", fixture.get("fridgeId"))
    assert isinstance(fridge_id, int)
    print("Temporary fixture fridge:", fridge_id, flush=True)
    try:
        stock = api("POST", "/api/inventories", {
            "fridgeId": fridge_id, "ingredientName": "양파", "unitType": "QUANTITY",
            "remainingQuantity": 500, "unit": "g", "locationCategory": "REFRIGERATED",
            "consumeByDate": (date.today() + timedelta(days=7)).isoformat(),
        })
        bid = stock["batchId"]

        def remaining():
            return Decimal(str(api("GET", f"/api/inventories/{bid}")["remainingQuantity"]))

        def meal(amount):
            return {
                "requestId": uuid.uuid4().hex, "fridgeId": fridge_id,
                "title": name, "mealType": "DINNER",
                "usedIngredients": [{"batchId": bid, "unitType": "QUANTITY",
                                     "quantityUsed": amount, "unit": "g"}],
            }

        body = meal(100)
        first = api("POST", "/api/calendar/meals", body)
        mid = first["mealEventId"]
        assert api("POST", "/api/calendar/meals", body)["mealEventId"] == mid
        assert remaining() == 400
        print("PASS create/retry: one deduction", flush=True)
        update = {"title": name, "mealType": "BREAKFAST", "usedIngredients": meal(150)["usedIngredients"]}
        api("PUT", f"/api/calendar/{mid}", update)
        assert remaining() == 350
        invalid = {**update, "usedIngredients": meal(501)["usedIngredients"]}
        api("PUT", f"/api/calendar/{mid}", invalid, expected=400)
        assert remaining() == 350
        print("PASS replace usage and reject excess without mutation", flush=True)
        api("DELETE", f"/api/calendar/{mid}")
        api("DELETE", f"/api/calendar/{mid}")
        assert remaining() == 500
        api("POST", "/api/calendar/meals", body, expected=409)
        assert remaining() == 500
        print("PASS delete/refund/repeat and tombstone retry", flush=True)
        bodies = [meal(300), meal(300)]
        def submit(payload):
            return requests.post(base + "/api/calendar/meals", headers=dict(session.headers),
                                 json=payload, timeout=45).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = list(pool.map(submit, bodies))
        assert sorted(codes) == [201, 400], codes
        assert remaining() == 200
        print("PASS concurrent overdraw blocked on MySQL", flush=True)
        with engine.connect() as conn:
            active = conn.execute(text("SELECT COUNT(*) FROM inventory_activity WHERE fridge_id=:id "
                                       "AND event_type='CONSUMED' AND cancelled_at IS NULL"), {"id": fridge_id}).scalar()
            assert active == 1, active
        print("PASS cancelled activity excluded; one active consumption", flush=True)
        recipe = api("POST", "/api/recipes/ai/preview", {
            "fridgeId": fridge_id, "targetIngredients": ["양파"],
            "additionalIngredients": [], "excludeIngredients": [],
        })
        assert recipe["recipeKey"] and recipe["ingredients"]
        preview = api("POST", "/api/recipes/ai/consumption-preview", {
            "fridgeId": fridge_id, "ingredients": recipe["ingredients"],
        })
        used = [{"batchId": row["stock"]["batchId"], "unitType": "QUANTITY",
                 "quantityUsed": row["quantityUsed"], "unit": row["stock"]["unit"]}
                for row in preview["items"] if row["quantityUsed"] > 0]
        assert used, "Recommendation must have at least one compatible available ingredient"
        before = remaining()
        def cooked_count():
            with engine.connect() as conn:
                return conn.execute(text("SELECT COUNT(*) FROM recipe_recommendation_feedback "
                                         "WHERE fridge_id=:id AND event_type='COOKED'"),
                                    {"id": fridge_id}).scalar()
        count_before = cooked_count()
        api("POST", "/api/recipes/ai/complete", {
            "recipeKey": recipe["recipeKey"], "meal": meal(201),
        }, expected=400)
        assert remaining() == before and cooked_count() == count_before
        completion = {"recipeKey": recipe["recipeKey"], "meal": {
            **meal(1), "title": recipe["recipeName"], "usedIngredients": used,
        }}
        done = api("POST", "/api/recipes/ai/complete", completion)
        assert api("POST", "/api/recipes/ai/complete", completion)["mealEventId"] == done["mealEventId"]
        assert remaining() == before - sum(Decimal(str(row["quantityUsed"])) for row in used)
        assert cooked_count() == count_before + 1
        assert any(row["mealEventId"] == done["mealEventId"] for row in api("GET", "/api/calendar/meals"))
        print("PASS real AI preview -> quantity preview -> atomic completion/retry/calendar/COOKED", flush=True)
        api("DELETE", f"/api/calendar/{done['mealEventId']}")
        assert remaining() == before and cooked_count() == count_before
        print("PASS atomic meal deletion restores stock and removes linked feedback", flush=True)
    finally:
        # Scoped transactional cleanup: never delete by a broad name/prefix or existing user ID.
        with engine.begin() as conn:
            owned = conn.execute(text("SELECT name, owner_user_id FROM fridge WHERE fridge_id=:id FOR UPDATE"),
                                 {"id": fridge_id}).one()
            assert owned.name == name and owned.owner_user_id == auth["userId"]
            main = conn.execute(text("SELECT main_fridge_id FROM user WHERE user_id=:uid"),
                                {"uid": auth["userId"]}).scalar()
            assert main == auth["mainFridgeId"], "Main fridge changed; preserve fixture for inspection"
            conn.execute(text("DELETE mc FROM meal_consumption mc JOIN calendar c ON c.meal_event_id=mc.meal_event_id "
                              "WHERE c.fridge_id=:id"), {"id": fridge_id})
            for table in ("recipe_recommendation_feedback", "inventory_activity", "calendar",
                          "inventory_batch", "fridge_member", "fridge"):
                conn.execute(text(f"DELETE FROM {table} WHERE fridge_id=:id"), {"id": fridge_id})
        print("PASS fixture removed; original main fridge unchanged", flush=True)


if __name__ == "__main__":
    main()
