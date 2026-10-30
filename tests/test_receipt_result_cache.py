import asyncio
import copy
from datetime import date,timedelta

import pytest
from app import receipt_llm as llm, receipt_shelf_life as shelf, receipt_result_cache as cache
from app.ingredient_matcher import IngredientCatalog, IngredientEntry

CATALOG=IngredientCatalog([IngredientEntry(n,()) for n in ['양파','대파','라면','오징어','팽이버섯','양송이버섯']])


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    cache.clear()
    monkeypatch.setenv('OCR_LLM_CACHE_ENABLED','true')
    yield
    cache.clear()


def analysis(text='친환경 미니 양파',row=0):
    return {'products':[{'row_index':row,'product_name':text,'normalized_name':text,
        'raw_text':text+' 2,000 2 4,000','prices':[2000,4000],
        'purchase_quantity':2,'purchase_quantity_source':'VERIFIED_RECEIPT_COLUMNS',
        'match':{'standard_food_name':None,'match_type':'none','score':.7,
                 'food_type':'원재료','classification':'ingredient','manufacturer':None,'source':'test'}}]}


def foods(row=0):
    return [dict(name='양파',category='vegetable',location_category='refrigerated',
                 _storage_source='CANONICAL_RULE',_product_name='국산 양파',
                 quantity=2,unit='개',consume_by_date=None,review_notes=[])]


def identity_caller(calls, name='양파',evidence='양파'):
    async def call(rows,key,model):
        calls.append(copy.deepcopy(rows))
        return llm.Decisions(rows=[llm.Decision(row_id=r['row_id'],verdict='food',
            candidate_id=next((c['id'] for c in r['candidates'] if c['name']==name),None),
            standard_name=name,evidence=evidence,explanation='test') for r in rows]),{'total_tokens':10}
    return call


def storage_caller(calls,storage='refrigerated',days=7):
    async def call(rows,key,model):
        calls.append(copy.deepcopy(rows))
        return shelf.StorageSuggestions(rows=[shelf.StorageSuggestion(row_id=r['row_id'],
            storage=storage,days=days) for r in rows]),{'total_tokens':10}
    return call


def correct(a,caller,catalog=CATALOG):
    return asyncio.run(llm.correct_receipt(a,enabled=True,key='test',caller=caller,catalog=catalog))


def dates(items,caller,today=date(2026,10,27)):
    return asyncio.run(shelf.fill_missing_dates(items,enabled=True,key='test',caller=caller,reference_date=today))


def test_identity_hit_equals_uncached_and_preserves_quantity_evidence(monkeypatch):
    a=analysis();before=copy.deepcopy(a);calls=[];caller=identity_caller(calls)
    first=correct(a,caller)
    assert correct(a,caller)==first and len(calls)==1 and a==before
    monkeypatch.setenv('OCR_LLM_CACHE_ENABLED','false')
    assert correct(a,caller)==first and len(calls)==2
    for f in ['raw_text','product_name','prices','purchase_quantity','purchase_quantity_source']:
        assert first['products'][0][f]==before['products'][0][f]


def test_row_ids_rebound_and_different_quantity_not_overwritten():
    calls=[];caller=identity_caller(calls)
    correct(analysis(),caller)
    changed=analysis(row=7)
    changed['products'][0].update(purchase_quantity=.5,prices=[1234])
    result=correct(changed,caller)
    assert len(calls)==1 and result['products'][0]['row_index']==7
    assert result['products'][0]['purchase_quantity']==.5 and result['products'][0]['prices']==[1234]


@pytest.mark.parametrize('change',['product','model','prompt','catalog'])
def test_identity_context_changes_do_not_hit(monkeypatch,change):
    calls=[];caller=identity_caller(calls);a=analysis();catalog=CATALOG
    correct(a,caller)
    if change=='product':a=analysis('국산 양파')
    if change=='model':monkeypatch.setenv('OPENAI_OCR_MODEL','another-model')
    if change=='prompt':monkeypatch.setattr(llm,'PROMPT',llm.PROMPT+' changed')
    if change=='catalog':catalog=IngredientCatalog(CATALOG.entries+[IngredientEntry('새재료',())])
    correct(a,caller,catalog)
    assert len(calls)==2


@pytest.mark.parametrize('text,name,evidence',[
    ('농심 오징어짬뽕','오징어','오징어'),('친환경 팽이버섯','양송이버섯','버섯'),
    ('그릭 무가','양파','양파')])
def test_rejected_identity_never_cached(text,name,evidence):
    calls=[];caller=identity_caller(calls,name,evidence);a=analysis(text)
    assert correct(a,caller)==correct(a,caller)
    assert len(calls)==2 and not cache._entries


