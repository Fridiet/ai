from dataclasses import replace
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
import app.main as main


def upload():
    return {'file': ('receipt.png', cv2.imencode('.png', np.full((80,300,3),255,dtype=np.uint8))[1].tobytes(), 'image/png')}


def boxes(text):
    return [{'text':text, 'confidence':.99, 'box':[[0,0],[700,0],[700,30],[0,30]]}]


@pytest.mark.parametrize('route', ['/ocr','/api/ocr/receipt'])
def test_optional_secondary_recovers_count_and_exposes_confirmation(monkeypatch, route):
    monkeypatch.setattr(main,'settings',replace(main.settings,ocr_secondary_counts_enabled=True))
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    calls=[]
    def recognize(image, **kwargs):
        calls.append(kwargs)
        return boxes('양파 2,000 2 4,000' if kwargs else '양파 2,000 4,000')
    monkeypatch.setattr(main,'run_ocr',recognize)
    response=TestClient(main.app).post(route,files=upload())
    assert response.status_code==200
    assert calls==[{}, {'detection_max_side':960}]
    item=response.json()['items'][0]
    assert item['name']=='양파' and item['quantity']==2
    assert item['reviewNotes'] == []


@pytest.mark.parametrize('mode', ['disabled','complete','failure','raw'])
def test_primary_result_survives_and_unnecessary_calls_are_skipped(monkeypatch, mode):
    monkeypatch.setattr(main,'settings',replace(main.settings,ocr_secondary_counts_enabled=mode!='disabled'))
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    calls=[]
    def recognize(image, **kwargs):
        calls.append(kwargs)
        if kwargs:
            raise RuntimeError('secondary predictor unavailable')
        return boxes('양파 2,000 1 2,000' if mode=='complete' else '양파 2,000 4,000')
    monkeypatch.setattr(main,'run_ocr',recognize)
    response=TestClient(main.app).post('/ocr/raw' if mode=='raw' else '/ocr',files=upload())
    assert response.status_code==200
    assert len(calls)==(2 if mode=='failure' else 1)
    if mode!='raw':
        assert response.json()['items'][0]['name']=='양파'
