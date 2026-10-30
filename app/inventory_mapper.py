import re
from typing import Any

from .ingredient_matcher import get_ingredient_catalog
from .ingredient_taxonomy import family, PROCESSED_NAMES
from .receipt_name_evidence import egg_product_head


PACKAGE_QUANTITY_PATTERN = re.compile(
    r"(?<!\d)(\d+(?:\.\d+)?)\s*(kg|mg|ml|g|l|개입|개|입|팩|봉|병|캔|박스|포|장|매|ea)(?:(?![A-Za-z가-힣])|(?=[xX]\s*\(?\s*\d))",
    re.IGNORECASE,
)

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
BEVERAGE_NAMES = {"우유", "두유"}
ETC_NAMES = {"라면"}
COUNT_UNIT_NAMES = {"달걀", "김"}
LIQUID_UNIT_NAMES = {
    "간장",
    "식초",
    "식용유",
    "올리브유",
    "참기름",
    "들기름",
    "케첩",
    "마요네즈",
}

REFRIGERATED_NAMES = {
    "등심", "쌈채소",
    "대파",
    "쪽파",
    "당근",
    "무",
    "배추",
    "양배추",
    "상추",
    "양상추",
    "깻잎",
    "시금치",
    "콩나물",
    "숙주",
    "오이",
    "애호박",
    "가지",
    "토마토",
    "파프리카",
    "피망",
    "고추",
    "브로콜리",
    "버섯",
    "소고기",
    "돼지고기",
    "닭고기",
    "오리고기",
    "달걀",
    "고등어",
    "붉은볼락",
    "연어",
    "오징어",
    "새우",
    "조개",
    "우유",
    "치즈",
    "버터",
    "요구르트",
    "두부",
    "햄",
    "소시지",
    "베이컨",
    "어묵",
    "맛살",
    "떡",
    "딸기",
    "포도",
    "블루베리",
    "파인애플",
}
ROOM_TEMPERATURE_NAMES = {
    "감자",
    "고구마",
    "양파",
    "마늘",
    "생강",
    "단호박",
    "참치",
    "멸치",
    "김",
    "두유",
    "쌀",
    "현미",
    "잡곡",
    "밀가루",
    "콩",
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
    "사과",
    "배",
    "바나나",
    "귤",
    "오렌지",
    "수박",
    "레몬",
    "복숭아",
    "아보카도",
    "라면",
}

STORAGE_TEXT_PATTERNS = (
    ("FROZEN", re.compile(r"냉동|동결", re.IGNORECASE)),
    ("REFRIGERATED", re.compile(r"냉장|신선", re.IGNORECASE)),
    ("ROOM_TEMPERATURE", re.compile(r"실온|상온", re.IGNORECASE)),
)


def _normalized_unit(unit: str) -> str:
    normalized = unit.lower()
    if normalized in {"ea", "입", "개입", "구"}:
        return "개"
    if normalized == "매":
        return "장"
    return normalized