def test_current_server_identity_validation_runs_for_hits(monkeypatch):
    calls=[];caller=identity_caller(calls);a=analysis();correct(a,caller)
    monkeypatch.setattr(llm,'supported',lambda *args:None)
    assert correct(a,caller)['products'][0]['match']['standard_food_name'] is None
    assert len(calls)==1


def test_storage_hit_uses_duration_and_rebases_today():
    calls=[];caller=storage_caller(calls);items=foods();before=copy.deepcopy(items)
    first=dates(items,caller)
    assert dates(items,caller)==first and len(calls)==1
    tomorrow=dates(items,caller,date(2026,10,28))
    assert tomorrow[0]['consume_by_date']==first[0]['consume_by_date']+timedelta(days=1)
    assert len(calls)==1 and items==before
    assert tomorrow[0]['quantity']==2 and tomorrow[0]['unit']=='개'
    assert all('consume_by_date' not in value for _,value in cache._entries.values())


def test_storage_hit_equals_cache_disabled_response(monkeypatch):
    calls=[];caller=storage_caller(calls);items=foods()
    original=dates(items,caller)
    assert dates(items,caller)==original and len(calls)==1
    monkeypatch.setenv('OCR_LLM_CACHE_ENABLED','false')
    assert dates(items,caller)==original and len(calls)==2


def test_current_storage_validator_runs_for_hits(monkeypatch):
    calls=[];caller=storage_caller(calls);items=foods()
    dates(items,caller)
    monkeypatch.setattr(shelf,'apply_suggestions',lambda items,*args,**kw:(copy.deepcopy(items),0))
    assert dates(items,caller)==items and len(calls)==1


@pytest.mark.parametrize('source',['PRODUCT_LABEL','USER_CONFIRMED','SAVED'])
def test_cached_storage_does_not_overwrite_explicit_dates(source):
    calls=[];caller=storage_caller(calls);items=foods();dates(items,caller)
    items[0].update(_date_source=source,consume_by_date=date(2026,11,30))
    assert dates(items,caller)==items and len(calls)==1


def test_expired_identity_requests_gpt_again(monkeypatch):
    clock=[100]
    monkeypatch.setattr(cache.time,'monotonic',lambda:clock[0])
    calls=[];caller=identity_caller(calls)
    correct(analysis(),caller)
    clock[0]+=cache.TTL_SECONDS+1
    correct(analysis(),caller)
    assert len(calls)==2


@pytest.mark.parametrize('field,value',[
    ('name','대파'),('category','fruit'),('_product_name','개봉한 양파'),
    ('_product_name','해동한 양파'),('location_category','frozen'),('_storage_source','USER_CONFIRMED')])
def test_storage_context_changes_do_not_hit(field,value):
    calls=[];caller=storage_caller(calls);items=foods();dates(items,caller)
    items[0][field]=value;dates(items,caller)
    assert len(calls)==2


@pytest.mark.parametrize('days',[0,731,None])
def test_rejected_storage_never_cached(days):
    calls=[];caller=storage_caller(calls,days=days)
    dates(foods(),caller);dates(foods(),caller)
    assert len(calls)==2 and not cache._entries


def test_partial_hits_request_only_missing_rows():
    calls=[];caller=storage_caller(calls)
    dates(foods(),caller)
    items=foods()+[dict(foods()[0],name='대파')]
    result=dates(items,caller)
    assert len(calls)==2 and [r['row_id'] for r in calls[1]]==[1]
    assert all(i['consume_by_date']==date(2026,11,3) for i in result)


def test_invalid_duplicate_response_no_cache_and_original_preserved():
    a=analysis();before=copy.deepcopy(a)
    async def bad(rows,key,model):
        d=(await identity_caller([])(rows,key,model))[0].rows[0]
        return llm.Decisions(rows=[d,d]),{}
    assert correct(a,bad)==a==before and not cache._entries


def test_failure_not_cached():
    calls=[]
    async def fail(rows,key,model):
        calls.append(1)
        raise llm.CorrectionFailure('http_429')
    assert correct(analysis(),fail)==analysis()
    assert correct(analysis(),fail)==analysis()
    assert len(calls)==2 and not cache._entries


def test_expiry_eviction_and_copy_isolation(monkeypatch):
    monkeypatch.setattr(cache,'MAX_ENTRIES',2)
    monkeypatch.setattr(cache.time,'monotonic',lambda:100)
    cache._put('a',{'days':7});cache._put('b',{'days':8})
    value=cache._get('a');value['days']=999
    assert cache._get('a')['days']==7
    cache._put('c',{'days':9})
    assert cache._get('b') is None and len(cache._entries)==2
    monkeypatch.setattr(cache.time,'monotonic',lambda:3701)
    assert cache._get('a') is None
