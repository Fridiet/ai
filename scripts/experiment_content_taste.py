"""Compare ID-free learned recipe content taste to current embeddings, without deploying it."""
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
import numpy as np
import torch
from app.ml.inference import TwoTowerRecommender
from app.ml.preferences import build_taste_profile
from app.ml.trainer import build_recipe_feature_matrix
from scripts.evaluate_personalization_holdout import rank_metrics


def run():
    model = TwoTowerRecommender.load()
    now = datetime(2026,9,11,12)
    groups = {}
    for i,r in enumerate(model.recipes):
        if str(r["source_recipe_id"]).startswith("fridiet-"):
            continue  # Keep prior demo recipes out of this experiment.
        for ingredient in r["ingredients"]:
            if ingredient.get("primary") and not ingredient.get("optional"):
                groups.setdefault(ingredient["canonical_name"],[]).append(i)
    groups = {n:sorted(set(ids),key=lambda i:hashlib.sha256(str(model.recipes[i]["source_recipe_id"]).encode()).hexdigest())
        for n,ids in groups.items() if len(set(ids)) >= 8}
    with torch.no_grad():
        features = torch.from_numpy(build_recipe_feature_matrix(model.recipes,model.metadata))
        tower = model.model.recipe_tower
        content = tower.content_network(features)
        zero_id = torch.zeros((len(model.recipes),model.model.config.recipe_id_embedding_size))
        content_vectors = torch.nn.functional.normalize(tower.fusion(torch.cat([content,zero_id],dim=1)),dim=-1).numpy()
    # Same broad inventory across every group and both strategies.
    stock = {i["canonical_name"]:[] for r in model.recipes[:20] for i in r["ingredients"]}
    base = model.predict(inventory_by_name=stock,health_condition=None,health_goal=None,today=now.date(),now=now)
    base_sim = np.array([base.scores_by_recipe_id[str(r["source_recipe_id"])].context_similarity for r in model.recipes])
    results = []
    for name, ids in sorted(groups.items()):
        history_ids = ids[:3]
        relevant = set(ids[3:])
        feedback = [{"recipeKey":f"{model.recipes[history_ids[d%3]].get('source') or 'LOCAL'}:{model.recipes[history_ids[d%3]]['source_recipe_id']}",
            "eventType":"COOKED","occurredAt":now-timedelta(days=d+1)} for d in range(12)]
        metrics = {}
        for strategy,vectors in [("current",model.recipe_vectors),("content_only",content_vectors)]:
            profile = build_taste_profile(model.recipes,vectors,feedback,now)
            # Equivalent ordering to query-vector blend; shared normalization is constant for all candidates.
            sim = (1-profile.weight)*base_sim + profile.weight*(model.recipe_vectors @ profile.vector)
            order = [int(i) for i in np.argsort(-sim,kind="stable") if i not in history_ids]
            metrics[strategy] = rank_metrics(order,relevant,10)
        split = "validation" if int(hashlib.sha256(name.encode()).hexdigest()[:8],16)%2 else "test"
        results.append({"group":name,"split":split,"relevant_count":len(relevant),"metrics":metrics})
    summary = {split:{strategy:{metric:float(np.mean([r["metrics"][strategy][metric] for r in results if r["split"]==split]))
        for metric in ("precision","recall","ndcg")} for strategy in ("current","content_only")} for split in ("validation","test")}
    return {"synthetic_relevance":"shares a labeled primary ingredient; not human preference",
        "deployed":False,"parameter_training":False,"group_count":len(results),"summary":summary,"groups":results,
        "limits":"Groups may share recipes. This is a diagnostic ablation, not a leakage-free real-world benchmark."}


if __name__ == "__main__":
    result = run()
    path = Path(__file__).resolve().parents[1]/"output/content_taste_experiment.json"
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k!='groups'},indent=2))
