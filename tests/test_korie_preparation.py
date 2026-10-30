import pytest
from app.product_pipeline import extract_product_name, normalize_product_name
from scripts.prepare_korie_review import prepare


@pytest.mark.parametrize("raw,expected", [
    ("[7654321] 애호박 1,900 2 3,800", "애호박"),
    ("03*청양고추 2,500 1 2,500", "청양고추"),
    ("농심 오징어짬뽕124g 1,200 1 1,200", "농심 오징어짬뽕"),
    ("[7654321], 두부, 2,000, 1, 2,000", "두부"),
])
def test_synthetic_sku_formats(raw, expected):
    assert normalize_product_name(extract_product_name(raw)) == expected


def test_candidates_are_not_ground_truth(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("image_name,Item_Name,class\nexample,03*청양고추,\n", encoding="utf-8")
    result = prepare(source, "test")
    row = result["records"][0]
    assert row["candidate_name"] == "청양고추"
    assert row["split"] == "test"
    assert row["canonical_name"] is None and row["is_food"] is None
    assert row["reviewed"] is False
    assert result["receipts"] == 1


def test_missing_columns_rejected(tmp_path):
    source = tmp_path / "input.csv"
    source.write_text("name\nexample\n", encoding="utf-8")
    with pytest.raises(ValueError):
        prepare(source, "train")
