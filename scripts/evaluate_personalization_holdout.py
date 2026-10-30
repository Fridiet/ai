"""Synthetic unseen-recipe holdout; no database writes or real-user accuracy claim."""
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from app.ml.inference import get_two_tower_recommender
from scripts.seed_personalization_demo import PERSONAS


def rank_metrics(ranking, relevant, k):
    top = list(dict.fromkeys(ranking))[:k]
    hits = sum(r in relevant for r in top)
    ideal = sum(1/math.log2(i+2) for i in range(min(k,len(relevant))))
    dcg = sum(1/math.log2(i+2) for i,r in enumerate(top) if r in relevant)
    return {"precision":hits/k, "recall":hits/len(relevant) if relevant else None,
        "ndcg":dcg/ideal if ideal else None}


def evaluate():
    model = get_two_tower_recommender()
    if model is None:
        raise RuntimeError("학습 모델이 없어 평가할 수 없습니다.")
    now = datetime(2026,9,11,12)
    by_name = {r["recipe_name"]:r for r in model.recipes}
    all_names = {name for _,_,names in PERSONAS for name in names}
    stock = {i["canonical_name"]:[{"consume_by_date":now.date()+timedelta(days=7)}]
        for name in all_names for i in by_name[name]["ingredients"]}
    args = dict(inventory_by_name=stock, health_condition=None, health_goal=None, today=now.date(), now=now)
    base = model.predict(**args)
    order = lambda p: sorted(p.scores_by_recipe_id, key=lambda k:(-p.scores_by_recipe_id[k].similarity,k))
    baseline_order = order(base)
    results = []
    for _, persona, favorites in PERSONAS:
        unique = list(dict.fromkeys(favorites))
        heldout = by_name[unique[-1]]
        training = unique[:-1]
        events = []
        for day in range(1,13):
            r = by_name[training[(day-1)%len(training)]]
            events.append({"recipeKey":f"{r.get('source') or 'LOCAL'}:{r['source_recipe_id']}",
                "recipeName":r["recipe_name"],"eventType":"COOKED","occurredAt":now-timedelta(days=day)})
        assert all(e["recipeName"] != heldout["recipe_name"] for e in events)
        personalized = model.predict(**args, recent_feedback=events)
        personalized_order = order(personalized)
        relevant = {str(heldout["source_recipe_id"])}
        results.append({"persona":persona,"training_recipes":training,"heldout":heldout["recipe_name"],
            "baseline_rank":baseline_order.index(next(iter(relevant)))+1,
            "personalized_rank":personalized_order.index(next(iter(relevant)))+1,
            "weight":personalized.personalization_weight,
            "at_k":{str(k):{"baseline":rank_metrics(baseline_order,relevant,k),
                "personalized":rank_metrics(personalized_order,relevant,k)} for k in (1,5,10,20)}})
    macro = {str(k):{mode:{metric:sum(r["at_k"][str(k)][mode][metric] for r in results)/len(results)
        for metric in ("precision","recall","ndcg")} for mode in ("baseline","personalized")} for k in (1,5,10,20)}
    cold = model.predict(**args, recent_feedback=[])
    assert cold.personalization_weight == 0 and order(cold) == baseline_order
    return {"synthetic_only":True,"model_version":base.model_version,"candidate_count":len(baseline_order),
        "evaluation_scope":"retrieval ranking, not final inventory/health-filtered recommendation",
        "limitation":"Only three synthetic personas with one held-out relevant recipe each. Held-out recipes may be in original model training; only taste-history leakage is excluded.",
        "same_stock_and_health":True,"cold_start_equals_baseline":True,"macro":macro,"personas":results}


if __name__ == "__main__":
    report = evaluate()
    path = Path(__file__).resolve().parents[1] / "output/personalization_holdout.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=True,indent=2))
