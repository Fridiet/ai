from app.ingredient_matcher import IngredientCatalog, IngredientEntry
from scripts.import_mfds_recipes import (
    _split_ingredient_segments,
    map_mfds_row,
    merge_recipes,
    normalize_source_ingredient_name,
    parse_ingredients,
)


def _catalog() -> IngredientCatalog:
    return IngredientCatalog(
        [
            IngredientEntry("두부", ("연두부", "순두부")),
            IngredientEntry("새우", ("칵테일새우",)),
            IngredientEntry("달걀", ("계란",)),
            IngredientEntry("버터", ("무염버터",)),
            IngredientEntry("시금치", ()),
            IngredientEntry("소금", ()),
            IngredientEntry("후추", ()),
            IngredientEntry("연어", ("훈제연어",)),
            IngredientEntry("주꾸미", ("쭈꾸미",)),
            IngredientEntry("미나리", ()),
            IngredientEntry("양파", ()),
            IngredientEntry("당근", ()),
            IngredientEntry("마늘", ("다진마늘",)),
            IngredientEntry("고추장", ()),
            IngredientEntry("김치", ("묵은지",)),
            IngredientEntry("자두", ("말린 자두",)),
            IngredientEntry("우유", ()),
            IngredientEntry("꿀", ()),
        ]
    )


def test_parse_ingredients_maps_only_quantified_master_ingredients() -> None:
    ingredients = parse_ingredients(
        "찜 재료\n연두부 75g(3/4모), 칵테일새우 20g(5마리), "
        "생크림 13g(1큰술), 무염버터 5g(1작은술), 소금 약간",
        _catalog(),
    )

    assert ingredients == [
        {"canonical_name": "두부", "quantity": 75, "unit": "g", "optional": False},
        {"canonical_name": "새우", "quantity": 20, "unit": "g", "optional": False},
        {"canonical_name": "버터", "quantity": 5, "unit": "g", "optional": True},
    ]


def test_map_mfds_row_preserves_official_nutrition_and_steps() -> None:
    row = {
        "RCP_SEQ": "28",
        "RCP_NM": "새우 두부 계란찜",
        "RCP_WAY2": "찌기",
        "RCP_PAT2": "반찬",
        "RCP_PARTS_DTLS": (
            "연두부 75g(3/4모), 칵테일새우 20g(5마리), "
            "계란 30g(1/2개), 무염버터 5g(1작은술)\n시금치 10g(3줄기)"
        ),
        "INFO_WGT": "",
        "INFO_ENG": "220",
        "INFO_CAR": "3",
        "INFO_PRO": "14",
        "INFO_FAT": "17",
        "INFO_NA": "99",
        "ATT_FILE_NO_MAIN": "https://example.test/recipe.png",
        "MANUAL01": "1. 손질된 새우를 끓는 물에 데쳐 건진다.",
        "MANUAL02": "2. 재료를 섞어 그릇에 담는다.",
        "MANUAL03": "3. 찜기에서 10분간 찐다.",
    }

    recipe = map_mfds_row(row, _catalog())

    assert recipe is not None
    assert recipe["source"] == "MFDS_COOKRCP01"
    assert recipe["source_recipe_id"] == "28"
    assert recipe["recipe_name"] == "새우 두부 계란찜"
    assert recipe["steps"] == [
        "손질된 새우를 끓는 물에 데쳐 건진다.",
        "재료를 섞어 그릇에 담는다.",
        "찜기에서 10분간 찐다.",
    ]
    assert recipe["nutrition"] == {
        "serving_size_g": None,
        "calories_kcal": 220,
        "carbohydrate_g": 3,
        "protein_g": 14,
        "fat_g": 17,
        "sodium_mg": 99,
        "source": "MFDS_COOKRCP01",
    }
    assert [item["canonical_name"] for item in recipe["ingredients"]] == [
        "두부",
        "새우",
        "달걀",
        "버터",
        "시금치",
    ]
    assert {
        item["canonical_name"]
        for item in recipe["ingredients"]
        if item.get("primary")
    } == {"두부", "새우", "달걀"}


