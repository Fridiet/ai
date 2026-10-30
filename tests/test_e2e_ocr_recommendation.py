from scripts.e2e_ocr_recommendation import extract_inventory_drafts


def test_extract_inventory_drafts_uses_standardized_ocr_candidates() -> None:
    drafts = extract_inventory_drafts(
        {
            "inventory_candidates": [
                {
                    "normalized_product_name": "국산 당근",
                    "ingredient_master_candidate": {
                        "canonical_name": "당근",
                        "default_unit": "g",
                    },
                    "inventory_batch_candidate": {
                        "remaining_quantity": 500,
                        "unit": "g",
                        "consume_by_date": "2026-08-27",
                        "storage_type": "REFRIGERATED",
                    },
                },
                {
                    "normalized_product_name": "흙당근",
                    "ingredient_master_candidate": {
                        "canonical_name": "당근",
                    },
                    "inventory_batch_candidate": {},
                },
            ]
        }
    )

    assert len(drafts) == 1
    assert drafts[0].name == "당근"
    assert drafts[0].quantity == 500
    assert drafts[0].unit == "g"
    assert drafts[0].consume_by_date == "2026-08-27"
    assert drafts[0].location_category == "REFRIGERATED"


def test_extract_inventory_drafts_supports_receipt_contract() -> None:
    drafts = extract_inventory_drafts(
        {
            "items": [
                {
                    "name": "고등어",
                    "quantity": 2,
                    "unit": "마리",
                    "consumeByDate": "2026-08-23",
                    "locationCategory": "frozen",
                }
            ]
        }
    )

    assert len(drafts) == 1
    assert drafts[0].name == "고등어"
    assert drafts[0].location_category == "FROZEN"
