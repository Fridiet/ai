from app.ingredient_matcher import get_ingredient_catalog
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items
from app.receipt_name_evidence import fresh_suffix


def test_rockfish_keeps_species_through_receipt_output(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    assert any(e.name == '붉은볼락' for e in get_ingredient_catalog().entries)
    raw = [dict(text='저염간붉은볼락 2,980 1 2,980', confidence=.99,
                box=[[0,0],[500,0],[500,30],[0,30]])]
    analysis = analyze_ocr_items(raw)
    items = build_receipt_items(build_inventory_candidates(analysis))
    assert len(items) == 1
    assert items[0]['name'] == '붉은볼락'
    assert items[0]['category'] == 'seafood'
    assert items[0]['quantity'] == 1


def test_species_evidence_does_not_guess_damaged_or_compound_name():
    assert fresh_suffix('저염간붉은볼락') == '붉은볼락'
    assert fresh_suffix('저염간북은볼락') is None
    assert fresh_suffix('붉은볼락소스') is None
    assert fresh_suffix('고등어붉은볼락') is None
