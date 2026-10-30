from app.ingredient_matcher import get_ingredient_catalog
from app.inventory_mapper import (
    build_inventory_candidates,
    infer_storage_type,
    parse_inventory_quantity,
)
from app.product_catalog import get_product_catalog
from app.product_pipeline import analyze_ocr_items, normalize_product_name


def item(text: str, confidence: float, left: int, top: int, right: int, bottom: int) -> dict:
    return {
        "text": text,
        "confidence": confidence,
        "box": [[left, top], [right, top], [right, bottom], [left, bottom]],
    }


def test_normalizes_product_name() -> None:
    assert normalize_product_name("(행사) 서울우유 1L 2,980원") == "서울우유"
    assert normalize_product_name("비타500 100ml 1,200원") == "비타500"
    assert normalize_product_name("농심 오징어짬뽕124g") == "농심 오징어짬뽕"


def test_matches_alias_and_ocr_typo() -> None:
    catalog = get_ingredient_catalog()
    assert catalog.match("서울우유")["standard_name"] == "우유"
    fuzzy = catalog.match("토마토케찹")
    assert fuzzy["standard_name"] == "케첩"
    assert fuzzy["match_type"] == "alias"


def test_short_name_does_not_match_inside_unrelated_compound() -> None:
    catalog = get_ingredient_catalog()
    assert catalog.match("프리미엄 오이스터소스")["standard_name"] != "오이"

    oyster_blade = catalog.match(normalize_product_name("08 호주곡물오이스터블"))
    assert oyster_blade["standard_name"] == "부채살"
    assert oyster_blade["matched_alias"] == "오이스터블"


def test_processed_product_match_has_priority_over_ingredient() -> None:
    match = get_product_catalog().match("농심 오징어짬뽕")
    assert match["standard_food_name"] == "라면"
    assert match["food_type"] == "유탕면"
    assert match["classification"] == "processed_food"
    assert match["manufacturer"] == "농심"


def test_separates_rows_filters_noise_and_matches_ingredients() -> None:
    source = [
        item("상품명", 0.99, 5, 0, 70, 15),
        item("금액", 0.99, 200, 0, 250, 15),
        item("서울우유 1L", 0.96, 5, 30, 120, 48),
        item("2,980원", 0.94, 200, 31, 270, 48),
        item("풀무원 국산콩두부 300g", 0.93, 5, 60, 180, 78),
        item("1,800", 0.91, 200, 61, 260, 78),
        item("합계", 0.99, 5, 90, 60, 108),
        item("4,780원", 0.99, 200, 90, 270, 108),
        item("중복", 0.40, 5, 120, 50, 138),
        item("중복", 0.90, 5, 120, 50, 138),
        item("낮은신뢰도", 0.10, 5, 150, 100, 168),
    ]

    result = analyze_ocr_items(source)

    assert result["cleaned_count"] == 9
    assert result["row_count"] == 5
    assert result["product_count"] == 3
    assert result["products"][0]["normalized_name"] == "서울우유"
    assert result["products"][0]["prices"] == [2980]
    assert result["products"][0]["match"]["standard_food_name"] == "우유"
    assert result["products"][1]["normalized_name"] == "풀무원 국산콩두부"
    assert result["products"][1]["match"]["standard_food_name"] == "두부"
    assert result["products"][2]["match"]["match_type"] == "none"


def test_does_not_merge_tightly_spaced_receipt_rows_and_recovers_fresh_foods() -> None:
    names = [
        "청양고추(봉)",
        "고름어(대,해동)",
        "붉당근(봉)",
        "컷파인애플1kg",
        "약삼축(입)",
        "가나다진마늘(450G)",
    ]
    source: list[dict] = []
    for index, name in enumerate(names):
        top = index * 26
        source.extend(
            [
                item(name, 0.93, 0, top, 180, top + 40),
                item("2,980", 0.99, 220, top + 2, 280, top + 38),
                item("1", 0.99, 300, top + 2, 315, top + 38),
                item("2,980", 0.99, 340, top + 2, 400, top + 38),
            ]
        )

    result = analyze_ocr_items(source)

    assert result["row_count"] == 6
    assert [
        product["match"]["standard_food_name"] for product in result["products"]
    ] == ["청양고추", "고등어", "당근", "파인애플", "양상추", "다진마늘"]


def test_specific_ingredient_match_beats_generic_kfind_food_type() -> None:
    result = analyze_ocr_items([item("가나다진마늘(450G)", 0.99, 0, 0, 220, 20)])

    match = result["products"][0]["match"]

    assert match["standard_food_name"] == "다진마늘"
    assert match["classification"] == "ingredient"
    assert match["source"] == "ingredient_dictionary"

def test_pipeline_classifies_squid_noodle_as_ramen() -> None:
    result = analyze_ocr_items([item("농심 오징어짬뽕124g", 0.99, 0, 0, 220, 20)])
    product = result["products"][0]
    assert product["normalized_name"] == "농심 오징어짬뽕"
    assert product["match"]["standard_food_name"] == "라면"
    assert product["match"]["food_type"] == "유탕면"
    assert product["match"]["classification"] == "processed_food"


def test_parses_inventory_quantity_and_storage() -> None:
    assert parse_inventory_quantity("서울우유 1L") == {
        "quantity": 1.0,
        "unit": "l",
        "source": "PACKAGE_TEXT",
    }
    assert parse_inventory_quantity("상품명만 있음") == {
        "quantity": 1.0,
        "unit": "개",
        "source": "DEFAULT",
    }
    assert infer_storage_type("냉동 새우 400g", "새우")["storage_type"] == "FROZEN"
    assert infer_storage_type("서울우유 1L", "우유") == {
        "storage_type": "REFRIGERATED",
        "source": "CANONICAL_RULE",
        "confidence": 0.8,
    }


def test_builds_database_aligned_inventory_candidate() -> None:
    result = analyze_ocr_items([item("농심 오징어짬뽕124g", 0.99, 0, 0, 220, 20)])

    candidate = build_inventory_candidates(result)[0]

    assert candidate["ingredient_master_candidate"] == {
        "ingredient_id": None,
        "ingredient_type": "ETC",
        "canonical_name": "라면",
        "default_unit": "개",
        "alias_text": "농심 오징어짬뽕",
        "match_confidence": 1.0,
        "match_type": "exact",
        "source": "seed_product_catalog",
    }
    assert candidate["inventory_batch_candidate"] == {
        "ingredient_id": None,
        "consume_by_date": None,
        "remaining_quantity": 124.0,
        "unit": "g",
        "quantity_source": "PACKAGE_TEXT",
        "storage_type": "ROOM_TEMPERATURE",
        "storage_source": "CANONICAL_RULE",
        "storage_confidence": 0.8,
        "status": "ACTIVE",
    }
    assert "INGREDIENT_ID_REQUIRES_BACKEND_MAPPING" in candidate["warnings"]
    assert "CONSUME_BY_DATE_REQUIRES_INPUT" in candidate["warnings"]
