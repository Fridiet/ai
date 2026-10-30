from types import SimpleNamespace
from collections import OrderedDict

import pytest
from fastapi.testclient import TestClient

from app import ocr_classification as adapter
from app.main import app


@pytest.fixture(autouse=True)
def isolated_adapter(monkeypatch):
    monkeypatch.setenv("OCR_CLASSIFIER_ENABLED", "true")
    monkeypatch.setattr(adapter, "_model", None)
    monkeypatch.setattr(adapter, "_failed", False)
    monkeypatch.setattr(adapter, "_cache", OrderedDict())


def prediction(name="양파", review=False, food=True):
    return {"canonical_name": name, "needs_review": review, "is_food": food, "model_score": .8}


def test_disabled_does_not_load(monkeypatch):
    monkeypatch.setenv("OCR_CLASSIFIER_ENABLED", "false")
    monkeypatch.setattr(adapter, "_load", lambda: pytest.fail("must not load"))
    assert adapter.predict_product("양파") is None


def test_missing_model_falls_back(monkeypatch):
    monkeypatch.setenv("OCR_CLASSIFIER_MANIFEST", "missing-model.json")
    original = {"standard_food_name": "양파"}
    assert adapter.apply_classification("불명상품", original, None) is original
    assert adapter._failed


def test_runtime_failure_falls_back(monkeypatch):
    def fail(text):
        raise RuntimeError("test inference failure")
    monkeypatch.setattr(adapter, "_model", SimpleNamespace(predict=fail))
    assert adapter.predict_product("양파") is None


def test_prediction_cache_is_reused_and_returns_copy(monkeypatch):
    calls = []
    def predict(text):
        calls.append(text)
        return prediction()
    monkeypatch.setattr(adapter, "_model", SimpleNamespace(predict=predict))
    first = adapter.predict_product("양파")
    first["canonical_name"] = "changed"
    assert adapter.predict_product("양파")["canonical_name"] == "양파"
    assert calls == ["양파"]


def test_uncertain_retains_only_exact_curated_match(monkeypatch):
    monkeypatch.setattr(adapter, "predict_product", lambda _: prediction(review=True))
    exact = {"standard_food_name": "사과", "source": "ingredient_dictionary", "match_type": "exact"}
    assert adapter.apply_classification("사과", exact, None) is exact
    fuzzy = dict(exact, match_type="fuzzy")
    assert adapter.apply_classification("사과", fuzzy, None)["standard_food_name"] is None


@pytest.mark.parametrize("route", ["/ocr", "/api/ocr/receipt", "/ocr/inventory-candidates"])
def test_api_model_classification_and_schema(monkeypatch, route):
    import cv2
    import numpy as np
    monkeypatch.setattr(adapter, "predict_product", lambda _: prediction(name="키위"))
    monkeypatch.setattr("app.main.run_ocr", lambda _: [
        {"text": "상품테스트 2,000 2 4,000", "confidence": .99,
         "box": [[0, 0], [500, 0], [500, 20], [0, 20]]}])
    _, encoded = cv2.imencode(".png", np.zeros((30, 500, 3), dtype=np.uint8))
    response = TestClient(app).post(route, files={"file": ("test.png", encoded.tobytes(), "image/png")})
    assert response.status_code == 200, response.text
    data = response.json()
    if "inventory_candidates" in data:
        match = data["inventory_candidates"][0]["ingredient_master_candidate"]
        assert match["source"] == "ocr_classifier_fast_v7"
        assert match["match_type"] == "ml_classifier"
    else:
        assert data["items"][0]["name"] == "키위"
        assert data["items"][0]["quantity"] == 2


@pytest.mark.parametrize("review,food,review_count", [(True, None, 1), (False, False, 0)])
def test_api_review_and_nonfood(monkeypatch, review, food, review_count):
    import cv2
    import numpy as np
    monkeypatch.setattr(adapter, "predict_product", lambda _: prediction(None, review, food))
    monkeypatch.setattr("app.main.run_ocr", lambda _: [
        {"text": "상품테스트 2,000 1 2,000", "confidence": .99,
         "box": [[0, 0], [500, 0], [500, 20], [0, 20]]}])
    _, encoded = cv2.imencode(".png", np.zeros((30, 500, 3), dtype=np.uint8))
    response = TestClient(app).post("/ocr", files={"file": ("test.png", encoded.tobytes(), "image/png")})
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert len(response.json()["reviewCandidates"]) == review_count
