from dataclasses import replace
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
import app.main as main


@pytest.mark.parametrize('enabled',[False,True])
def test_name_hint_stays_in_review_not_inventory(monkeypatch,enabled):
    monkeypatch.setattr(main,'settings',replace(main.settings,
        ocr_secondary_counts_enabled=False,ocr_secondary_name_hints_enabled=enabled))
    monkeypatch.setenv('OCR_CLASSIFIER_ENABLED','true')
    monkeypatch.setattr('app.ocr_classification.predict_product',lambda _: {
        'needs_review':True,'is_food':True,'canonical_name':None,'model_score':.1})
    calls=[]
    def recognize(image,**kwargs):
        calls.append(kwargs)
        name='콰트로치즈피자' if kwargs else '관튼록친즌핀자'
        return [{'text':name,'confidence':.99 if kwargs else .76,'box':[[0,0],[220,0],[220,40],[0,40]]},
                {'text':'6,990 2 13,980','confidence':.99,'box':[[250,0],[700,0],[700,40],[250,40]]}]
    monkeypatch.setattr(main,'run_ocr',recognize)
    data=cv2.imencode('.png',np.full((60,720,3),255,dtype=np.uint8))[1].tobytes()
    response=TestClient(main.app).post('/api/ocr/receipt',files={'file':('test.png',data,'image/png')})
    assert response.status_code==200
    body=response.json()
    assert body['items']==[]
    assert len(body['reviewCandidates'])==1
    candidate=body['reviewCandidates'][0]
    assert candidate['rawText'].startswith('관튼록친즌핀자')
    assert candidate['purchaseCount']==2
    assert candidate['reason'] == '재료명을 확인해주세요.'
    assert len(calls)==(2 if enabled else 1)
