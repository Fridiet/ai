import asyncio
from dataclasses import replace
import app.ocr as ocr
import app.main as main


def test_warmup_initializes_primary_and_enabled_numeric(monkeypatch):
    calls=[]
    class Engine:
        def predict(self,image):
            calls.append(('primary',image.shape))
            return []
    class Numeric:
        def predict(self,images):
            calls.append(('numeric',images[0].shape))
            return []
    monkeypatch.setattr(ocr,'get_engine',lambda:Engine())
    monkeypatch.setattr(ocr,'get_numeric_recognizer',lambda:Numeric())
    monkeypatch.setattr(ocr,'settings',replace(ocr.settings,ocr_numeric_cells_enabled=True))
    ocr.warmup_engines()
    assert [x[0] for x in calls]==['primary','numeric']


def test_startup_warmup_is_opt_in_and_finishes_before_ready(monkeypatch):
    calls=[]
    monkeypatch.setattr('app.ocr_classification.warmup',lambda:None)
    monkeypatch.setattr(ocr,'warmup_engines',lambda:calls.append('warm'))
    async def startup():
        async with main.lifespan(main.app):
            calls.append('ready')
    monkeypatch.setenv('OCR_STARTUP_WARMUP','false')
    asyncio.run(startup())
    assert calls==['ready']
    calls.clear()
    monkeypatch.setenv('OCR_STARTUP_WARMUP','true')
    asyncio.run(startup())
    assert calls==['warm','ready']
