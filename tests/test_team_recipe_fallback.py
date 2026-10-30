from datetime import date, timedelta

import pytest

from app.team_recipe import TeamRecipeNotFoundError, generate_team_recipe


def recommend(**changes):
    args = dict(health_condition=None, health_goal=None,
                active_inventories=['달걀', '양파', '당근'],
                additional_ingredients=[], exclude_ingredients=[],
                target_ingredients=['부침가루'], allow_target_fallback=True)
    args.update(changes)
    return generate_team_recipe(**args)


def test_fallback_is_explicit_and_has_real_recipe():
    result = recommend(target_ingredients=['존재하지않는재료'])
    assert result['title'] and result['steps']
    assert any('대체 추천' in reason for reason in result['reasons'])
    assert '존재하지않는재료' not in result['availableIngredients']


def test_valid_target_still_required():
    result = recommend(active_inventories=['고등어', '무', '달걀'], target_ingredients=['고등어'])
    assert '고등어' in [i['nameText'] for i in result['ingredients']]
    assert not any('대체 추천' in reason for reason in result['reasons'])


def test_expired_target_not_resurrected_and_exclusion_preserved():
    today = date(2026, 10, 4)
    result = recommend(active_inventories=['달걀', '양파', '당근', '배추'],
                       target_ingredients=['배추'], exclude_ingredients=['식용유'],
                       inventories=[dict(ingredientName='배추', remainingQuantity=100,
                                         unit='g', consumeByDate=today-timedelta(days=1))], today=today)
    assert '배추' not in result['availableIngredients']
    assert '식용유' not in [i['nameText'] for i in result['ingredients']]
    assert any('소비기한' in reason for reason in result['reasons'])


def test_empty_inventory_still_fails_and_strict_mode_unchanged():
    with pytest.raises(TeamRecipeNotFoundError):
        recommend(active_inventories=[])
    with pytest.raises(TeamRecipeNotFoundError):
        recommend(target_ingredients=['존재하지않는재료'], allow_target_fallback=False)
