import pytest
from app.ocr_classification import apply_classification
from app.ingredient_matcher import get_ingredient_catalog
from app.product_pipeline import analyze_ocr_items, is_adjustment_row
from app.receipt_name_evidence import fresh_suffix, clear_nonfood
from app.receipt_name_evidence import explicit_food_head


@pytest.mark.parametrize('text', ['오리온 눈을감자', '고구마 스틱', '당근 주스', '농심 감자'])
def test_processed_context_not_raw_suffix(text):
    assert fresh_suffix(text) is None


def test_manufacturer_milk_is_not_coffee_identity(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _: None)
    original = dict(standard_food_name='우유', source='seed_product_catalog',
                    match_type='contains', matched_name='서울우유')
    result = apply_classification('서울우유 모리커피 컵', original, get_ingredient_catalog())
    assert result['standard_food_name'] is None


def test_plain_milk_still_accepted(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _: pytest.fail('no model needed'))
    original = dict(standard_food_name='우유', source='seed_product_catalog',
                    match_type='contains', matched_name='서울우유')
    assert apply_classification('서울우유 1000ml', original, get_ingredient_catalog()) is original


def test_snack_brand_does_not_block_explicit_cracker(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _: pytest.fail('explicit product'))
    original = dict(standard_food_name='크래커', source='ingredient_dictionary',
                    match_type='contains', matched_name='크래커')
    assert apply_classification('크라운 참크래커 2번', original, get_ingredient_catalog()) is original


def test_drink_brand_not_orange_fruit(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _: None)
    original = dict(standard_food_name='오렌지', source='ingredient_dictionary',
                    match_type='contains', matched_name='오렌지')
    assert apply_classification('해태 써니텐 오렌지1.5L', original,
                                get_ingredient_catalog())['standard_food_name'] is None


@pytest.mark.parametrize('text',['[L_일인]40% 7,714','[M+N] 1+1 12,980','[번들]50% 2,700'])
def test_pos_discount_line_not_product(text):
    assert is_adjustment_row(text)


def test_discount_product_name_not_discarded():
    assert not is_adjustment_row('[행사] 우유 2,000 2 4,000')


def test_bag_not_food():
    assert clear_nonfood('재사용봉투20L')
    assert clear_nonfood('재시용봉투20L')


def test_tax_section_stops_classifying_payment(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','false')
    texts=['상품명 단가 수량 금액','당근 1,000 1 1,000','면세 물품가액 1,000',
           '정구액(일시) CARD-IC 1,000']
    boxes=[dict(text=t,confidence=.99,box=[[0,i*40],[600,i*40],[600,i*40+20],[0,i*40+20]])
           for i,t in enumerate(texts)]
    assert len(analyze_ocr_items(boxes)['products']) == 1


@pytest.mark.parametrize('text,name', [('심립모닝앤데이식빵','식빵'),
    ('고구마식빵','식빵'),('크런치믹스샐러드','샐러드'),('노브랜드미네랄워터','물')])
def test_prepared_heads_keep_prepared_identity(text,name):
    assert explicit_food_head(text)[0]==name


@pytest.mark.parametrize('text',['샐러드소스','식빵맛과자','미네랄워터향샴푸'])
def test_prepared_head_not_substring(text):
    assert explicit_food_head(text) is None


@pytest.mark.parametrize('text,name', [
    ('직수입구운캐슈너트', '캐슈넛'), ('직수입구문캐슈너트', '캐슈넛'),
    ('토마토스파게티소스', '토마토소스'), ('토마토파스타소스500g', '토마토소스'),
    ('크런치믹스샐러드_30', '샐러드')])
def test_explicit_identity_with_receipt_modifiers(text, name):
    assert explicit_food_head(text)[0] == name


@pytest.mark.parametrize('text', ['캐슈너트음료', '캐슈너트맛과자',
    '토마토크림소스', '로제토마토소스', '샐러드_소스', '샐러드30종소스'])
def test_modifiers_do_not_erase_different_product_identity(text):
    assert explicit_food_head(text) is None


@pytest.mark.parametrize('text,name', [('토마토스파게티소스','토마토소스'),
    ('크런치믹스샐러드_30','샐러드'), ('직수입구문캐슈너트','캐슈넛')])
def test_explicit_product_survives_model_abstention(monkeypatch, text, name):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    monkeypatch.setattr('app.ocr_classification.predict_product',
                        lambda _: pytest.fail('Explicit identity should precede learned fallback'))
    original = dict(standard_food_name=None, source='ingredient_dictionary',
                    match_type='none', matched_name=None)
    assert apply_classification(text, original, get_ingredient_catalog())['standard_food_name'] == name


@pytest.mark.parametrize('text,partial,expected', [
    ('노브랜드바닐라웨이퍼','바닐라','웨이퍼'),
    ('딸기웨이퍼','딸기','웨이퍼'), ('폰타나 사우전 드레싱','사우전','드레싱'),
    ('블루베리잼','블루베리','블루베리잼')])
def test_prepared_identity_overrides_flavor_component(monkeypatch, text, partial, expected):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    monkeypatch.setattr('app.ocr_classification.predict_product', lambda _: None)
    original = dict(standard_food_name=partial, matched_name=partial,
                    match_type='contains', source='ingredient_dictionary')
    assert apply_classification(text, original, get_ingredient_catalog())['standard_food_name'] == expected


@pytest.mark.parametrize('text,expected', [('감자칩사워크림','감자칩'),
    ('노브랜드감자칩어니언110g','감자칩'), ('노브랜드사과잼800g','사과잼'),
    ('블루베리잼','블루베리잼')])
def test_explicit_snack_and_jam_identity(text, expected):
    assert explicit_food_head(text)[0] == expected


@pytest.mark.parametrize('text', ['감자칩소스', '감자칩모양', '사과잼향샴푸'])
def test_snack_and_jam_substrings_not_promoted(text):
    assert explicit_food_head(text) is None


def test_sour_cream_flavor_not_sour_cream_ingredient(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    monkeypatch.setattr('app.ocr_classification.predict_product', lambda _: None)
    original = dict(standard_food_name='사워크림', matched_name='사워크림',
                    match_type='contains', source='ingredient_dictionary')
    assert apply_classification('감자칩사워크림', original, get_ingredient_catalog())['standard_food_name'] == '감자칩'


@pytest.mark.parametrize('text,partial,expected', [
    ('롯데아몬드빼빼로37g', '아몬드', '빼빼로'),
    ('생크림크라상(5입)', '생크림', '크루아상'),
    ('웨하스바닐라80g', '바닐라', '웨하스'),
    ('옥수수꿀호떡요요', '옥수수', '호떡')])
def test_confectionery_not_component(monkeypatch, text, partial, expected):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED', 'true')
    monkeypatch.setattr('app.ocr_classification.predict_product', lambda _: None)
    original = dict(standard_food_name=partial, matched_name=partial,
                    match_type='contains', source='ingredient_dictionary')
    assert apply_classification(text, original, get_ingredient_catalog())['standard_food_name'] == expected


@pytest.mark.parametrize('text', ['빼빼로맛우유','호떡믹스','웨하스향샴푸'])
def test_bakery_words_not_unconditional_alias(text):
    assert explicit_food_head(text) is None


@pytest.mark.parametrize('text', ['사과잼쿠키', '크라상모양쿠키'])
def test_cookie_head_preserved_instead_of_jam_or_croissant(text):
    assert explicit_food_head(text) == ('쿠키', '과자')
