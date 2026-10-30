import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from app import main


def test_numeric_failure_keeps_secondary_recovery(monkeypatch):
    monkeypatch.setattr(main,'settings',SimpleNamespace(ocr_secondary_counts_enabled=True,
        ocr_secondary_name_hints_enabled=False,ocr_detection_max_side=0,ocr_numeric_cells_enabled=True))
    primary={'products':[{'purchase_quantity':None}]}
    recovered={'products':[{'purchase_quantity':2,
        'purchase_quantity_source':'SECONDARY_VERIFIED_RECEIPT_COLUMNS'}]}
    monkeypatch.setattr(main,'needs_secondary_count_recovery',lambda _:True)
    monkeypatch.setattr(main,'_decode_upload',AsyncMock(return_value=None))
    monkeypatch.setattr(main,'run_ocr',lambda *a,**k:[])
    monkeypatch.setattr(main,'analyze_ocr_items',lambda *a,**k:primary)
    monkeypatch.setattr(main,'supplement_product_counts',lambda *a:recovered)
    def fail(*args):
        raise RuntimeError('numeric predictor unavailable')
    monkeypatch.setattr(main,'recover_numeric_counts',fail)
    result=asyncio.run(main._supplement_receipt_counts(SimpleNamespace(seek=AsyncMock()),primary))
    assert result is recovered
    assert primary['products'][0]['purchase_quantity'] is None
