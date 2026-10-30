"""Read-only monthly report integration checks using synthetic accounts.

No recipe previews, saves or cooked events are created by this verifier.
"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine
from scripts.verify_personalization_demo import post
from scripts.seed_personalization_demo import PERSONAS, PASSWORD


def verify():
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("로컬 DB 전용 검사입니다.")
    now = datetime.now(ZoneInfo("Asia/Seoul")).replace(tzinfo=None)
    month = now.strftime("%Y-%m")
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    results = []
    for email in [p[0] for p in PERSONAS] + ["taste.new@example.invalid"]:
        login = post("/api/auth/login", {"email": email, "password": PASSWORD})
        def get(query):
            req = Request("http://localhost:8080/api/reports/monthly" + query,
                          headers={"Authorization": "Bearer " + login["token"]})
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        # Supplying someone else's ID must not change authenticated-user scope.
        report = get("?month=" + month + "&userId=1")
        with engine.connect() as db:
            rows = db.execute(text("""SELECT recipe_key, recipe_name, event_type, created_at
                FROM recipe_recommendation_feedback WHERE user_id=:uid
                  AND created_at>=:start AND created_at<:now
                  AND event_type IN ('COOKED', 'SAVED')"""),
                              {"uid": login["userId"], "start": start, "now": now}).mappings().all()
        identity = lambda r: r["recipe_key"] or "legacy:" + r["recipe_name"].strip()
        cooked = {(identity(r), r["created_at"].date()) for r in rows if r["event_type"] == "COOKED"}
        saved = {identity(r) for r in rows if r["event_type"] == "SAVED"}
        assert report["cookedCount"] == len(cooked)
        assert report["savedRecipeCount"] == len(saved)
        assert sum(d["cookedCount"] for d in report["dailyActivity"]) == len(cooked)
        assert sum(d["savedCount"] for d in report["dailyActivity"]) == len(saved)
        assert report["month"] == month
        assert report["timezone"] == "Asia/Seoul"
        if email == "taste.new@example.invalid":
            assert report["activeDays"] == 0
        try:
            get("?month=2026-99")
            raise AssertionError("잘못된 월을 허용했습니다.")
        except HTTPError as error:
            assert error.code == 400
        results.append({"user_id": login["userId"], "month": month,
                        "cooked": len(cooked), "saved": len(saved), "active_days": report["activeDays"]})
    return {"verified_against_db": True, "synthetic_accounts_only": True, "results": results}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))
