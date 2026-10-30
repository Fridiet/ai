from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.schemas import AIRecipeInventory
from app.team_recipe import _inventory_by_name
from app.quantity_balance import ingredient_balances


def inventory(mode, amount=None, **changes):
    return dict(ingredientName='두부', unitType=mode, remainingQuantity=amount,
                unit='g', **changes)


def grouped(*batches):
    return _inventory_by_name(list(batches), ['두부'], [], date(2026, 10, 13))


def balance(stock):
    return ingredient_balances([dict(canonical_name='두부',quantity=400,unit='g')],stock)[0]


@pytest.mark.parametrize('mode,amount', [('PERCENT',75), ('PERCENT',None),
                                       ('CUSTOM',None), ('CUSTOM',0), ('CUSTOM',800)])
def test_nonquantity_is_present_but_never_grams(mode,amount):
    stock=grouped(inventory(mode,amount,customQuantityText='약 800g',
                            perCountQuantity=1000,perCountUnit='g'))
    assert '두부' in stock
    assert stock['두부'][0]['customQuantityText'] == '약 800g'
    result=balance(stock)
    assert result['status'] == 'UNKNOWN'
    assert result['shortageQuantity'] is None
    assert result['knownAvailableQuantity'] == 0


@pytest.mark.parametrize('mode,amount,status', [('PERCENT',0,'ACTIVE'),
    ('CUSTOM',None,'DEPLETED'), ('CUSTOM',0,'DISCARDED'), ('QUANTITY',500,'DEPLETED')])
def test_depleted_not_revived_from_legacy_names(mode,amount,status):
    assert grouped(inventory(mode,amount,status=status)) == {}


@pytest.mark.parametrize('mode', ['PERCENT','CUSTOM'])
def test_expired_nonquantity_not_used(mode):
    assert grouped(inventory(mode,50,consumeByDate='2026-10-12')) == {}


def test_mixed_batches_do_not_turn_percent_into_grams():
    partial=grouped(inventory('QUANTITY',300),inventory('PERCENT',100))
    result=balance(partial)
    assert result['status']=='UNKNOWN'
    assert result['knownAvailableQuantity']==300
    assert result['shortageQuantity'] is None
    enough=grouped(inventory('QUANTITY',500),inventory('CUSTOM',0))
    assert balance(enough)['status']=='ENOUGH'
    assert balance(enough)['knownAvailableQuantity']==500


def test_direct_balance_ignores_depleted_percent_and_inactive_custom():
    result=balance({'두부':[dict(unitType='PERCENT',remaining_quantity=0,unit='g'),
                            dict(unitType='CUSTOM',status='DEPLETED',remaining_quantity=None)]})
    assert result['status']=='SHORTAGE'
    assert result['shortageQuantity']==400


@pytest.mark.parametrize('amount', [-1,101,float('inf'),float('nan')])
def test_percent_validation(amount):
    with pytest.raises(ValidationError):
        AIRecipeInventory(**inventory('PERCENT',amount))


@pytest.mark.parametrize('amount', [0,25,50,75,100,None])
def test_percent_values_valid(amount):
    assert AIRecipeInventory(**inventory('PERCENT',amount)).remainingQuantity == amount


def test_unknown_unit_mode_rejected():
    with pytest.raises(ValidationError):
        AIRecipeInventory(**inventory('OTHER',50))


@pytest.fixture
def simple_recipe(monkeypatch):
    monkeypatch.setattr('app.team_recipe.get_two_tower_recommender',lambda:None)
    monkeypatch.setattr('app.team_recipe._load_recipes',lambda:[
        dict(source_recipe_id='unit-mode-test',recipe_name='두부 요리',
             ingredients=[dict(canonical_name='두부',quantity=400,unit='g',optional=False)],
             steps=['두부를 익힌다.'])])


@pytest.mark.parametrize('mode,amount,text', [('PERCENT',50,None),('CUSTOM',0,'반 통')])
def test_actual_api_uses_nonquantity_without_claiming_enough(simple_recipe,mode,amount,text):
    response=TestClient(app).post('/api/recipes/ai',json={
        'inventories':[inventory(mode,amount,customQuantityText=text)],'activeInventories':['두부']})
    assert response.status_code==200
    payload=response.json()
    assert payload['availableIngredients']==['두부']
    assert payload['missingIngredients']==[]
    assert payload['ingredientBalances'][0]['status']=='UNKNOWN'
    assert payload['ingredientBalances'][0]['shortageQuantity'] is None
    assert '잔량 확인 필요: 두부.' in payload['reasons']


def test_latest_percent_and_custom_snapshot_used_each_request(simple_recipe):
    client=TestClient(app)
    for before,after in [(inventory('PERCENT',75),inventory('PERCENT',0)),
                         (inventory('CUSTOM',0,customQuantityText='반 통'),
                          inventory('CUSTOM',0,customQuantityText='',status='DEPLETED'))]:
        assert client.post('/api/recipes/ai',json={'inventories':[before]}).status_code==200
        result=client.post('/api/recipes/ai',json={'inventories':[after],'activeInventories':['두부']})
        assert result.status_code==404


def test_api_rejects_invalid_percent_before_recommendation():
    assert TestClient(app).post('/api/recipes/ai',json={'inventories':[inventory('PERCENT',101)]}).status_code==422
