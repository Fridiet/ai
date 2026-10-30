import cv2
import numpy as np
import pytest
from datetime import date, datetime, timedelta
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


@pytest.fixture(autouse=True)
def internal_service_auth(monkeypatch):
    token = "test-only-service-token-not-a-secret-123456"
    monkeypatch.setenv("AI_SERVICE_TOKEN", token)
    client.headers["X-AI-Service-Token"] = token
    yield
    client.headers.pop("X-AI-Service-Token", None)


def test_database_endpoints_reject_unauthenticated_service():
    response = TestClient(app).post("/recommendations", json={"fridge_id":7,"user_id":5})
    assert response.status_code == 401


def sample_png() -> bytes:
    image = np.full((40, 120, 3), 255, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    return encoded.tobytes()


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_public_api_has_expected_business_routes() -> None:
    assert set(app.openapi()["paths"]) == {
        "/health",
        "/api/containers/estimate",
        "/ocr",
        "/ocr/raw",
        "/api/ocr/receipt",
        "/api/recipes/ai",
        "/ocr/inventory-candidates",
        "/ocr/inventory-candidates/save",
        "/ocr/scans/{ocr_scan_id}/confirm",
        "/recommendations",
    }


def test_ocr(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.run_ocr",
        lambda image: [{"text": "테스트", "confidence": 0.99, "box": [[0, 0], [10, 0], [10, 10], [0, 10]]}],
    )
    response = client.post("/ocr/raw", files={"file": ("sample.png", sample_png(), "image/png")})
    assert response.status_code == 200
    assert response.json()["text"] == "테스트"
    assert response.json()["count"] == 1


def test_rejects_non_image() -> None:
    response = client.post("/ocr", files={"file": ("a.txt", b"hello", "text/plain")})
    assert response.status_code == 415


def test_inventory_api_accepts_reconstructed_count_source(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'false')
    def box(text, x, y):
        return dict(text=text, confidence=.99,
                    box=[[x-15,y],[x+15,y],[x+15,y+15],[x-15,y+15]])
    items=[box('상품명',50,0),box('단가',300,0),box('수량',400,0),box('금액',500,0),
           box('당근',50,40),box('1,000',300,40),box('2,000',500,40)]
    monkeypatch.setattr('app.main.run_ocr',lambda image: items)
    response=client.post('/ocr/inventory-candidates',files={'file':('receipt.png',sample_png(),'image/png')})
    assert response.status_code==200
    batch=response.json()['inventory_candidates'][0]['inventory_batch_candidate']
    assert batch['remaining_quantity']==2
    assert batch['quantity_source']=='RECONSTRUCTED_PRICE_COLUMNS'


def test_ocr_inventory_candidates(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.run_ocr",
        lambda image: [
            {
                "text": "냉장 국산콩두부 300g",
                "confidence": 0.99,
                "box": [[0, 0], [220, 0], [220, 20], [0, 20]],
            }
        ],
    )

    response = client.post(
        "/ocr/inventory-candidates",
        files={"file": ("sample.png", sample_png(), "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["requires_backend_mapping"] is True
    candidate = payload["inventory_candidates"][0]
    assert candidate["ingredient_master_candidate"]["ingredient_id"] is None
    assert candidate["ingredient_master_candidate"]["canonical_name"] == "두부"
    batch = candidate["inventory_batch_candidate"]
    assert batch["storage_type"] == "REFRIGERATED"
    assert batch["storage_source"] == "RECEIPT_TEXT"


def test_receipt_ocr_uses_frontend_contract(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.run_ocr",
        lambda image: [
            {
                "text": "냉장 당근 2개 3,000원",
                "confidence": 0.99,
                "box": [[0, 0], [240, 0], [240, 20], [0, 20]],
            }
        ],
    )

    response = client.post(
        "/ocr",
        files={"file": ("receipt.png", sample_png(), "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0].pop("rawText") == "냉장 당근 2개 3,000원"
    assert payload.pop("reviewCandidates") == []
    notes = payload["items"][0].pop("reviewNotes")
    assert notes == []
    assert payload == {
        "items": [
            {
                "name": "당근",
                "quantity": 2,
                "unit": "개",
                "consumeByDate": (date.today() + timedelta(days=7)).isoformat(),
                "locationCategory": "refrigerated",
                "category": "vegetable",
            }
        ]
    }


def test_ai_recipe_uses_team_contract(monkeypatch) -> None:
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return {
            "title": "소고기 배추 된장국",
            "description": "간단한 국 요리입니다.",
            "cookingTime": 25,
            "category": "국/찌개",
            "score": 0.0,
            "scoreBreakdown": {},
            "reasons": [],
            "availableIngredients": [],
            "missingIngredients": [],
            "ingredients": [
                {
                    "nameText": "소고기",
                    "quantity": 150.0,
                    "unit": "g",
                    "optional": False,
                }
            ],
            "steps": [{"stepNumber": 1, "description": "재료를 손질합니다."}],
        }

    monkeypatch.setattr("app.main.generate_team_recipe", fake_generate)
    response = client.post(
        "/api/recipes/ai",
        json={
            "healthCondition": "당뇨",
            "healthGoal": "혈당 관리",
            "activeInventories": ["소고기", "배추"],
            "inventories": [
                {
                    "ingredientName": "소고기",
                    "consumeByDate": "2026-08-22",
                    "remainingQuantity": 300,
                    "unit": "g",
                }
            ],
            "targetIngredients": ["소고기"],
            "additionalIngredients": ["버터", "파프리카"],
            "excludeIngredients": ["설탕", "마요네즈"],
            "recentFeedback": [
                {
                    "recipeName": "소고기 배추 된장국",
                    "eventType": "COOKED",
                    "occurredAt": "2026-08-19T18:30:00",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert captured == {
        "shopping_mode": False,
        "allow_target_fallback": True,
        "health_condition": "당뇨",
        "health_goal": "혈당 관리",
        "active_inventories": ["소고기", "배추"],
        "inventories": [
            {
                "ingredientName": "소고기",
                "consumeByDate": date(2026, 8, 22),
                "remainingQuantity": 300.0,
                "unit": "g",
                "countBasis": None,
                "perCountQuantity": None,
                "perCountUnit": None,
                "quantityNeedsReview": False,
                "unitType": "QUANTITY",
                    "customQuantityText": None,
                    "status": "ACTIVE",
            }
        ],
        "target_ingredients": ["소고기"],
        "additional_ingredients": ["버터", "파프리카"],
        "exclude_ingredients": ["설탕", "마요네즈"],
        "recent_feedback": [
            {
                "recipeKey": None,
                "recipeName": "소고기 배추 된장국",
                "eventType": "COOKED",
                "occurredAt": datetime(2026, 8, 19, 18, 30),
            }
        ],
    }
    assert response.json() == {
        "recipeKey": None,
        "ingredientBalances": [],
        "title": "소고기 배추 된장국",
        "description": "간단한 국 요리입니다.",
        "cookingTime": 25,
        "category": "국/찌개",
        "score": 0.0,
        "scoreBreakdown": {},
        "reasons": [],
        "availableIngredients": [],
        "missingIngredients": [],
        "ingredients": [
            {
                "nameText": "소고기",
                "quantity": 150.0,
                "unit": "g",
                "optional": False,
            }
        ],
        "steps": [{"stepNumber": 1, "description": "재료를 손질합니다."}],
    }


def test_ocr_inventory_candidates_save(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.main.run_ocr",
        lambda image: [
            {
                "text": "냉장 국산콩두부 300g",
                "confidence": 0.99,
                "box": [[0, 0], [220, 0], [220, 20], [0, 20]],
            }
        ],
    )
    captured = {}

    def fake_save(result, *, fridge_id, created_by):
        captured["fridge_id"] = fridge_id
        captured["created_by"] = created_by
        captured["canonical_name"] = result.inventory_candidates[0].ingredient_master_candidate.canonical_name
        return {
            "ocr_scan_id": 10,
            "status": "PENDING_REVIEW",
            "item_count": 1,
            "mapped_item_count": 1,
            "requires_review_count": 1,
            "items": [
                {
                    "ocr_scan_item_id": 20,
                    "row_index": 0,
                    "ingredient_id": 3,
                    "canonical_name": "두부",
                    "review_status": "PENDING_REVIEW",
                }
            ],
            "inventory_candidates": result.model_dump(mode="json")["inventory_candidates"],
        }

    monkeypatch.setattr("app.main.save_inventory_candidates", fake_save)
    response = client.post(
        "/ocr/inventory-candidates/save",
        files={"file": ("sample.png", sample_png(), "image/png")},
        data={"fridge_id": "7", "created_by": "11"},
    )

    assert response.status_code == 201
    assert response.json()["ocr_scan_id"] == 10
    assert captured == {"fridge_id": 7, "created_by": 11, "canonical_name": "두부"}


def test_confirm_ocr_inventory(monkeypatch) -> None:
    def fake_confirm(ocr_scan_id, *, created_by, review_items):
        assert ocr_scan_id == 10
        assert created_by == 11
        assert review_items[0]["ingredient_id"] == 3
        return {
            "ocr_scan_id": 10,
            "status": "REVIEWED",
            "created_batch_count": 1,
            "rejected_item_count": 0,
            "batches": [
                {
                    "batch_id": 30,
                    "ocr_scan_item_id": 20,
                    "ingredient_id": 3,
                    "remaining_quantity": 300.0,
                    "unit": "g",
                    "consume_by_date": "2026-08-20",
                    "storage_type": "REFRIGERATED",
                }
            ],
        }

    monkeypatch.setattr("app.main.confirm_inventory_candidates", fake_confirm)
    response = client.post(
        "/ocr/scans/10/confirm",
        json={
            "created_by": 11,
            "items": [
                {
                    "ocr_scan_item_id": 20,
                    "decision": "CONFIRM",
                    "ingredient_id": 3,
                    "remaining_quantity": 300,
                    "unit": "g",
                    "consume_by_date": "2026-08-20",
                    "storage_type": "REFRIGERATED",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["created_batch_count"] == 1


def test_recipe_recommendations(monkeypatch) -> None:
    def fake_recommend(**kwargs):
        assert kwargs == {
            "fridge_id": 7,
            "user_id": 11,
            "limit": 3,
            "max_missing_required": 1,
        }
        return {
            "fridge_id": 7,
            "user_id": 11,
            "generated_at": "2026-08-15T12:00:00",
            "active_inventory_count": 2,
            "excluded_expired_inventory_count": 0,
            "applied_profile": None,
            "warnings": [],
            "score_policy": {
                "expiry_urgency": 40.0,
                "inventory_coverage": 35.0,
                "stock_utilization": 15.0,
                "menu_diversity": 10.0,
                "missing_required_penalty_per_item": 10.0,
            },
            "recommendations": [
                {
                    "recipe_id": 1,
                    "recipe_name": "두부구이",
                    "cook_time": 15,
                    "category": "반찬",
                    "description": "두부를 활용한 간단한 반찬",
                    "steps": ["두부를 굽습니다."],
                    "source": "FRIDIET_CURATED_MVP",
                    "total_score": 89.0,
                    "score_breakdown": {
                        "expiry_urgency": 32.0,
                        "inventory_coverage": 35.0,
                        "stock_utilization": 5.0,
                        "menu_diversity": 10.0,
                        "missing_required_penalty": 0.0,
                        "base_inventory_score": 89.0,
                        "nutrition_fit_score": None,
                    },
                    "coverage_ratio": 1.0,
                    "available_ingredients": ["두부"],
                    "missing_ingredients": [],
                    "expiring_ingredients": [
                        {
                            "batch_id": 30,
                            "ingredient_id": 3,
                            "canonical_name": "두부",
                            "consume_by_date": "2026-08-18",
                            "days_remaining": 3,
                        }
                    ],
                    "used_batch_ids": [30],
                    "reason": "두부의 소비기한이 3일 남았습니다. 필수 재료 1개 중 1개를 보유하고 있습니다.",
                }
            ],
        }

    monkeypatch.setattr("app.main.recommend_recipes", fake_recommend)
    response = client.post(
        "/recommendations",
        json={
            "fridge_id": 7,
            "user_id": 11,
            "limit": 3,
            "max_missing_required": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["recommendations"][0]["recipe_name"] == "두부구이"
