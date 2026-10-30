"""Read-only live API comparison and same-code top-five diagnostic."""
import json
from datetime import datetime, timedelta
from pathlib import Path
import requests
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine
from app.team_recipe import generate_team_recipe

def main():
    now=datetime.now()
    results=[]
    with _resolve_engine(None).connect() as db:
        for email in ['devmeat@test.com','devfish@test.com','devnone@test.com']:
            u=db.execute(text('SELECT user_id,main_fridge_id,health_condition,health_goal FROM user WHERE email=:e'),{'e':email}).mappings().one()
            inv=[dict(x) for x in db.execute(text("SELECT ingredient_name AS ingredientName,remaining_quantity AS remainingQuantity,unit,consume_by_date AS consumeByDate FROM inventory_batch WHERE fridge_id=:f AND status='ACTIVE' ORDER BY batch_id"),{'f':u['main_fridge_id']}).mappings()]
            history=[dict(x) for x in db.execute(text('SELECT recipe_key AS recipeKey,recipe_name AS recipeName,event_type AS eventType,created_at AS occurredAt FROM recipe_recommendation_feedback WHERE user_id=:u AND created_at BETWEEN :start AND :now'),{'u':u['user_id'],'start':now-timedelta(days=30),'now':now}).mappings()]
            runs={}
            for mode,events in [('baseline',[]),('positive_only',[x for x in history if x['eventType'] in {'COOKED','SAVED'}]),('current',history)]:
                payload=dict(inventories=inv,recentFeedback=events,healthCondition=u['health_condition'],healthGoal=u['health_goal'])
                response=requests.post('http://127.0.0.1:8000/api/recipes/ai',json=json.loads(json.dumps(payload,default=str)),timeout=120)
                response.raise_for_status()
                ranks=[]
                local=generate_team_recipe(inventories=inv,recent_feedback=events,health_condition=u['health_condition'],health_goal=u['health_goal'],active_inventories=None,additional_ingredients=None,exclude_ingredients=None,now=now,ranking_results=ranks)
                assert response.json()['title']==local['title']
                runs[mode]=dict(api_title=response.json()['title'],top5=ranks[:5])
            results.append(dict(email=email,fridge=u['main_fridge_id'],stock_count=len(inv),runs=runs))
    assert len({r['runs']['baseline']['api_title'] for r in results})==1
    output=Path(__file__).resolve().parents[1]/'output/shared_taste_demo/personalization_current.json'
    output.write_text(json.dumps(dict(synthetic=True,measured_at=now.isoformat(),results=results),ensure_ascii=False,indent=2),encoding='utf-8')
    for result in results:
        print(result['email'])
        for mode,run in result['runs'].items():
            print(mode,[(r['title'],r['score']) for r in run['top5']])

if __name__=='__main__': main()
