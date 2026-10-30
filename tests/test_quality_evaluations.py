from scripts.evaluate_personalization_holdout import rank_metrics
from scripts.evaluate_market_receipt import evaluate
import math


def test_ranking_denominators_and_duplicates():
    assert rank_metrics(["a","a","b"], {"a","c"}, 2) == {"precision":.5,"recall":.5,"ndcg":1/(1+1/math.log2(3))}


def test_receipt_misses_are_in_quantity_denominator():
    rows = [{"text":"당근", "names":["당근"],"count":2,"quantity":2,"unit":"개"}]
    report = evaluate(rows, [])
    assert report["metrics"]["selection_precision"] is None
    assert report["metrics"]["end_to_end_amount_recall"] == 0


def test_receipt_duplicate_predictions_are_false_positives():
    raw = [{"text":"당근 1,000 2 2,000","confidence":.99,"box":[[0,y],[500,y],[500,y+20],[0,y+20]]} for y in (0,100)]
    report = evaluate([{"text":"당근", "names":["당근"],"count":2}], raw)
    assert report["counts"]["false_positive"] == 1
    assert report["metrics"]["selection_precision"] == .5
