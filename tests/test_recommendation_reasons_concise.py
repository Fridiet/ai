import copy

from app.team_recipe import _recommendation_reasons, SCORE_WEIGHTS


def args(**changes):
    result = dict(available=['라면', '대파'], missing=['쪽파', '돼지고기', '가다랑어포'],
                  expiring=[dict(canonical_name='대파', days_remaining=5)],
                  breakdown=dict(nutrition_fit_score=None, two_tower_score=96.56,
                                 menu_diversity=SCORE_WEIGHTS['menu_diversity']),
                  personalization_weight=.29)
    result.update(changes)
    return result


def test_personalized_reason_short_and_calculation_untouched():
    settings = args()
    before = copy.deepcopy(settings)
    assert _recommendation_reasons(**settings) == [
        '대파 소비기한: 5일 남음.', '보유 재료: 라면, 대파.',
        '추가 재료: 쪽파, 돼지고기, 가다랑어포.', '저장·조리 이력을 반영했어요.']
    assert settings == before


def test_health_and_personalization_combined_without_technical_score():
    settings = args()
    settings['breakdown']['nutrition_fit_score'] = 70
    reasons = _recommendation_reasons(**settings)
    assert len(reasons) == 4
    assert reasons[-1] == '건강 목표와 저장·조리 이력을 반영했어요.'
    assert not any(word in ' '.join(reasons) for word in ['Two-Tower', '벡터', '96.56', '29%', '유사도'])


def test_cold_start_does_not_claim_behavior_personalization():
    reasons = _recommendation_reasons(**args(personalization_weight=0))
    assert not any('이력' in reason for reason in reasons)


def test_long_ingredient_list_abbreviated_but_input_preserved():
    settings = args(missing=['쪽파', '돼지고기', '가다랑어포', '버섯', '마늘'])
    reasons = _recommendation_reasons(**settings)
    assert reasons[2] == '추가 재료: 쪽파, 돼지고기, 가다랑어포 외 2개.'
    assert len(settings['missing']) == 5


def test_fallback_warning_not_hidden_by_simplification():
    reasons = _recommendation_reasons(**args(target_fallback=True, target_names=['고등어'],
                                            inventory_names=['라면', '대파']))
    assert '대체 추천' in reasons[0]
    assert '소비기한' in reasons[1] and '고등어' in reasons[1]
