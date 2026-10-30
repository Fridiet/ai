import copy
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
import app.main as main
from app import receipt_llm as llm
from app.ingredient_matcher import IngredientCatalog, IngredientEntry


@pytest.mark.parametrize('route,expected', [('/ocr', 1), ('/api/ocr/receipt', 1), ('/ocr/raw', 0), ('/ocr/inventory-candidates', 0)])
def test_only_receipt_routes_apply_llm(monkeypatch, route, expected):
    calls = []
    monkeypatch.setattr(main, 'run_ocr', lambda image: [{'text': '친환경경주미니양파 3,980 2 7,960',
        'confidence': .99, 'box': [[0, 0], [600, 0], [600, 30], [0, 30]]}])
    async def correct(analysis):
        calls.append(True)
        result = copy.deepcopy(analysis)
        p = next(p for p in result['products'] if '양파' in p['product_name'])
        p['match']['standard_food_name'] = None
        p['match']['match_type'] = 'none'
        catalog = IngredientCatalog([IngredientEntry('양파', ())])
        rows = llm.select_rows(result, catalog)
        decisions = llm.Decisions(rows=[llm.Decision(row_id=rows[0]['row_id'], verdict='food',
            candidate_id='0', evidence='양파', explanation='검증')])
        return llm.apply_decisions(result, rows, decisions)[0]
    monkeypatch.setattr(main, 'correct_receipt', correct)
    image = cv2.imencode('.png', np.full((80, 600, 3), 255, dtype=np.uint8))[1].tobytes()
    response = TestClient(main.app).post(route, files={'file': ('receipt.png', image, 'image/png')})
    assert response.status_code == 200
    assert len(calls) == expected
    if expected:
        assert set(response.json()) == {'items', 'reviewCandidates'}
        assert response.json()['items'][0]['name'] == '양파'
        assert response.json()['items'][0]['reviewNotes'] == []
        assert response.json()['items'][0]['consumeByDate']
