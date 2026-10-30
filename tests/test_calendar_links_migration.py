import pytest
from scripts.migrate_calendar_links import unique_links


def test_strict_one_to_one_links():
    assert unique_links([1, 2], [(1, 10), (2, 20)], 'test') == {1: 10, 2: 20}


@pytest.mark.parametrize('candidates', [[(1, None)], [(1, 10), (1, 20)], [(1, 10), (2, 10)]])
def test_missing_ambiguous_or_shared_target_is_rejected(candidates):
    with pytest.raises(ValueError):
        unique_links([1, 2], candidates, 'test')


def test_empty_plan_is_safe_for_retries():
    assert unique_links([], [], 'test') == {}
