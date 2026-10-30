import copy

from app import team_recipe


def test_mfds_attribution_hidden_in_response_but_original_metadata_preserved(monkeypatch):
    recipe = copy.deepcopy(next(r for r in team_recipe._load_recipes() if r.get('source') == 'MFDS_COOKRCP01'))
    original = copy.deepcopy(recipe)
    monkeypatch.setattr(team_recipe, '_load_recipes', lambda: [recipe])
    response = team_recipe.generate_team_recipe(
        health_condition=None, health_goal=None,
        active_inventories=[i['canonical_name'] for i in recipe['ingredients']],
        additional_ingredients=[], exclude_ingredients=[])
    assert response['description'] == ''
    assert recipe == original
    assert response['recipeKey'].startswith('MFDS_COOKRCP01:')
    assert response['ingredients'] and response['steps']
