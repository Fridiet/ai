from datetime import date, timedelta
from typing import Any
import re
from .ingredient_taxonomy import family, receipt_category_override


def build_review_candidates(analysis):
    """Unclassified priced rows are not asserted to be food or silently discarded."""
    return [{"rawText":p["raw_text"], "purchaseCount":p["purchase_quantity"],
             # Detailed model/candidate evidence remains in analysis, not repeated in UI.
             "reason":"재료명을 확인해주세요."}
            for p in analysis["products"]
            if p["match"]["standard_food_name"] is None and (p.get("purchase_quantity") is not None or p.get("prices"))
            and p["match"].get("classification") != "nonfood"
            and not re.search(r"샴푸|컨디셔|치약|세제|휴지|화장지|비누", p["product_name"])]


VEGETABLE_NAMES = {
    "쌈채소",
    "청양고추", "팽이버섯", "표고버섯", "느타리버섯", "새송이버섯",
    "감자",
    "고구마",
    "양파",
    "대파",
    "쪽파",
    "마늘",
    "생강",
    "당근",
    "무",
    "배추",
    "양배추",
    "상추",
    "양상추",
    "깻잎",
    "시금치",
    "부추",
    "콩나물",
    "숙주",
    "오이",
    "애호박",
    "단호박",
    "가지",
    "토마토",
    "파프리카",
    "피망",
    "고추",
    "브로콜리",
    "버섯",
}
FRUIT_NAMES = {
    "자두",
    "사과",
    "배",
    "바나나",
    "귤",
    "오렌지",
    "수박",
    "레몬",
    "복숭아",
    "아보카도",
    "딸기",
    "포도",
    "블루베리",
    "파인애플",
}
MEAT_NAMES = {"고기", "등심", "소고기", "돼지고기", "닭고기", "오리고기", "햄", "소시지", "베이컨"}
SEAFOOD_NAMES = {"주꾸미", "붉은볼락", "고등어", "연어", "참치", "오징어", "새우", "멸치", "조개", "북어"}
DAIRY_NAMES = {"우유", "치즈", "버터", "요구르트"}
EGG_NAMES = {"달걀"}
GRAIN_NAMES = {"쌀", "현미", "잡곡", "밀가루", "콩", "콩가루", "땅콩"}
SEASONING_NAMES = {
    "소금",
    "설탕",
    "간장",
    "고추장",
    "된장",
    "식초",
    "식용유",
    "올리브유",
    "참기름",
    "들기름",
    "고춧가루",
    "후추",
    "케첩",
    "마요네즈",
}
BEVERAGE_NAMES = {"두유", "맥주"}

NAME_CATEGORY_RULES = (
    ("vegetable", VEGETABLE_NAMES),
    ("fruit", FRUIT_NAMES),
    ("meat", MEAT_NAMES),
    ("seafood", SEAFOOD_NAMES),
    ("dairy", DAIRY_NAMES),
    ("egg", EGG_NAMES),
    ("grain", GRAIN_NAMES),
    ("seasoning", SEASONING_NAMES),
    ("beverage", BEVERAGE_NAMES),
)

LOCATION_CATEGORY_MAP = {
    "REFRIGERATED": "refrigerated",
    "FROZEN": "frozen",
    "ROOM_TEMPERATURE": "room_temperature",
    "UNKNOWN": "unknown",
}

# 실제 표시 기한이 없는 영수증을 재고 등록 형식으로 바꾸기 위한 보수적인 MVP 규칙이다.
# 날짜는 영수증을 처리한 날부터 계산하며, 사용자가 최종 등록 전에 수정할 수 있어야 한다.
SHELF_LIFE_DAYS = {
    "refrigerated": {
        "vegetable": 7,
        "fruit": 7,
        "meat": 3,
        "seafood": 2,
        "dairy": 7,
        "egg": 21,
        "grain": 30,
        "seasoning": 60,
        "beverage": 14,
        "processed_food": 14,
        "etc": 7,
    },
    "frozen": {
        "vegetable": 60,
        "fruit": 60,
        "meat": 90,
        "seafood": 60,
        "dairy": 30,
        "egg": 30,
        "grain": 180,
        "seasoning": 180,
        "beverage": 30,
        "processed_food": 90,
        "etc": 60,
    },
    "room_temperature": {
        "vegetable": 14,
        "fruit": 7,
        "meat": 1,
        "seafood": 1,
        "dairy": 1,
        "egg": 14,
        "grain": 180,
        "seasoning": 180,
        "beverage": 60,
        "processed_food": 90,
        "etc": 30,
    },
    "unknown": {
        "vegetable": 3,
        "fruit": 3,
        "meat": 1,
        "seafood": 1,
        "dairy": 1,
        "egg": 7,
        "grain": 30,
        "seasoning": 30,
        "beverage": 7,
        "processed_food": 7,
        "etc": 3,
    },
}


