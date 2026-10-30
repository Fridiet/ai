import pytest
from app.receipt_name_evidence import explicit_food_head
from app.ocr_classification import apply_classification
from app.ingredient_matcher import get_ingredient_catalog


@pytest.mark.parametrize('text,expected', [
    ('노브랜드그린올리브', '그린올리브'),
    ('블랙올리브200g', '블랙올리브'),
    ('노브랜드미고렝누들', '미고랭누들'),
    ('미고랭누들', '미고랭누들'),
    ('NB)해쉬브라운감자', '해시브라운'),
    ('해시브라운', '해시브라운'),
    ('노브랜드단백질바초코', '단백질바'),
    ('우유카스테라', '카스테라'),
    ('노브랜드soft버터쿠키', '쿠키'),
    ('노브랜드체다치즈볼', '치즈볼'),
    ('웨이퍼롤바닐라', '웨이퍼'),
    ('초코웨이퍼', '웨이퍼'),
    ('콰트로치즈피자', '피자'),
    ('불고기피자', '피자'),
    ('말랑쫀득건무화과', '건무화과'),
    ('건자두300G', '푸룬'),
    ('건포도200g', '건포도'),
    ('건망고', '건망고'),
    ('모닝롤90G', '모닝빵'),
    ('우유케이크', '케이크'),
    ('노브랜드망고음료1.5', '망고음료'),
    ('사과주스1L', '사과주스'),
    ('오렌지음료500ml', '오렌지음료'),
    ('칠성사이다제로P500', '제로사이다'),
    ('제로사이다500ml', '제로사이다'),
    ('코카콜라제로1.5L', '제로콜라'),
])
def test_preserves_explicit_product(text, expected, monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    monkeypatch.setattr('app.ocr_classification.predict_product', lambda _: pytest.fail('unnecessary model'))
    assert explicit_food_head(text)[0] == expected
    original = dict(standard_food_name='올리브' if '올리브' in text else '면류',
                    source='ingredient_dictionary', match_type='contains', matched_name='올리브')
    result = apply_classification(text, original, get_ingredient_catalog())
    assert result['standard_food_name'] == expected


@pytest.mark.parametrize('text', ['그린올리브오일', '블랙올리브소스', '그린올리브샴푸',
                                  '미고랭누들소스', '올리브', '미고랭'])
def test_does_not_invent_truncated_or_compound_identity(text):
    head = explicit_food_head(text)
    assert head is None or head[0] not in {'그린올리브', '블랙올리브', '미고랭누들'}


def test_verified_catalog_identity_is_not_overridden(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    original = dict(standard_food_name='검증된 상품', inventory_identity_verified=True,
                    source='K-FIND:test', match_type='exact')
    assert apply_classification('그린올리브', original, get_ingredient_catalog()) == original


@pytest.mark.parametrize('text', ['해시브라운소스', '단백질바음료', '버터쿠키향샴푸', '카스테라믹스', '치즈볼소스', '웨이퍼롤바닐라향샴푸', '피자소스', '피자맛과자'])
def test_prepared_heads_do_not_override_other_product_types(text):
    assert explicit_food_head(text) is None


@pytest.mark.parametrize('text', ['건무화과소스', '건자두주스', '건망고맛과자',
                                  '모닝롤믹스', '케이크향샴푸', '건자투300G'])
def test_dried_and_bakery_heads_require_actual_identity(text):
    assert explicit_food_head(text) is None


@pytest.mark.parametrize('text', ['사과주스향샴푸', '망고음료믹스', '망고', '망고음', '사과포도주스'])
def test_named_beverage_does_not_guess_truncation_or_mixed_fruit(text):
    head = explicit_food_head(text)
    assert head is None or head[0] not in {'망고음료', '사과주스', '포도주스'}


@pytest.mark.parametrize('text', ['콜라', '사이다', '사이다제로젤리', '제로콜라맛과자', '제로콜라향샴푸'])
def test_zero_variant_requires_printed_beverage_head(text):
    assert explicit_food_head(text) is None


def test_receipt_output_keeps_product_and_verified_count(monkeypatch):
    from app.product_pipeline import analyze_ocr_items
    from app.inventory_mapper import build_inventory_candidates
    from app.receipt_mapper import build_receipt_items
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    texts = ['노브랜드그린올리브 1,980 1 1,980', '노브랜드미고렝누들 2,580 2 5,160']
    rows = [{'text': text, 'confidence': .99,
             'box': [[0, i*40], [700, i*40], [700, i*40+20], [0, i*40+20]]}
            for i, text in enumerate(texts)]
    response = build_receipt_items(build_inventory_candidates(analyze_ocr_items(rows)))
    assert [(item['name'], item['quantity']) for item in response] == [('그린올리브', 1), ('미고랭누들', 2)]
