import pytest
from app.product_pipeline import analyze_ocr_items, extract_product_name
from app.receipt_mapper import build_review_candidates, build_receipt_items
from app.inventory_mapper import build_inventory_candidates
from app.receipt_name_evidence import clear_nonfood, explicit_food_head


def rows(texts):
    return [{"text":t,"confidence":.99,"box":[[0,i*40],[900,i*40],[900,i*40+20],[0,i*40+20]]} for i,t in enumerate(texts)]


def test_prefix_number_does_not_erase_chili():
    assert extract_product_name("* (990)청양고추(봉) 990 990") == "청양고추(봉)"


def test_missing_count_kept_for_review(monkeypatch):
    monkeypatch.setattr("app.product_pipeline.apply_classification", lambda text, original, catalog: dict(original, standard_food_name=None))
    review = build_review_candidates(analyze_ocr_items(rows(["알수없는상품 3,980 ] 3,980"])))
    assert len(review) == 1 and review[0]["purchaseCount"] is None


def test_food_heads_and_nonfood_do_not_call_model(monkeypatch):
    monkeypatch.setenv("OCR_CLASSIFIER_ENABLED", "true")
    monkeypatch.setattr("app.ocr_classification.predict_product", lambda _: pytest.fail("unnecessary model"))
    analysis = analyze_ocr_items(rows([
        "동원비피더스명장12입 3,980 ] 3,980", "유기농 모둠쌈(300g) 2,390 1 2,390",
        "* (990)청양고추(봉) 990 990", "MB2호주냉장등심 13,340 1 13,340",
        "친환경유정란 15개입 6,380 1 6,380", "카스후레쉬캔355m]*6 8,220 1 8,220",
        "79옷칠미니볶음스푼기 4,900 1 4,900", "N코럴스텐국자(소) 9,500 1 9,500",
        "원형면봉200개 2,900 1 2,900", "불스원샷휘발유용 500 27,000 1 27,000"] ))
    items = build_receipt_items(build_inventory_candidates(analysis))
    assert [i["name"] for i in items] == ["요구르트", "쌈채소", "청양고추", "등심", "달걀", "맥주"]
    assert items[4]["quantity"] == 15
    assert build_review_candidates(analysis) == []


def test_spoon_brand_is_not_household():
    assert not clear_nonfood("한스푼설탕")


def test_receipt_footer_is_not_a_review_product():
    analysis = analyze_ocr_items(rows(["상품명 단가 수량 금액", "알수없는상품 2,000 ] 2,000",
                                       "총 품목 수량 1", "가 세 5,407"]))
    review = build_review_candidates(analysis)
    assert len(review) == 1 and review[0]["rawText"].startswith("알수없는상품")


@pytest.mark.parametrize("text", ["유정란빵", "등심소스", "샴푸유정란", "과자모둠쌈"])
def test_food_head_counterexamples(text):
    assert explicit_food_head(text) is None
