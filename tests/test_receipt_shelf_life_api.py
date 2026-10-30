from datetime import date

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

import app.main as main


@pytest.mark.parametrize('route,expected', [('/ocr', 1), ('/api/ocr/receipt', 1),
                                          ('/ocr/raw', 0), ('/ocr/inventory-candidates', 0)])
def test_only_registration_preparation_uses_storage_suggestions(monkeypatch, route, expected):
    calls = []
    monkeypatch.setattr(main, 'run_ocr', lambda image: [dict(
        text='양파 3,980 2 7,960', confidence=.99,
        box=[[0, 0], [600, 0], [600, 30], [0, 30]])])
    async def fill(items, *, include_existing=False):
        assert include_existing
        calls.append(True)
        items[0]['consume_by_date'] = date(2026, 10, 7)
        items[0]['review_notes'].append('GPT 일반 보관기간 추정: 제품 표시 확인 필요')
        return items
    monkeypatch.setattr(main, 'fill_missing_dates', fill)
    image = cv2.imencode('.png', np.full((80, 600, 3), 255, dtype=np.uint8))[1].tobytes()
    response = TestClient(main.app).post(route, files={'file': ('receipt.png', image, 'image/png')})
    assert response.status_code == 200
    assert len(calls) == expected
    if expected:
        item = response.json()['items'][0]
        assert item['consumeByDate'] == '2026-10-07'
        assert item['reviewNotes'] == []


@pytest.mark.parametrize('route', ['/ocr', '/api/ocr/receipt'])
def test_frozen_product_database_item_reaches_gpt_before_public_response(monkeypatch, route):
    from app.receipt_shelf_life import StorageSuggestions
    import app.receipt_shelf_life as shelf
    monkeypatch.setenv('OPENAI_API_KEY', 'test')
    monkeypatch.setenv('OCR_SHELF_LIFE_LLM_ENABLED', 'true')
    monkeypatch.setattr(main, 'run_ocr', lambda image: [])
    async def passthrough(analysis):
        return analysis
    monkeypatch.setattr(main, 'correct_receipt', passthrough)
    monkeypatch.setattr(main, 'build_inventory_candidates', lambda analysis: [dict(
        classification='processed_food', recognized_product_name='냉동 콤비네이션 피자',
        ingredient_master_candidate=dict(canonical_name='피자', ingredient_type='ETC'),
        inventory_batch_candidate=dict(remaining_quantity=2., unit='개', storage_type='FROZEN',
                                       storage_source='RECEIPT_TEXT'))])
    async def caller(rows, key, model):
        assert len(rows) == 1 and rows[0]['ingredient'] == '피자'
        assert rows[0]['product'] == '냉동 콤비네이션 피자'
        assert rows[0]['storage'] == 'frozen'
        return StorageSuggestions(rows=[dict(row_id=0, storage='frozen', days=180)]), {}
    monkeypatch.setattr(shelf, 'request_suggestions', caller)
    image = cv2.imencode('.png', np.full((80, 600, 3), 255, dtype=np.uint8))[1].tobytes()
    response = TestClient(main.app).post(route, files={'file': ('receipt.png', image, 'image/png')})
    assert response.status_code == 200
    item = response.json()['items'][0]
    from datetime import timedelta
    assert item['consumeByDate'] == (date.today()+timedelta(days=180)).isoformat()
    assert item['locationCategory'] == 'frozen' and item['quantity'] == 2
    assert item['reviewNotes'] == [] and not any(k.startswith('_') for k in item)