def parse_inventory_quantity(text: str, canonical_name: str | None = None) -> dict[str, Any]:
    # An explicitly printed contents + bundle suffix is not a transaction count.
    # Bare receipt price/count columns do not satisfy this anchored pattern.
    bundled = re.search(r'(?<![\d.])\d+(?:\.\d+)?\s*(?:kg|mg|ml|g|l)\s*[, ]+\d+\s*(?:개입|입|개|팩|봉|병|캔)(?:\s*[, ]+1\s*(?:개|세트))?\s*$', text, re.I)
    if bundled:
        from .online_order import package_quantity
        contents = package_quantity(text)
        if contents['source'] != 'DEFAULT':
            return contents
    # Do not skip an unreadable volume unit and accidentally accept only the
    # following multipack count as a confidently parsed package size.
    if re.search(r'\d+\s*m1(?:\s*[*x×]\s*\d+(?:개입|입|개)?)?\s*$', text, re.I):
        return {"quantity": 1.0, "unit": "개", "source": "DEFAULT"}
    match = PACKAGE_QUANTITY_PATTERN.search(text)
    # 구 is a count unit only for a legible whole-egg product, not a district,
    # product code, cooked egg dish, or a guessed missing unit.
    if canonical_name == '달걀' and egg_product_head(text) == '달걀':
        egg_count = re.search(r'(?<![\d.])([1-9]\d{0,2})\s*(구)\s*$', text)
        if egg_count is not None:
            match = egg_count
    # CT is a printed package piece count for a clearly identified dinner-roll
    # product, not a generic SKU suffix or the number of packages purchased.
    if canonical_name == '디너롤':
        roll_count = re.fullmatch(r'\s*(?:뉴)?디너롤\s*([1-9]\d{0,2})\s*(CT)\s*', text, re.I)
        if roll_count is not None:
            return {'quantity':float(roll_count.group(1)), 'unit':'개', 'source':'PACKAGE_TEXT'}
    if match is None:
        return {"quantity": 1.0, "unit": "개", "source": "DEFAULT"}
    multiplier = 1
    tail = text[match.end():]
    if match.group(2).lower() in {'kg','mg','ml','g','l'} and re.match(
            r'\s*(?:[*x×]\s*)?\(?\s*\d+\s*\+', tail, re.I):
        # A trailing package-count unit distinguishes printed bundle contents
        # from an unqualified 1+1 sales promotion. Never evaluate expressions.
        expression = re.sub(r'^\s*[*x×]\s*', '', tail, flags=re.I).strip()
        if expression.startswith('(') and expression.endswith(')'):
            expression = expression[1:-1].strip()
        additive = re.fullmatch(
            r'(\()?\s*([1-9]\d{0,2})\s*\+\s*([1-9]\d{0,2})\s*(\))?\s*(?:개입|입|병|캔)',
            expression)
        if additive is None or bool(additive.group(1)) != bool(additive.group(4)):
            return {"quantity": 1.0, "unit": "개", "source": "DEFAULT"}
        multiplier = int(additive.group(2)) + int(additive.group(3))
        if multiplier > 999:
            return {"quantity": 1.0, "unit": "개", "source": "DEFAULT"}
    elif re.match(r'\s*[*x×]', tail, re.I):
        bundle = re.fullmatch(r'\s*[*x×]\s*([1-9]\d{0,2})(?:개입|입|개|병|캔)?\s*[)\]]?\s*', tail, re.I)
        if bundle is None:
            return {"quantity": 1.0, "unit": "개", "source": "DEFAULT"}
        multiplier = int(bundle.group(1))
    return {
        "quantity": float(match.group(1)) * multiplier,
        "unit": _normalized_unit(match.group(2)),
        "source": "PACKAGE_TEXT",
    }


def infer_ingredient_type(canonical_name: str, food_type: str | None) -> str:
    if canonical_name in PROCESSED_NAMES:
        return 'ETC'
    if family(canonical_name) in SEASONING_NAMES:
        return "SEASONING"
    if food_type in {'소스', '복합조미식품', '액젓'}:
        return 'SEASONING'
    if canonical_name in BEVERAGE_NAMES or (
        food_type and any(keyword in food_type for keyword in ("음료", "주스", "커피", "차류"))
    ):
        return "BEVERAGE"
    if canonical_name in ETC_NAMES:
        return "ETC"
    ingredient_names = {entry.name for entry in get_ingredient_catalog().entries}
    if canonical_name in ingredient_names:
        return "INGREDIENT"
    return "ETC"


def infer_default_unit(
    canonical_name: str,
    ingredient_type: str,
) -> str:
    if ingredient_type == "BEVERAGE" or canonical_name in LIQUID_UNIT_NAMES:
        return "ml"
    if canonical_name in COUNT_UNIT_NAMES or ingredient_type == "ETC":
        return "개"
    return "g"


def infer_storage_type(
    raw_text: str,
    canonical_name: str,
) -> dict[str, Any]:
    for storage_type, pattern in STORAGE_TEXT_PATTERNS:
        if pattern.search(raw_text):
            return {
                "storage_type": storage_type,
                "source": "RECEIPT_TEXT",
                "confidence": 1.0,
            }
    if canonical_name in PROCESSED_NAMES | {"다진마늘", "멸균우유", "건새우", "새우가루", "황태", "코다리", "현미가루", "동태"}:
        return {"storage_type": "UNKNOWN", "source": "UNKNOWN", "confidence": 0.0}
    canonical_name = family(canonical_name)
    if canonical_name in REFRIGERATED_NAMES:
        return {
            "storage_type": "REFRIGERATED",
            "source": "CANONICAL_RULE",
            "confidence": 0.8,
        }
    if canonical_name in ROOM_TEMPERATURE_NAMES:
        return {
            "storage_type": "ROOM_TEMPERATURE",
            "source": "CANONICAL_RULE",
            "confidence": 0.8,
        }
    return {"storage_type": "UNKNOWN", "source": "UNKNOWN", "confidence": 0.0}


