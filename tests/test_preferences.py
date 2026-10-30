from datetime import datetime, timedelta

import numpy as np

from app.ml.preferences import build_taste_profile
from app.ml.inference import TwoTowerRecommender


NOW = datetime(2026, 9, 10, 12)
RECIPES = [{"recipe_name": "두부요리"}, {"recipe_name": "생선요리"}]
VECTORS = np.array([[1, 0], [0, 1]], dtype=np.float32)


def event(name="두부요리", kind="COOKED", day=1):
    return dict(recipeName=name, eventType=kind, occurredAt=NOW - timedelta(days=day))


def test_empty_unresolved_and_nonpositive_feedback_has_no_effect():
    p = build_taste_profile(RECIPES, VECTORS, [event(kind="SHOWN"), event(kind="DISMISSED"), event(name="없는요리"), event(day=-1), event(day=31)], NOW)
    assert p.vector is None and p.weight == 0 and p.event_count == 0


def test_stable_key_resolves_duplicate_names_and_survives_rename():
    recipes = [dict(recipe_name="같은이름", source="MFDS", source_recipe_id=str(i)) for i in range(2)]
    history = [dict(recipeKey="MFDS:1", recipeName="변경전이름", eventType="COOKED", occurredAt=NOW-timedelta(days=1))]
    profile = build_taste_profile(recipes,VECTORS,history,NOW)
    np.testing.assert_allclose(profile.vector,VECTORS[1])
    history[0]["recipeKey"] = "MFDS:missing"
    history[0]["recipeName"] = "같은이름"
    assert build_taste_profile(recipes,VECTORS,history,NOW).event_count == 0


def test_cooked_stronger_than_saved_and_duplicates_do_not_amplify():
    feedback = [event(kind="SAVED"), event("생선요리")]
    p = build_taste_profile(RECIPES, VECTORS, feedback, NOW)
    assert p.vector[1] > p.vector[0]
    duplicate = build_taste_profile(RECIPES, VECTORS, feedback * 100, NOW)
    np.testing.assert_allclose(p.vector, duplicate.vector)
    assert p.weight == duplicate.weight and p.event_count == 2


def test_timezone_ambiguity_and_weight_cap():
    utc_event = event()
    utc_event["occurredAt"] = "2026-09-09T03:00:00Z"
    p = build_taste_profile(RECIPES, VECTORS, [utc_event], NOW)
    local = build_taste_profile(RECIPES, VECTORS, [event()], NOW)
    assert p.weight == local.weight
    ambiguous = build_taste_profile(RECIPES + [RECIPES[0]], np.vstack([VECTORS,VECTORS[0]]), [event()], NOW)
    assert ambiguous.event_count == 0
    p = build_taste_profile(RECIPES, VECTORS, [event(day=d) for d in range(1,25)], NOW)
    assert 0 < p.weight <= 0.65


def test_real_model_changes_ranking_with_same_inventory():
    model = TwoTowerRecommender.load()
    inventory = {i["canonical_name"]: [] for r in model.recipes[:20] for i in r["ingredients"]}
    args = dict(inventory_by_name=inventory, health_condition=None, health_goal="GENERAL", today=NOW.date(), now=NOW)
    baseline = model.predict(**args)
    ignored = model.predict(**args, recent_feedback=[event("두부구이", "SHOWN")])
    assert baseline.scores_by_recipe_id == ignored.scores_by_recipe_id
    tofu = model.predict(**args, recent_feedback=[event("두부구이",day=d) for d in range(1,21)])
    fish = model.predict(**args, recent_feedback=[event("고등어 무조림",day=d) for d in range(1,21)])
    ids = {r["recipe_name"]: r["source_recipe_id"] for r in model.recipes}
    assert tofu.scores_by_recipe_id[ids["두부구이"]].percentile_score > fish.scores_by_recipe_id[ids["두부구이"]].percentile_score
    assert fish.scores_by_recipe_id[ids["고등어 무조림"]].percentile_score > tofu.scores_by_recipe_id[ids["고등어 무조림"]].percentile_score


def test_persona_history_changes_feasible_result_without_overriding_exclusions():
    from app.team_recipe import _load_recipes, generate_team_recipe
    from scripts.seed_personalization_demo import PERSONAS

    recipes = {r["recipe_name"]: r for r in _load_recipes()}
    inventory = sorted({i["canonical_name"] for _, _, names in PERSONAS for name in names for i in recipes[name]["ingredients"]})
    args = dict(health_condition=None, health_goal="GENERAL", active_inventories=inventory,
                additional_ingredients=[], exclude_ingredients=[], now=NOW, today=NOW.date())
    results = []
    histories = []
    for _, _, names in PERSONAS:
        history = [event(name, "SAVED", 28 - i) for i,name in enumerate(dict.fromkeys(names))]
        history += [event(names[(d-1) % len(names)], day=d) for d in range(1,21)]
        histories.append(history)
        result = generate_team_recipe(**args, recent_feedback=history)
        results.append(result)
        assert result["missingIngredients"] == []
        assert 0 < result["scoreBreakdown"]["personalization_weight"] <= 0.65
    assert "두부" in results[0]["title"]
    assert any(name in results[1]["title"] for name in ("닭고기", "소고기", "돼지고기"))
    assert any(name in results[2]["title"] for name in ("참치", "새우", "오징어", "고등어"))
    excluded = generate_team_recipe(**{**args, "exclude_ingredients": ["닭고기"]}, recent_feedback=histories[1])
    assert "닭고기" not in {i["nameText"] for i in excluded["ingredients"]}


def test_skip_expires_and_only_three_recent_skips_are_excluded():
    from app.team_recipe import _is_recently_dismissed
    history = [dict(recipeName=str(i), eventType='DISMISSED', occurredAt=NOW-timedelta(minutes=i)) for i in range(5)]
    assert _is_recently_dismissed('0', history, NOW)
    assert not _is_recently_dismissed('4', history, NOW)
    assert not _is_recently_dismissed('0', history, NOW+timedelta(hours=1))
    assert not _is_recently_dismissed('0', history, NOW-timedelta(hours=1))


def test_sparse_history_is_gentle_and_content_preference_is_general():
    from app.ml.preferences import ingredient_taste_scores
    recipes = [dict(recipe_name='A',source_recipe_id='a',ingredients=[dict(canonical_name='X')]),
               dict(recipe_name='B',source_recipe_id='b',ingredients=[dict(canonical_name='Y')])]
    history = [event('B', day=d) for d in range(1,15)]
    scores, weight = ingredient_taste_scores(recipes,history,NOW)
    assert scores['LOCAL:b'] > scores['LOCAL:a']
    assert ingredient_taste_scores(recipes,[],NOW)==({},0.0)
    sparse = build_taste_profile(RECIPES,VECTORS,[event()],NOW)
    assert 0 < sparse.weight < .05
