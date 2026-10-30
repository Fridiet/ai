from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_AI_URL = "http://localhost:8000"
DEFAULT_BACKEND_URL = "http://localhost:8080"


class SmokeTestError(RuntimeError):
    """Raised when an OCR-to-recommendation smoke-test stage fails."""


@dataclass(frozen=True)
class InventoryDraft:
    name: str
    quantity: float
    unit: str
    consume_by_date: str
    location_category: str


def _normalized_storage(value: Any) -> str:
    normalized = str(value or "REFRIGERATED").upper()
    if normalized in {"ROOM_TEMP", "ROOM_TEMPERATURE"}:
        return "ROOM_TEMP"
    if normalized == "FROZEN":
        return "FROZEN"
    if normalized == "SEASONING":
        return "SEASONING"
    return "REFRIGERATED"


def _positive_number(value: Any, default: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def extract_inventory_drafts(payload: dict[str, Any]) -> list[InventoryDraft]:
    drafts: list[InventoryDraft] = []
    seen: set[str] = set()

    candidates = payload.get("inventory_candidates")
    if isinstance(candidates, list):
        for raw_candidate in candidates:
            if not isinstance(raw_candidate, dict):
                continue
            master = raw_candidate.get("ingredient_master_candidate") or {}
            batch = raw_candidate.get("inventory_batch_candidate") or {}
            if not isinstance(master, dict) or not isinstance(batch, dict):
                continue
            name = str(
                master.get("canonical_name")
                or raw_candidate.get("normalized_product_name")
                or ""
            ).strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            drafts.append(
                InventoryDraft(
                    name=name,
                    quantity=_positive_number(batch.get("remaining_quantity")),
                    unit=str(batch.get("unit") or master.get("default_unit") or "개"),
                    consume_by_date=str(batch.get("consume_by_date") or ""),
                    location_category=_normalized_storage(batch.get("storage_type")),
                )
            )
        return drafts

    items = payload.get("items")
    if not isinstance(items, list):
        return drafts
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        name = str(raw_item.get("name") or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        drafts.append(
            InventoryDraft(
                name=name,
                quantity=_positive_number(raw_item.get("quantity")),
                unit=str(raw_item.get("unit") or "개"),
                consume_by_date=str(raw_item.get("consumeByDate") or ""),
                location_category=_normalized_storage(
                    raw_item.get("locationCategory")
                ),
            )
        )
    return drafts


def _request_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout: int = 120,
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, method=method, data=body, headers=headers)
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise SmokeTestError(f"{method} {url} 실패 ({error.code}): {detail}") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise SmokeTestError(f"{method} {url} 응답을 처리할 수 없습니다.") from error


def upload_receipt(ai_url: str, image_path: Path) -> dict[str, Any]:
    if not image_path.is_file():
        raise SmokeTestError(f"영수증 이미지가 없습니다: {image_path}")
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    boundary = f"----fridiet-{uuid.uuid4().hex}"
    file_bytes = image_path.read_bytes()
    body = b"\r\n".join(
        [
            f"--{boundary}".encode(),
            (
                'Content-Disposition: form-data; name="file"; '
                f'filename="{image_path.name}"'
            ).encode("utf-8"),
            f"Content-Type: {mime_type}".encode(),
            b"",
            file_bytes,
            f"--{boundary}--".encode(),
            b"",
        ]
    )
    request = Request(
        f"{ai_url.rstrip('/')}/ocr",
        method="POST",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urlopen(request, timeout=180) as response:
            payload = json.load(response)
    except HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise SmokeTestError(f"OCR 실패 ({error.code}): {detail}") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise SmokeTestError("OCR 서버 응답을 처리할 수 없습니다.") from error
    if not isinstance(payload, dict):
        raise SmokeTestError("OCR 응답이 JSON 객체가 아닙니다.")
    return payload


def register_drafts(
    backend_url: str,
    token: str,
    fridge_id: int,
    drafts: list[InventoryDraft],
) -> int:
    registered = 0
    for draft in drafts:
        if not draft.consume_by_date:
            raise SmokeTestError(
                f"{draft.name}의 소비기한이 없어 재고 등록을 중단했습니다. "
                "OCR 결과를 먼저 확인하세요."
            )
        _request_json(
            "POST",
            f"{backend_url.rstrip('/')}/api/inventories",
            token=token,
            payload={
                "fridgeId": fridge_id,
                "ingredientName": draft.name,
                "unitType": "QUANTITY",
                "remainingQuantity": draft.quantity,
                "unit": draft.unit,
                "customQuantityText": None,
                "consumeByDate": draft.consume_by_date,
                "locationCategory": draft.location_category,
            },
        )
        registered += 1
    return registered


def run_smoke_test(
    *,
    image_path: Path,
    fridge_id: int,
    token: str,
    ai_url: str = DEFAULT_AI_URL,
    backend_url: str = DEFAULT_BACKEND_URL,
    register: bool = False,
    max_items: int = 10,
    require_personalization: bool = False,
) -> dict[str, Any]:
    ocr_payload = upload_receipt(ai_url, image_path)
    drafts = extract_inventory_drafts(ocr_payload)[:max_items]
    if not drafts:
        raise SmokeTestError("OCR 결과에서 추천에 사용할 표준 재료를 찾지 못했습니다.")

    registered_count = (
        register_drafts(backend_url, token, fridge_id, drafts) if register else 0
    )
    _, recommendation = _request_json(
        "POST",
        f"{backend_url.rstrip('/')}/api/recipes/ai/preview",
        token=token,
        payload={
            "fridgeId": fridge_id,
            "targetIngredients": [],
            "additionalIngredients": [] if register else [draft.name for draft in drafts],
            "excludeIngredients": [],
        },
    )
    if not isinstance(recommendation, dict) or not recommendation.get("recipeName"):
        raise SmokeTestError("Spring 추천 API가 유효한 레시피를 반환하지 않았습니다.")

    score_breakdown = recommendation.get("scoreBreakdown") or {}
    personalization_applied = score_breakdown.get("nutrition_fit_score") is not None
    if require_personalization and not personalization_applied:
        raise SmokeTestError(
            "사용자 건강 프로필에 따른 영양 적합도 점수가 적용되지 않았습니다."
        )

    return {
        "recognized_items": [asdict(draft) for draft in drafts],
        "registered_count": registered_count,
        "database_changed": register,
        "recommendation": {
            "recipe_name": recommendation["recipeName"],
            "score": recommendation.get("recommendationScore"),
            "reasons": recommendation.get("recommendationReasons") or [],
            "missing_ingredients": recommendation.get("missingIngredients") or [],
            "nutrition_profile_applied": personalization_applied,
        },
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="실제 서버의 영수증 OCR부터 개인화 레시피 추천까지 점검합니다."
    )
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--fridge-id", type=int, required=True)
    parser.add_argument("--ai-url", default=DEFAULT_AI_URL)
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument("--max-items", type=int, default=10)
    parser.add_argument(
        "--register",
        action="store_true",
        help="인식 재료를 실제 재고 DB에 저장한 뒤 추천합니다.",
    )
    parser.add_argument(
        "--require-personalization",
        action="store_true",
        help="건강 프로필 영양 점수가 없으면 실패 처리합니다.",
    )
    args = parser.parse_args()

    token = os.getenv("FRIDIET_AUTH_TOKEN", "").strip()
    if not token:
        parser.error("FRIDIET_AUTH_TOKEN 환경 변수에 로그인 JWT를 설정하세요.")
    if args.fridge_id < 1:
        parser.error("--fridge-id는 1 이상이어야 합니다.")
    if args.max_items < 1:
        parser.error("--max-items는 1 이상이어야 합니다.")

    result = run_smoke_test(
        image_path=args.image,
        fridge_id=args.fridge_id,
        token=token,
        ai_url=args.ai_url,
        backend_url=args.backend_url,
        register=args.register,
        max_items=args.max_items,
        require_personalization=args.require_personalization,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
