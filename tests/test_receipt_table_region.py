from app.product_pipeline import analyze_ocr_items


def lines(*texts):
    return [{"text": text, "confidence": 0.99,
             "box": [[0, i*40], [900, i*40], [900, i*40+20], [0, i*40+20]]}
            for i, text in enumerate(texts)]


def test_header_excludes_store_address():
    result = analyze_ocr_items(lines("충북 청주시", "상품 명 단가 수량 금액",
        "당근 1,000 2 2,000", "합계 2,000"))
    assert [r["raw_text"] for r in result["products"]] == ["당근 1,000 2 2,000"]


def test_without_header_does_not_drop_cropped_product():
    result = analyze_ocr_items(lines("당근 1,000 2 2,000"))
    assert len(result["products"]) == 1


def test_sandwich_bread_does_not_fuzzy_match_yeast():
    result = analyze_ocr_items(lines("001삼립)토스트&샌드위치식빵768g"))
    assert result["products"][0]["match"]["standard_food_name"] == "식빵"
