import pytest
from app.receipt_name_evidence import fresh_suffix
from app.product_pipeline import analyze_ocr_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_mapper import build_receipt_items, build_review_candidates


@pytest.mark.parametrize("text,name", [
    ("* 친환경복주머니양파", "양파"), ("* 송송어린대파", "대파"),
    ("국산양파 1kg", "양파"), ("흙당근(봉)", "당근"),
    ("청양고추(봉)", "청양고추"), ("컷파인애플1kg", "파인애플"),
    ("파머스픽 흙대파(봉)", "대파"), ("양상추(봉)", "양상추"),
    ("고등어(대,해동)", "고등어"), ("파머스픽 팽이버섯(봉)", "팽이버섯"),
])
def test_fresh_product_ending(text, name):
    assert fresh_suffix(text) == name


@pytest.mark.parametrize("text", [
    "호주곡물오이스터블", "농심 오징어짬뽕124g", "양파링", "양파맛과자",
    "대파크림치즈", "양파소스", "감자칩", "오이비누", "사과샴푸", "양파즙",
    "과자 양파", "샴푸 오이", "양파대파", "모듬양파", "분말양파", "향신료", "양파(과자)", "오이(비누)", "양파(장난감)",
])
def test_compounds_are_not_raw_ingredients(text):
    assert fresh_suffix(text) is None


def test_screen_rows_return_names_without_model(monkeypatch):
    monkeypatch.setenv("OCR_CLASSIFIER_ENABLED", "true")
    monkeypatch.setattr("app.ocr_classification.predict_product", lambda _: pytest.fail("unnecessary model inference"))
    texts = ["* 친환경복주머니양파 3,980 1 3,980", "* 송송어린대파 2,980 1 2,980",
             "농심 오징어짬뽕124g 1,000 1 1,000", "사과 샴푸 3,000 1 3,000"]
    rows = [{"text": text, "confidence": .99, "box": [[0,i*40],[700,i*40],[700,i*40+20],[0,i*40+20]]}
            for i,text in enumerate(texts)]
    analysis = analyze_ocr_items(rows)
    result = build_receipt_items(build_inventory_candidates(analysis))
    assert [item["name"] for item in result] == ["양파", "대파", "라면"]
    assert [item["quantity"] for item in result[:2]] == [1,1]
    assert build_review_candidates(analysis) == []
