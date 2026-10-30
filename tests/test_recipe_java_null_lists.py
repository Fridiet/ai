import pytest
from pydantic import ValidationError
from app.schemas import AIRecipeRequest


FIELDS = ("activeInventories", "inventories", "targetIngredients", "additionalIngredients",
          "excludeIngredients", "recentFeedback")


@pytest.mark.parametrize("field", FIELDS)
def test_java_null_optional_list_becomes_empty(field):
    original = {field: None}
    parsed = AIRecipeRequest.model_validate(original)
    assert getattr(parsed, field) == []
    assert original[field] is None


@pytest.mark.parametrize("field", FIELDS)
def test_non_list_values_are_still_rejected(field):
    with pytest.raises(ValidationError):
        AIRecipeRequest.model_validate({field: "not-a-list"})
