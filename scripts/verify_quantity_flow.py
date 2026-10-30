"""Opt-in local API integration checks; creates clearly named demo stock only."""
import json
import os
import uuid
from pathlib import Path
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
import requests
from sqlalchemy import text
from scripts.seed_development_data import _resolve_engine

BASE='http://127.0.0.1:8080'
def main():
    session=requests.Session()
    response=session.post(BASE+'/api/auth/login',json={'email':'devmeat@test.com','password':os.environ['DEMO_PASSWORD']},timeout=15)
    response.raise_for_status()
    session.headers['Authorization']='Bearer '+response.json()['token']
    def api(method,path,body=None):
        r=session.request(method,BASE+path,json=body,timeout=120)
        if not r.ok: raise RuntimeError(f'{method} {path}: {r.status_code} {r.text[:200]}')
        return r.json() if r.content else None
    tag=uuid.uuid4().hex[:8]
    fridge=api('POST','/api/fridges',{'name':'[수량 검증] '+tag})
    fridge_id=fridge.get('id',fridge.get('fridgeId'))
    assert fridge_id
    report={'fridgeId':fridge_id,'tag':tag,'checks':[]}
    output=Path('output/quantity_live_'+tag+'.json')
    output.write_text(json.dumps(report),encoding='utf-8')
    expiry=(date.today()+timedelta(days=14)).isoformat()
    def create(name,n,unit,**metadata):
        return api('POST','/api/inventories',dict(fridgeId=fridge_id,ingredientName=name,unitType='QUANTITY',remainingQuantity=n,unit=unit,consumeByDate=expiry,locationCategory='REFRIGERATED',**metadata))
    onion=create('양파',2,'개',perCountQuantity=200,perCountUnit='g')
    weight=create('양파',.5,'kg')
    assert weight['remainingQuantity']==500 and weight['unit']=='g'
    milk=create('우유',1,'L'); assert milk['remainingQuantity']==1000 and milk['unit']=='mL'
    pack=create('당근',2,'봉',packageCount=2,packageQuantity=500,packageUnit='g'); assert pack['remainingQuantity']==1000
    egg=create('달걀',.5,'개'); assert egg['remainingQuantity']==.5
    report['checks'].append('kg/L/half-count/package-total')
    before=api('GET',f'/api/inventories?fridgeId={fridge_id}')
    for n,u in [(2,'개'),(500,'g')]:
        api('POST',f'/api/fridges/{fridge_id}/inventory',{'items':[{'name':'양파','quantity':n,'unit':u,'consumeByDate':expiry,'locationCategory':'REFRIGERATED'}]})
    after=api('GET',f'/api/inventories?fridgeId={fridge_id}')
    assert len(after)==len(before)+2
    report['checks'].append('OCR-separate-batches')
    preview=api('POST','/api/recipes/ai/consumption-preview',{'fridgeId':fridge_id,'ingredients':[{'nameText':'양파','quantity':100,'unit':'g','optional':False}]})
    selected=[i for i in preview['items'] if i['quantityUsed']>0]
    assert selected[0]['stock']['batchId']==onion['batchId'] and selected[0]['quantityUsed']==.5
    report['checks'].append('confirmed-count-conversion')
    def meal(batch,amount,request_id=None):
        return {'requestId':request_id or 'test-'+uuid.uuid4().hex,'fridgeId':fridge_id,'title':'[수량 검증] '+tag,'mealType':'DINNER',
                'usedIngredients':[{'batchId':batch['batchId'],'unitType':'QUANTITY','quantityUsed':amount,'unit':batch['unit']}]}
    body=meal(weight,100)
    first=api('POST','/api/calendar/meals',body); again=api('POST','/api/calendar/meals',body)
    assert first['mealEventId']==again['mealEventId'] and api('GET',f"/api/inventories/{weight['batchId']}")['remainingQuantity']==400
    report['checks'].append('idempotency')
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda _:session.post(BASE+'/api/calendar/meals',json=meal(weight,300),timeout=30).status_code,range(2)))
    assert sorted(statuses)==[201,400],statuses
    assert api('GET',f"/api/inventories/{weight['batchId']}")['remainingQuantity']==100
    report['checks'].append('concurrent-overdraw-blocked')
    # Real AI recommendation, followed by the atomic completion endpoint.
    create('두부',1000,'g'); create('대파',500,'g'); create('간장',500,'mL'); create('참기름',500,'mL'); create('달걀',20,'개')
    recipe=api('POST','/api/recipes/ai/preview',{'fridgeId':fridge_id,'targetIngredients':['양파'],'additionalIngredients':[],'excludeIngredients':[]})
    key=recipe['recipeKey']
    completion={'recipeKey':key,'meal':meal(weight,101)}
    engine=_resolve_engine(None)
    with engine.connect() as c:
        count=c.execute(text('SELECT COUNT(*) FROM recipe_recommendation_feedback WHERE fridge_id=:id'),{'id':fridge_id}).scalar()
    r=session.post(BASE+'/api/recipes/ai/complete',json=completion,timeout=30)
    assert r.status_code==400,r.status_code
    with engine.connect() as c:
        assert count==c.execute(text('SELECT COUNT(*) FROM recipe_recommendation_feedback WHERE fridge_id=:id'),{'id':fridge_id}).scalar()
    report['checks'].append('failed-completion-rolls-back-feedback')
    consumption=api('POST','/api/recipes/ai/consumption-preview',{'fridgeId':fridge_id,'ingredients':recipe['ingredients']})
    used=[{'batchId':x['stock']['batchId'],'unitType':'QUANTITY','quantityUsed':x['quantityUsed'],'unit':x['stock']['unit']} for x in consumption['items'] if x['quantityUsed']>0]
    assert used,'No compatible quantities in actual recommendation'
    body={'recipeKey':key,'meal':{**meal(weight,1),'title':'[검증] '+recipe['recipeName'],'usedIngredients':used}}
    done=api('POST','/api/recipes/ai/complete',body)
    assert api('POST','/api/recipes/ai/complete',body)['mealEventId']==done['mealEventId']
    assert any(x['mealEventId']==done['mealEventId'] for x in api('GET','/api/calendar/meals'))
    report.update(recipe=recipe['recipeName'],mealEventId=done['mealEventId'],consumed=done['consumedItems'])
    report['checks'].append('actual-recommendation-completion-calendar')
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
