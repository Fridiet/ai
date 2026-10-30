from datetime import date

from app.receipt_mapper import build_receipt_items


def test_build_receipt_items_maps_team_contract() -> None:
    candidates = [
        {
            "classification": "ingredient",
            "ingredient_master_candidate": {
                "canonical_name": "당근",
                "ingredient_type": "INGREDIENT",
            },
            "inventory_batch_candidate": {
                "remaining_quantity": 2.0,
                "unit": "개",
                "storage_type": "REFRIGERATED",
            },
        }
    ]

    result = build_receipt_items(candidates, reference_date=date(2026, 8, 18))
    assert result[0].pop("raw_text") is None
    assert len(result[0].pop("review_notes")) == 3
    assert result[0].pop('_product_name') is None
    assert result[0].pop('_storage_source') == 'UNKNOWN'
    assert result[0].pop('_date_source') == 'CATEGORY_FALLBACK'
    assert result == [
        {
            "name": "당근",
            "quantity": 2,
            "unit": "개",
            "consume_by_date": date(2026, 8, 25),
            "location_category": "refrigerated",
            "category": "vegetable",
        }
    ]


def test_build_receipt_items_classifies_processed_food() -> None:
    candidates = [
        {
            "classification": "processed_food",
            "ingredient_master_candidate": {
                "canonical_name": "라면",
                "ingredient_type": "ETC",
            },
            "inventory_batch_candidate": {
                "remaining_quantity": 5.0,
                "unit": "개",
                "storage_type": "ROOM_TEMPERATURE",
            },
        }
    ]

    item = build_receipt_items(candidates, reference_date=date(2026, 8, 18))[0]
    assert item["category"] == "processed_food"
    assert item["location_category"] == "room_temperature"
    assert item["consume_by_date"] == date(2026, 11, 16)