def build_inventory_candidates(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for product in analysis["products"]:
        match = product["match"]
        canonical_name = match["standard_food_name"]
        if canonical_name is None:
            continue

        if product.get('order_package_quantity'):
            quantity = dict(product['order_package_quantity'])
        else:
            quantity = dict(product['llm_quantity_snapshot']) if product.get('llm_quantity_snapshot') else parse_inventory_quantity(product["product_name"], canonical_name)
        package_unknown = quantity["source"] == "DEFAULT"
        purchase_count = product.get("purchase_quantity")
        if purchase_count is not None:
            quantity["quantity"] *= purchase_count
            quantity["source"] = product.get('purchase_quantity_source') or 'VERIFIED_RECEIPT_COLUMNS'
        ingredient_type = infer_ingredient_type(canonical_name, match["food_type"])
        default_unit = infer_default_unit(
            canonical_name,
            ingredient_type,
        )
        storage = infer_storage_type(product["raw_text"], canonical_name)
        warnings = [
            "INGREDIENT_ID_REQUIRES_BACKEND_MAPPING",
            "CONSUME_BY_DATE_REQUIRES_INPUT",
        ]
        if product.get('llm_corrected'):
            warnings.append('GPT_IDENTITY_REQUIRES_CONFIRMATION')
        if quantity["source"] == "DEFAULT":
            warnings.append("QUANTITY_DEFAULTED_TO_ONE")
        if package_unknown:
            # A verified transaction count can represent purchased units even
            # when the receipt prints no package contents. Numeric fragments
            # may be damaged size text (900m1, 124...), so keep those reviewable.
            purchase_units_only = (
                purchase_count is not None
                and product.get('purchase_quantity_source') in {
                    'VERIFIED_RECEIPT_COLUMNS', 'VERIFIED_RECEIPT_TOTALS'}
                and not re.search(r'\d', product['product_name'])
            )
            warnings.append('PURCHASE_UNIT_COUNT_ONLY' if purchase_units_only
                            else 'PACKAGE_SIZE_REQUIRES_CONFIRMATION')
        if quantity['source']=='RECONSTRUCTED_PRICE_COLUMNS':
            warnings.append('PURCHASE_QUANTITY_RECONSTRUCTED_FROM_PRICES')
        if quantity['source']=='NUMERIC_CELLS_VERIFIED':
            warnings.append('PURCHASE_QUANTITY_FROM_NUMERIC_CELLS_REQUIRES_CONFIRMATION')
        if quantity['source']=='SECONDARY_VERIFIED_RECEIPT_COLUMNS':
            warnings.append('PURCHASE_QUANTITY_FROM_SECONDARY_OCR_REQUIRES_CONFIRMATION')
        if purchase_count is None:
            warnings.append("PURCHASE_QUANTITY_REQUIRES_CONFIRMATION")
        if storage["storage_type"] == "UNKNOWN":
            warnings.append("STORAGE_TYPE_REQUIRES_CONFIRMATION")
        elif storage["source"] == "CANONICAL_RULE":
            warnings.append("STORAGE_TYPE_IS_SUGGESTION")

        candidates.append(
            {
                "row_index": product["row_index"],
                "raw_text": product["raw_text"],
                "recognized_product_name": product["product_name"],
                "normalized_product_name": product["normalized_name"],
                "manufacturer": match["manufacturer"],
                "food_type": match["food_type"],
                "classification": match["classification"],
                "prices": product["prices"],
                "ingredient_master_candidate": {
                    "ingredient_id": None,
                    "ingredient_type": ingredient_type,
                    "canonical_name": canonical_name,
                    "default_unit": default_unit,
                    "alias_text": (
                        product["normalized_name"]
                        if product["normalized_name"] != canonical_name
                        else None
                    ),
                    "match_confidence": match["score"],
                    "match_type": match["match_type"],
                    "source": match["source"],
                },
                "inventory_batch_candidate": {
                    "ingredient_id": None,
                    "consume_by_date": None,
                    "remaining_quantity": quantity["quantity"],
                    "unit": quantity["unit"],
                    "quantity_source": quantity["source"],
                    "storage_type": storage["storage_type"],
                    "storage_source": storage["source"],
                    "storage_confidence": storage["confidence"],
                    "status": "ACTIVE",
                },
                "warnings": warnings,
            }
        )
    return candidates
