import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import numpy as np
from app.receipt_header_recovery import recover_header
from app import main


def test_no_seed_does_not_run_ocr(monkeypatch):
    def fail(*args):raise AssertionError('Unexpected OCR')
    monkeypatch.setattr('app.ocr.run_ocr',fail)
    raw=[]
    assert recover_header(np.zeros((10,10,3)),raw) is raw


def test_disabled_option_does_not_read_upload(monkeypatch):
    monkeypatch.setattr(main,'settings',SimpleNamespace(ocr_header_recovery_enabled=False))
    upload=SimpleNamespace(seek=AsyncMock())
    raw=[]
    assert asyncio.run(main._supplement_receipt_header(upload,raw)) is raw
    upload.seek.assert_not_called()


def test_failed_header_inference_preserves_primary(monkeypatch):
    monkeypatch.setattr(main,'settings',SimpleNamespace(ocr_header_recovery_enabled=True))
    monkeypatch.setattr(main,'header_seed',lambda _:['seed'])
    monkeypatch.setattr(main,'_decode_upload',AsyncMock(return_value=None))
    def fail(*args):raise RuntimeError('predictor failure')
    monkeypatch.setattr(main,'recover_header',fail)
    raw=[{'text':'original'}]
    assert asyncio.run(main._supplement_receipt_header(SimpleNamespace(seek=AsyncMock()),raw)) is raw