def test_parse_ingredients_keeps_core_mfds_ingredient_and_parenthesized_comma() -> None:
    ingredients = parse_ingredients(
        "연어(훈제연어, 120g), 주꾸미 200g, 미나리 20g, "
        "양파 25g, 당근 20g, 계란 60g, 다진마늘 15g, 고추장 15g, "
        "묵은지(¼포기)",
        _catalog(),
    )

    assert [item["canonical_name"] for item in ingredients] == [
        "연어",
        "주꾸미",
        "미나리",
        "양파",
        "당근",
        "달걀",
        "마늘",
        "고추장",
        "김치",
    ]
    assert ingredients[-1]["quantity"] == 0.25
    assert ingredients[-1]["unit"] == "포기"


def test_map_mfds_row_rejects_unusable_recipe() -> None:
    assert map_mfds_row({"RCP_SEQ": "1", "RCP_NM": "빈 레시피"}, _catalog()) is None


def test_map_mfds_row_keeps_parenthesized_title_ingredient_as_primary() -> None:
    row = {
        "RCP_SEQ": "1128",
        "RCP_NM": "자두 라떼",
        "RCP_PAT2": "후식",
        "RCP_PARTS_DTLS": "자두(200g), 우유(250g), 꿀(10g), 물(15g)",
        "MANUAL01": "자두를 깨끗이 씻어 얼린다.",
        "MANUAL02": "우유와 함께 간다.",
    }

    recipe = map_mfds_row(row, _catalog())

    assert recipe is not None
    assert [item["canonical_name"] for item in recipe["ingredients"]] == [
        "자두",
        "우유",
        "꿀",
    ]
    assert recipe["ingredients"][0]["primary"] is True
    assert recipe["ingredients"][0]["optional"] is False


def test_source_ingredient_name_removes_mfds_section_and_cut_labels() -> None:
    assert normalize_source_ingredient_name("[주재료] 감태") == "감태"
    assert normalize_source_ingredient_name("(속재료) 단호박") == "단호박"
    assert normalize_source_ingredient_name("한돈(사태)") == "한돈"
    assert normalize_source_ingredient_name("물(반죽용)(") == "물"
    assert normalize_source_ingredient_name("소금적당량 [팥소]팥") == "팥"
    assert normalize_source_ingredient_name("팔각(") == "팔각"


def test_split_ingredient_segments_recovers_missing_component_separator() -> None:
    assert _split_ingredient_segments(
        "연근 20g, 물 100g 홍시드레싱 : 홍시 30g, 요구르트 30g"
    ) == [
        "연근 20g",
        "물 100g",
        "홍시드레싱 : 홍시 30g",
        "요구르트 30g",
    ]
    assert _split_ingredient_segments("두부 75g(3/4모)") == ["두부 75g(3/4모)"]


def test_parse_ingredients_keeps_one_row_per_canonical_ingredient() -> None:
    ingredients = parse_ingredients("후추 5알, 후추 2ts, 소금 1g, 소금 2g", _catalog())

    assert ingredients == [
        {"canonical_name": "후추", "quantity": 5, "unit": "알", "optional": True},
        {"canonical_name": "소금", "quantity": 3, "unit": "g", "optional": True},
    ]


def test_merge_recipes_upserts_official_row_without_dropping_local_recipe() -> None:
    existing = [
        {"source_recipe_id": "fridiet-001", "recipe_name": "로컬"},
        {
            "source": "MFDS_COOKRCP01",
            "source_recipe_id": "28",
            "recipe_name": "이전 이름",
        },
    ]
    imported = [
        {
            "source": "MFDS_COOKRCP01",
            "source_recipe_id": "28",
            "recipe_name": "새 이름",
        },
        {
            "source": "MFDS_COOKRCP01",
            "source_recipe_id": "29",
            "recipe_name": "새 레시피",
        },
    ]

    merged = merge_recipes(existing, imported)

    assert [recipe["recipe_name"] for recipe in merged] == [
        "로컬",
        "새 이름",
        "새 레시피",
    ]
