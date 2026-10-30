import json
from pathlib import Path
from app.quantity_balance import ingredient_balances

def test_shared_cases():
    for case in json.loads((Path(__file__).parents[1]/'app/data/quantity_cases.json').read_text(encoding='utf-8')):
        result=ingredient_balances([{'canonical_name':'양파','quantity':case['quantity'],'unit':case['unit']}], {})[0]
        assert result['requiredQuantity']==case['expectedQuantity']
        assert result['unit']==case['expectedUnit']

def test_only_confirmed_per_count_weight():
    recipe=[{'canonical_name':'양파','quantity':100,'unit':'g'}]
    batch={'remaining_quantity':2,'unit':'개'}
    assert ingredient_balances(recipe,{'양파':[batch]})[0]['status']=='UNKNOWN'
    batch.update(perCountQuantity=200,perCountUnit='g')
    assert ingredient_balances(recipe,{'양파':[batch]})[0]['status']=='ENOUGH'
    batch['quantityNeedsReview']=True
    assert ingredient_balances(recipe,{'양파':[batch]})[0]['status']=='UNKNOWN'

def test_same_stock_cannot_satisfy_two_requirements():
    recipe=[{'canonical_name':'양파','quantity':2,'unit':'개'},{'canonical_name':'양파','quantity':100,'unit':'g'}]
    batch={'remaining_quantity':2,'unit':'개','perCountQuantity':200,'perCountUnit':'g'}
    result=ingredient_balances(recipe,{'양파':[batch]})
    assert result[1]['status']=='SHORTAGE'
