from sqlalchemy import create_engine, text

from app.database import create_ocr_tables, confirm_inventory_candidates, save_inventory_candidates
from app.schemas import InventoryCandidatesResponse
from scripts.seed_ingredient_master import seed_ingredient_master


def sample_result() -> InventoryCandidatesResponse:
    return InventoryCandidatesResponse.model_validate(
        {
            "filename": "receipt.png",
            "count": 1,
            "inventory_candidates": [
                {
                    "row_index": 0,
                    "raw_text": "서울우유 1L 2980",
                    "recognized_product_name": "서울우유 1L",
                    "normalized_product_name": "서울우유",
                    "manufacturer": "서울우유협동조합",
                    "food_type": "우유",
                    "classification": "processed_food",
                    "prices": [2980],
                    "ingredient_master_candidate": {
                        "ingredient_id": None,
                        "ingredient_type": "BEVERAGE",
                        "canonical_name": "우유",
                        "default_unit": "ml",
                        "alias_text": "서울우유",
                        "match_confidence": 1.0,
                        "match_type": "exact",
                        "source": "seed_product_catalog",
                    },
                    "inventory_batch_candidate": {
                        "ingredient_id": None,
                        "consume_by_date": None,
                        "remaining_quantity": 1.0,
                        "unit": "l",
                        "quantity_source": "PACKAGE_TEXT",
                        "storage_type": "REFRIGERATED",
                        "storage_source": "CANONICAL_RULE",
                        "storage_confidence": 0.8,
                        "status": "ACTIVE",
                    },
                    "warnings": ["CONSUME_BY_DATE_REQUIRES_INPUT"],
                }
            ],
        }
    )


def test_saves_ocr_scan_and_maps_existing_ingredient() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_ocr_tables(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE ingredient_master ("
                "ingredient_id INTEGER PRIMARY KEY, canonical_name VARCHAR(255) NOT NULL)"
            )
        )
        connection.execute(
            text("INSERT INTO ingredient_master VALUES (4, '우유')")
        )

    saved = save_inventory_candidates(
        sample_result(),
        fridge_id=2,
        created_by=3,
        engine=engine,
    )

    assert saved["ocr_scan_id"] == 1
    assert saved["mapped_item_count"] == 1
    assert saved["items"][0]["ingredient_id"] == 4
    with engine.connect() as connection:
        scan_count = connection.execute(text("SELECT COUNT(*) FROM ocr_scan")).scalar_one()
        item_status = connection.execute(
            text("SELECT review_status FROM ocr_scan_item")
        ).scalar_one()
    assert scan_count == 1
    assert item_status == "PENDING_REVIEW"


def test_confirms_ocr_item_into_inventory_batch() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_ocr_tables(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE ingredient_master ("
                "ingredient_id INTEGER PRIMARY KEY, canonical_name VARCHAR(255) NOT NULL)"
            )
        )
        connection.execute(text("INSERT INTO ingredient_master VALUES (4, '우유')"))
        connection.execute(
            text(
                "CREATE TABLE inventory_batches ("
                "batch_id INTEGER PRIMARY KEY AUTOINCREMENT, fridge_id INTEGER NOT NULL, "
                "ingredient_id INTEGER NOT NULL, consume_by_date DATE, "
                "remaining_quantity DECIMAL(10,2) NOT NULL, unit VARCHAR(10) NOT NULL, "
                "status VARCHAR(20) NOT NULL, created_by INTEGER NOT NULL, "
                "storage_type VARCHAR(32) NOT NULL, source_type VARCHAR(20) NOT NULL, "
                "source_ocr_item_id INTEGER UNIQUE)"
            )
        )

    saved = save_inventory_candidates(sample_result(), fridge_id=2, created_by=3, engine=engine)
    item_id = saved["items"][0]["ocr_scan_item_id"]
    confirmed = confirm_inventory_candidates(
        saved["ocr_scan_id"],
        created_by=3,
        review_items=[
            {
                "ocr_scan_item_id": item_id,
                "decision": "CONFIRM",
                "ingredient_id": 4,
                "remaining_quantity": 1000.0,
                "unit": "ml",
                "consume_by_date": None,
                "storage_type": "REFRIGERATED",
            }
        ],
        engine=engine,
    )

    assert confirmed["status"] == "REVIEWED"
    assert confirmed["created_batch_count"] == 1
    with engine.connect() as connection:
        source_type = connection.execute(
            text("SELECT source_type FROM inventory_batches")
        ).scalar_one()
        review_status = connection.execute(
            text("SELECT review_status FROM ocr_scan_item")
        ).scalar_one()
    assert source_type == "OCR"
    assert review_status == "CONFIRMED"


def test_seeds_ingredient_master_without_duplicates() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE ingredient_master (ingredient_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "ingredient_type VARCHAR(20) NOT NULL, canonical_name VARCHAR(120) UNIQUE NOT NULL, "
                "default_unit VARCHAR(10))"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE ingredient_alias (alias_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "alias_text VARCHAR(120) NOT NULL, ingredient_id INTEGER NOT NULL, confidence DECIMAL(4,3))"
            )
        )

    first = seed_ingredient_master(engine)
    second = seed_ingredient_master(engine)

    assert first["dictionary_count"] > 89
    assert first["inserted_master_count"] == first["dictionary_count"]
    assert second["inserted_master_count"] == 0
    assert second["inserted_alias_count"] == 0
