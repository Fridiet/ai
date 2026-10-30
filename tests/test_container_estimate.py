import io
import json

import httpx
import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import container_estimate as module


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AI_SERVICE_TOKEN", "test-token-" * 4)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("OPENAI_CONTAINER_MODEL", raising=False)
    app = FastAPI()
    app.include_router(module.router)
    return TestClient(app)


def picture():
    data = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(data, "PNG")
    return data.getvalue()


def post(client, raw=None, mime="image/png", auth=True):
    return client.post("/api/containers/estimate",
        headers={"X-AI-Service-Token": "test-token-" * 4} if auth else {},
        files={"file": ("container.png", picture() if raw is None else raw, mime)})


def mock_provider(monkeypatch, body=None, code=200, error=None):
    async def send(self, url, **kwargs):
        assert url == "https://api.openai.com/v1/responses"
        payload = kwargs["json"]
        assert payload["model"] == "gpt-5.4-mini"
        assert payload["store"] is False
        assert payload["text"]["format"]["strict"] is True
        assert payload["input"][0]["content"][1]["image_url"].startswith("data:image/jpeg;base64,")
        if error:
            raise error
        return httpx.Response(code, json=body)
    monkeypatch.setattr(httpx.AsyncClient, "post", send)


def result(status="estimated", percent=50):
    return dict(status=status, remainingPercent=percent,
                lowerPercent=percent, upperPercent=percent, reason="테스트 응답")


def wrapped(value):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(value)}]}]}


@pytest.mark.parametrize("percent", [0, 50, 100])
def test_success(client, monkeypatch, percent):
    mock_provider(monkeypatch, wrapped(result(percent=percent)))
    response = post(client)
    assert response.status_code == 200
    assert response.json()["remainingPercent"] == percent
    assert response.json()["requiresConfirmation"] is True
    assert response.json()["basis"] == "container_volume"


def test_unknown(client, monkeypatch):
    mock_provider(monkeypatch, wrapped(result("unable_to_estimate", None)))
    assert post(client).json()["remainingPercent"] is None


@pytest.mark.parametrize("value", [result(percent=101), result(percent=51),
    result("unable_to_estimate", 50), result(percent=None),
    {**result(), "lowerPercent": 90}])
def test_invalid_model_result(client, monkeypatch, value):
    mock_provider(monkeypatch, wrapped(value))
    assert post(client).status_code == 502


@pytest.mark.parametrize("code,expected", [(401, 502), (429, 503), (500, 502)])
def test_provider_error(client, monkeypatch, code, expected):
    mock_provider(monkeypatch, {"secret": "do not leak"}, code)
    response = post(client)
    assert response.status_code == expected
    assert "do not leak" not in response.text


def test_timeout(client, monkeypatch):
    mock_provider(monkeypatch, error=httpx.ReadTimeout("secret"))
    assert post(client).status_code == 504


def test_quota_is_not_reported_as_temporary(client, monkeypatch):
    mock_provider(monkeypatch, {"error": {"code": "credit_balance_exhausted",
        "type": "insufficient_quota"}}, 429)
    response = post(client)
    assert response.status_code == 503
    assert "결제" in response.json()["detail"]
    assert "잠시 후" not in response.json()["detail"]


def test_refusal(client, monkeypatch):
    mock_provider(monkeypatch, {"status": "completed", "output": [{"type": "message",
        "content": [{"type": "refusal", "refusal": "no"}]}]})
    assert post(client).json()["status"] == "unable_to_estimate"


def test_incomplete(client, monkeypatch):
    mock_provider(monkeypatch, {"status": "incomplete"})
    assert post(client).status_code == 502


def test_auth_and_key(client, monkeypatch):
    assert post(client, auth=False).status_code == 401
    monkeypatch.delenv("OPENAI_API_KEY")
    assert post(client).status_code == 503


def test_invalid_upload(client, monkeypatch):
    assert post(client, b"bad").status_code == 400
    assert post(client, mime="text/plain").status_code == 415
    monkeypatch.setattr(module, "MAX_BYTES", 10)
    assert post(client).status_code == 413


def test_pixel_limit(client, monkeypatch):
    monkeypatch.setattr(module, "MAX_PIXELS", 10)
    assert post(client).status_code == 413


@pytest.mark.parametrize("percent,band", [(0,"empty"),(5,"up_to_25"),(25,"up_to_25"),
    (30,"up_to_50"),(50,"up_to_50"),(55,"up_to_75"),(75,"up_to_75"),(80,"over_75"),(100,"over_75")])
def test_display_band(client, monkeypatch, percent, band):
    mock_provider(monkeypatch, wrapped(result(percent=percent)))
    assert post(client).json()["remainingBand"] == band


def test_unknown_band(client, monkeypatch):
    mock_provider(monkeypatch, wrapped(result("unable_to_estimate", None)))
    assert post(client).json()["remainingBand"] == "unknown"
