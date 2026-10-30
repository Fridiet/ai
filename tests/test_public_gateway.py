import pytest
import asyncio
import httpx
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
from starlette.testclient import TestClient
from app.public_gateway import PublicGateway

TOKEN='private-test-token-only-1234567890123456789'
async def business(request):
    return JSONResponse({'bytes':len(await request.body())})

def client(**limits):
    return TestClient(PublicGateway(Starlette(routes=[Route('/api/recipes/ai',business,methods=['POST'])]),TOKEN,**limits))

def test_fail_closed_without_key():
    with pytest.raises(ValueError):
        PublicGateway(None,'')

def test_authentication_before_business():
    c=client()
    assert c.get('/health').status_code==200
    assert c.post('/api/recipes/ai',json={}).status_code==401
    assert c.post('/api/recipes/ai',headers={'X-AI-Service-Token':'wrong'}).status_code==401
    r=c.post('/api/recipes/ai',headers={'X-AI-Service-Token':TOKEN},json={})
    assert r.status_code==200

@pytest.mark.parametrize('path',['/docs','/openapi.json','/recommendations','/ocr/raw','/ocr/inventory-candidates/save','/ocr/scans/1/confirm'])
def test_internal_routes_not_public(path):
    assert client().post(path,headers={'X-AI-Service-Token':TOKEN}).status_code==404

def test_limits_and_size():
    c=client(per_minute=1)
    headers={'X-AI-Service-Token':TOKEN}
    assert c.post('/api/recipes/ai',headers=headers,json={}).status_code==200
    assert c.post('/api/recipes/ai',headers=headers,json={}).status_code==429
    assert client().post('/api/recipes/ai',headers=headers,content=b'a'*(256*1024+1)).status_code==413

def test_chunked_body_not_bypass_limit():
    headers={'X-AI-Service-Token':TOKEN}
    assert client().post('/api/recipes/ai',headers=headers,content=iter([b'a'*(256*1024),b'b'])).status_code==413


def test_zero_disables_request_quotas_but_preserves_auth_and_size():
    c=client(per_minute=0,per_day=0)
    headers={'X-AI-Service-Token':TOKEN}
    for _ in range(205):
        assert c.post('/api/recipes/ai',headers=headers,json={}).status_code==200
    assert not c.app.calls
    assert c.post('/api/recipes/ai',json={}).status_code==401
    assert c.post('/api/recipes/ai',headers=headers,content=b'a'*(256*1024+1)).status_code==413


@pytest.mark.parametrize('limits',[{'per_minute':-1},{'per_day':-1},{'concurrency':0}])
def test_invalid_limits_rejected(limits):
    with pytest.raises(ValueError):
        client(**limits)


def test_daily_limit_can_remain_when_minute_limit_disabled():
    c=client(per_minute=0,per_day=1)
    headers={'X-AI-Service-Token':TOKEN}
    assert c.post('/api/recipes/ai',headers=headers,json={}).status_code==200
    assert c.post('/api/recipes/ai',headers=headers,json={}).status_code==429


def test_simultaneous_requests_wait_and_overflow_is_controlled():
    async def scenario():
        entered,release=asyncio.Event(),asyncio.Event()
        async def slow(request):
            entered.set()
            await release.wait()
            return JSONResponse({'ok':True})
        gateway=PublicGateway(Starlette(routes=[Route('/api/recipes/ai',slow,methods=['POST'])]),
            TOKEN,per_minute=0,per_day=0,concurrency=1,max_pending=1,queue_timeout=2)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),base_url='http://test') as c:
            async def post():return await c.post('/api/recipes/ai',headers={'X-AI-Service-Token':TOKEN},json={})
            first=asyncio.create_task(post())
            await entered.wait()
            second=asyncio.create_task(post())
            while gateway.pending<2:await asyncio.sleep(0)
            assert (await post()).status_code==429
            assert (await c.get('/health')).status_code==200
            release.set()
            assert [r.status_code for r in await asyncio.gather(first,second)]==[200,200]
            assert gateway.pending==0 and not gateway.slots.locked()
    asyncio.run(scenario())


def test_queue_timeout_and_cancellation_do_not_leak_capacity():
    async def scenario():
        gateway=PublicGateway(None,TOKEN,concurrency=1,max_pending=2,queue_timeout=.01)
        await gateway.slots.acquire()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),base_url='http://test') as c:
            async def post():return await c.post('/api/recipes/ai',headers={'X-AI-Service-Token':TOKEN},json={})
            assert (await post()).status_code==429
            assert gateway.pending==0
            gateway.queue_timeout=10
            task=asyncio.create_task(post())
            while gateway.pending==0:await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert gateway.pending==0
        gateway.slots.release()
    asyncio.run(scenario())


def test_upload_timeout_returns_408_and_releases_slot(monkeypatch):
    original=asyncio.wait_for
    async def expire_upload(awaitable,timeout):
        if timeout==15:
            awaitable.close()
            raise asyncio.TimeoutError()
        return await original(awaitable,timeout)
    monkeypatch.setattr(asyncio,'wait_for',expire_upload)
    c=client()
    assert c.post('/api/recipes/ai',headers={'X-AI-Service-Token':TOKEN},json={}).status_code==408
    assert c.app.pending==0 and not c.app.slots.locked()


def test_db_writer_requires_both_keys_and_exact_path():
    dbkey='separate-writer-test-token-12345678901234567890'
    inner=Starlette(routes=[Route('/ocr/inventory-candidates/save',business,methods=['POST']),
                            Route('/ocr/scans/12/confirm',business,methods=['POST'])])
    c=TestClient(PublicGateway(inner,TOKEN,db_token=dbkey))
    for path in ['/ocr/inventory-candidates/save','/ocr/scans/12/confirm']:
        assert c.post(path,headers={'X-AI-Service-Token':TOKEN}).status_code==401
        assert c.post(path,headers={'X-AI-DB-Token':dbkey}).status_code==401
        assert c.post(path,headers={'X-AI-Service-Token':TOKEN,'X-AI-DB-Token':dbkey},json={}).status_code==200
    assert c.post('/ocr/scans/12/confirm/extra',headers={'X-AI-Service-Token':TOKEN,'X-AI-DB-Token':dbkey}).status_code==404
    with pytest.raises(ValueError):PublicGateway(inner,TOKEN,db_token=TOKEN)
