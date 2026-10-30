"""Local synthetic account integration. Creates explicitly marked test stock; never purchases externally."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
import requests
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine
from scripts.seed_personalization_demo import PASSWORD


def verify():
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost","127.0.0.1","::1"}:
        raise RuntimeError("로컬 DB 전용 검증입니다.")
    base = "http://localhost:8080"
    for path in ("/api/inventories?fridgeId=7", "/api/carts?fridgeId=7", "/api/reports/monthly"):
        assert requests.get(base+path,timeout=20).status_code == 401
        assert requests.get(base+path,headers={"Authorization":"Bearer invalid"},timeout=20).status_code == 401
    login = requests.post(base+"/api/auth/login",json={"email":"taste.meat@example.invalid","password":PASSWORD},timeout=20)
    login.raise_for_status()
    headers = {"Authorization":"Bearer "+login.json()["token"]}
    for path in ("/api/inventories?fridgeId=1", "/api/carts?fridgeId=1"):
        assert requests.get(base+path,headers=headers,timeout=20).status_code == 403
    def post(path,payload=None):
        r = requests.post(base+path,headers=headers,json=payload,timeout=30)
        r.raise_for_status()
        return r.json()
    name = "[검증]장바구니재시도-"+uuid.uuid4().hex[:8]
    cart = post("/api/carts",{"fridgeId":7,"name":name})
    item = post(f"/api/carts/{cart['cartId']}/items",{"itemName":name,"quantity":2,"unit":"개","unitType":"QUANTITY"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _:post(f"/api/carts/items/{item['itemId']}/buy"),range(4)))
    assert len(set(ids)) == 1
    with engine.connect() as db:
        count = db.scalar(text("SELECT COUNT(*) FROM inventory_batch WHERE fridge_id=7 AND ingredient_name=:name"),{"name":name})
    assert count == 1
    # Closed as REMOVED, not falsely recorded as real consumption or waste.
    closed = requests.delete(base+f"/api/inventories/{ids[0]}?reason=REMOVED",headers=headers,timeout=20)
    closed.raise_for_status()
    return {"anonymous_and_invalid_token_401":True,"other_fridge_403":True,
        "concurrent_requests":4,"created_batches":count,"batch_id":ids[0],"test_stock_closed":True}


if __name__ == "__main__":
    print(json.dumps(verify(),indent=2))
