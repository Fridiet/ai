import json
from pathlib import Path
import pytest
from app.ingredient_matcher import get_ingredient_catalog
from app.product_pipeline import analyze_ocr_items
from app.quantity_balance import ingredient_balances
from app.team_recipe import _normalize_ingredient_name
from app.ml.dataset_identity import recipe_dataset_hash
from app.ingredient_taxonomy import family


@pytest.mark.parametrize('text,name', [('새송이 버섯','새송이버섯'),('에멘탈 치즈','에멘탈치즈'),
    ('팽이','팽이버섯'),('양송이','양송이버섯'),('표고','표고버섯'),('목이버섯','목이버섯'),
    ('청양고추','청양고추'),('순두부','순두부'),('계란노른자','달걀노른자'),
    ('닭가슴살','닭가슴살'),('건새우','건새우'),('현미밥','현미밥'),('백김치','백김치')])
def test_specific_names_survive_both_normalizers(text,name):
    assert get_ingredient_catalog().match(text)['standard_name'] == name
    assert _normalize_ingredient_name(text) == name


def test_ocr_mushrooms_keep_species(monkeypatch):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _: pytest.fail('must not collapse via old model'))
    texts = ['파머스픽 팽이버섯(봉) 1,280 1 1,280','국산 양송이버섯 200g 2,980 1 2,980']
    rows = [{'text':t,'confidence':.99,'box':[[0,i*40],[900,i*40],[900,i*40+20],[0,i*40+20]]} for i,t in enumerate(texts)]
    assert [p['match']['standard_food_name'] for p in analyze_ocr_items(rows)['products']] == ['팽이버섯','양송이버섯']


def test_shared_category_is_not_inventory_equivalence():
    assert family('팽이버섯') == family('표고버섯') == '버섯'
    result = ingredient_balances([{'canonical_name':'표고버섯','quantity':100,'unit':'g'}],
        {'팽이버섯':[{'remaining_quantity':300,'unit':'g'}], '버섯':[{'remaining_quantity':300,'unit':'g'}]})
    assert result[0]['knownAvailableQuantity'] == 0
    assert result[0]['shortageQuantity'] == 100


def test_recipe_source_recovers_multiple_mushrooms():
    recipes = json.loads((Path(__file__).parents[1]/'app/data/recipes_ko.json').read_text(encoding='utf-8'))
    recipe = next(r for r in recipes if r['recipe_name']=='표고크림파스타')
    names = {i['canonical_name'] for i in recipe['ingredients']}
    assert {'표고버섯','양송이버섯'} <= names and '버섯' not in names


def test_model_identity_changes_when_ingredients_change():
    a = [{'source_recipe_id':'same','ingredients':[{'canonical_name':'버섯'}]}]
    b = [{'source_recipe_id':'same','ingredients':[{'canonical_name':'표고버섯'}]}]
    assert recipe_dataset_hash(a) != recipe_dataset_hash(b)


@pytest.mark.parametrize('text,name',[('적양파','적양파'),('대추방울토마토','방울토마토'),
    ('양파가루','양파가루'),('감자칩','감자칩'),('땅콩버터','땅콩버터'),('메밀면','메밀면'),('훈제연어','훈제연어')])
def test_specific_product_not_overwritten_by_suffix_or_model(monkeypatch,text,name):
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _: pytest.fail('specific evidence was discarded'))
    row = {'text':text+' 2,000 1 2,000','confidence':.99,'box':[[0,0],[800,0],[800,20],[0,20]]}
    assert analyze_ocr_items([row])['products'][0]['match']['standard_food_name'] == name


def test_legacy_ocr_parent_prediction_requires_review(monkeypatch):
    from app.ocr_classification import apply_classification
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _: {
        'canonical_name':'버섯','is_food':True,'needs_review':False,'model_score':.99})
    original = {'standard_food_name':None,'source':None,'match_type':'none'}
    assert apply_classification('판독불명상품',original,get_ingredient_catalog())['standard_food_name'] is None


def test_recipe_search_target_does_not_fabricate_inventory():
    from app.team_recipe import generate_team_recipe
    result = generate_team_recipe(health_condition=None,health_goal=None,
        active_inventories=['팽이버섯','양파','달걀'],target_ingredients=['표고버섯'],
        additional_ingredients=[],exclude_ingredients=[])
    assert '표고버섯' not in result['availableIngredients']
    assert '표고버섯' in result['missingIngredients']
