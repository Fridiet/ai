"""Create labeled synthetic batches, verify consumption/discard, leave closed test history."""
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine
from scripts.verify_personalization_demo import post
from scripts.seed_personalization_demo import PASSWORD


def verify():
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("로컬 검증만 허용합니다.")
    login = post("/api/auth/login", {"email": "taste.meat@example.invalid", "password": PASSWORD})
    fid = login["mainFridgeId"]
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    name = "[검증]소비폐기-" + uuid.uuid4().hex[:8]
    def call(method, path, data=None):
        request = Request("http://localhost:8080" + path, method=method,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": "Bearer " + login["token"], "Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            body = response.read()
            return json.loads(body) if body else None
    def create(unit, custom=False):
        return call("POST", "/api/inventories", {"fridgeId": fid, "ingredientName": name,
            "unitType": "CUSTOM" if custom else "QUANTITY", "remainingQuantity": None if custom else 100,
            "customQuantityText": "한 봉지" if custom else None, "unit": unit,
            "consumeByDate": (now.date() + timedelta(days=30)).isoformat(), "locationCategory": "REFRIGERATED"})["batchId"]
    batch = create("g")
    count_batch = create("개")
    custom_batch = create(None, True)
    def meal(amount, unit="g", batch_id=batch, custom=None, request_id=None):
        item = {"batchId": batch_id, "unitType": "CUSTOM" if custom else "QUANTITY", "unit": unit,
                "quantityUsed": amount, "customText": custom}
        return call("POST", "/api/calendar/meals", {"requestId": request_id or uuid.uuid4().hex, "fridgeId": fid, "title": name,
                    "mealType": "DINNER", "usedIngredients": [item]})
    for amount, unit in [(101, "g"), (-1, "g"), (1, "kg")]:
        try:
            meal(amount, unit)
            raise AssertionError("잘못된 소비량이 허용되었습니다.")
        except HTTPError as error:
            assert error.code == 400
    assert call("GET", f"/api/inventories/{batch}")["remainingQuantity"] == 100
    key = uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        meals = list(pool.map(lambda _: meal(40, request_id=key), range(4)))
    assert len({m["mealEventId"] for m in meals}) == 1
    assert call("GET", f"/api/inventories/{batch}")["remainingQuantity"] == 60
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: call("DELETE", f"/api/inventories/{batch}?reason=DISCARDED"), range(4)))
    call("DELETE", f"/api/inventories/{count_batch}?reason=CONSUMED")
    meal(None, None, custom_batch, "0")
    report = call("GET", "/api/reports/monthly?month=" + now.strftime("%Y-%m"))
    stock = [r for r in report["stockActivity"] if r["ingredientName"] == name]
    gram = next(r for r in stock if r["unit"] == "g")
    assert gram["consumedQuantity"] == 40 and gram["discardedQuantity"] == 60
    assert gram["discardedRecords"] == 1
    assert next(r for r in stock if r["unit"] == "개")["consumedQuantity"] == 100
    unknown = next(r for r in stock if r["unit"] == "수량 미집계")
    assert unknown["consumedQuantity"] is None and unknown["consumedRecords"] == 1
    with engine.connect() as db:
        states = list(db.execute(text("SELECT status FROM inventory_batch WHERE batch_id IN (:a,:b,:c)"),
                                {"a":batch,"b":count_batch,"c":custom_batch}).scalars())
        assert sorted(states) == ["CONSUMED", "CONSUMED", "DISCARDED"]
    return {"synthetic_only": True, "test_name": name, "batch_ids": [batch,count_batch,custom_batch],
            "validation_rejections": 3, "concurrent_meal_records": 1, "concurrent_discard_records": 1, "stock": stock}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=True, indent=2))