def infer_receipt_category(candidate: dict[str, Any]) -> str:
    ingredient = candidate["ingredient_master_candidate"]
    specific = receipt_category_override(ingredient['canonical_name'])
    if specific:
        return specific
    canonical_name = family(ingredient["canonical_name"])

    for category, names in NAME_CATEGORY_RULES:
        if canonical_name in names:
            return category

    ingredient_type = ingredient["ingredient_type"]
    if ingredient_type == "SEASONING":
        return "seasoning"
    if ingredient_type == "BEVERAGE":
        return "beverage"
    if candidate["classification"] == "processed_food":
        return "processed_food"
    return "etc"


def recommended_consume_by_date(
    *,
    category: str,
    location_category: str,
    reference_date: date | None = None,
) -> date | None:
    if location_category not in {'refrigerated', 'frozen', 'room_temperature'}:
        return None
    storage_rules = SHELF_LIFE_DAYS.get(location_category, SHELF_LIFE_DAYS["unknown"])
    days = storage_rules.get(category, storage_rules["etc"])
    return (reference_date or date.today()) + timedelta(days=days)


def build_receipt_items(
    candidates: list[dict[str, Any]],
    *,
    reference_date: date | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for candidate in candidates:
        ingredient = candidate["ingredient_master_candidate"]
        batch = candidate["inventory_batch_candidate"]
        category = infer_receipt_category(candidate)
        location_category = LOCATION_CATEGORY_MAP.get(batch["storage_type"], "unknown")
        quantity = float(batch["remaining_quantity"])
        notes = ["소비기한과 보관 위치는 추정값입니다. 제품 표시를 확인해주세요."]
        if location_category == 'unknown':
            notes = ['보관 방식을 확인하지 못해 소비기한을 추정하지 않았습니다. 제품 표시를 확인하고 보관 위치와 소비기한을 입력해주세요.']
        if 'GPT_IDENTITY_REQUIRES_CONFIRMATION' in candidate.get('warnings', []):
            notes.append('GPT가 상품명을 보정했습니다. 원문과 재료명이 일치하는지 확인해주세요.')
        if batch.get('quantity_source')=='VERIFIED_RECEIPT_TOTALS':
            notes.append('표시된 상품 수량·금액의 합이 영수증 전체 구매 수량·금액과 일치합니다. 증정품 수량을 포함합니다.')
        elif batch.get('quantity_source')=='NUMERIC_CELLS_VERIFIED':
            notes.append('구매 수량은 단가·수량·금액 영역을 다시 읽고 검산했습니다. 원본 영수증과 확인해주세요.')
        elif batch.get('quantity_source')=='SECONDARY_VERIFIED_RECEIPT_COLUMNS':
            notes.append('구매 수량은 보조 OCR의 같은 상품 행에서 검산하여 보충했습니다. 원본 영수증과 확인해주세요.')
        elif batch.get('quantity_source')=='RECONSTRUCTED_PRICE_COLUMNS':
            notes.append('구매 수량은 단가·금액 열의 위치와 계산으로 복원했습니다. 영수증과 확인해주세요.')
        elif batch.get("quantity_source") != "VERIFIED_RECEIPT_COLUMNS":
            notes.append("구매 수량을 검산하지 못했습니다. 포장 용량과 구매 개수를 확인해주세요.")
        if "PACKAGE_SIZE_REQUIRES_CONFIRMATION" in candidate.get("warnings", []):
            notes.append("포장당 용량은 확인되지 않았습니다. 개수는 무게나 용량을 의미하지 않습니다.")
        if 'PURCHASE_UNIT_COUNT_ONLY' in candidate.get('warnings', []):
            notes.append('영수증의 구매 단위 개수로 표시했습니다. 한 봉지·묶음 안의 낱개 수나 무게는 알 수 없습니다. 무게·용량으로 관리하려는 경우에만 단위와 수량을 변경해주세요.')
        if ingredient.get("match_confidence", 0) < 0.85:
            notes.append("재료명 매칭이 불확실합니다. 영수증 원문과 비교해주세요.")

        items.append(
            {
                "name": ingredient["canonical_name"],
                "raw_text": candidate.get("raw_text"),
                # Internal only: retain product/state evidence for storage estimation.
                # ReceiptInventoryItem deliberately does not expose these fields.
                "_product_name": candidate.get("recognized_product_name") or candidate.get("normalized_product_name"),
                "_storage_source": batch.get("storage_source", "UNKNOWN"),
                "_date_source": "CATEGORY_FALLBACK",
                **({"_quantity_review_note": "포장 내용량을 확인해주세요. 현재 수량은 포장 개수입니다."}
                   if batch.get('quantity_source') == 'VERIFIED_ORDER_PURCHASE_COUNT'
                   and 'PACKAGE_SIZE_REQUIRES_CONFIRMATION' in candidate.get('warnings', []) else {}),
                "review_notes": notes,
                "quantity": int(quantity) if quantity.is_integer() else quantity,
                "unit": batch["unit"],
                "consume_by_date": recommended_consume_by_date(
                    category=category,
                    location_category=location_category,
                    reference_date=reference_date,
                ),
                "location_category": location_category,
                "category": category,
            }
        )
    return items
