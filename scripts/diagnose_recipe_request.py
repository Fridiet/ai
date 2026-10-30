"""Read-only reproduction of Spring's AI request; never records feedback."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from scripts.seed_development_data import _resolve_engine


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--user-id', type=int, default=10)
    parser.add_argument('--fridge-id', type=int, default=11)
    args = parser.parse_args()
    load_dotenv(Path(__file__).resolve().parents[1] / '.env')
    engine = _resolve_engine(None)
    with engine.connect() as conn:
        user = conn.execute(text('SELECT health_condition,health_goal FROM user WHERE user_id=:id'), {'id': args.user_id}).mappings().one()
        batches = [dict(r) for r in conn.execute(text("SELECT ingredient_name,consume_by_date,remaining_quantity,unit,count_basis,per_count_quantity,per_count_unit,quantity_needs_review,unit_type,status FROM inventory_batch WHERE fridge_id=:id AND status IN ('ACTIVE','EXPIRED') ORDER BY consume_by_date,batch_id"), {'id': args.fridge_id}).mappings()]
        feedback = [dict(r) for r in conn.execute(text('SELECT recipe_key,recipe_name,event_type,created_at FROM recipe_recommendation_feedback WHERE user_id=:id AND created_at>:cutoff ORDER BY created_at DESC'), {'id': args.user_id, 'cutoff': datetime.now()-timedelta(days=30)}).mappings()]
    def camel(s):
        first, *rest = s.split('_')
        return first + ''.join(t.title() for t in rest)
    payload = {'healthCondition': user['health_condition'], 'healthGoal': user['health_goal'],
        'activeInventories': sorted({r['ingredient_name'] for r in batches}),
        'inventories': [{camel(k): (v.isoformat() if hasattr(v, 'isoformat') else float(v) if k in {'remaining_quantity','per_count_quantity'} and v is not None else bool(v) if k == 'quantity_needs_review' else v) for k,v in r.items() if k != 'status'} for r in batches],
        'recentFeedback': [{'recipeKey': r['recipe_key'], 'recipeName': r['recipe_name'], 'eventType': r['event_type'], 'occurredAt': r['created_at'].isoformat()} for i,r in enumerate(feedback) if i<50 or r['event_type'] in {'SAVED','COOKED'}],
        'additionalIngredients': [], 'excludeIngredients': []}
    print(json.dumps({'active_batches': sum(r['status']=='ACTIVE' for r in batches),
                      'expired_batches': sum(r['status']=='EXPIRED' for r in batches),
                      'feedback_count': len(payload['recentFeedback'])}, ensure_ascii=False))
    targets = [None, *payload['activeInventories']]
    with httpx.Client(timeout=60) as client:
        for target in targets:
            response = client.post('http://localhost:8000/api/recipes/ai', json=dict(payload, targetIngredients=[target] if target else []))
            data = response.json()
            fallback = any('대체 추천' in reason for reason in data.get('reasons', []))
            if target is None or response.status_code != 200 or fallback:
                print(json.dumps({'target': target, 'status': response.status_code,
                    'result': data.get('title') or data.get('detail'),
                    'fallback': fallback,
                    'reasons': data.get('reasons') if fallback else [],
                    'target_batches': [{'status': r['status'], 'date': str(r['consume_by_date'])} for r in batches if r['ingredient_name'] == target]}, ensure_ascii=False))
    engine.dispose()


if __name__ == '__main__':
    main()
