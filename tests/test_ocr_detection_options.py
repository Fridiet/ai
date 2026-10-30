from types import SimpleNamespace
import numpy as np
import pytest
from app import ocr


@pytest.mark.parametrize('side,expected', [(0, {}), (960, {
    'text_det_limit_side_len': 960, 'text_det_limit_type': 'max'})])
def test_detector_option_does_not_resize_recognizer_input(monkeypatch, side, expected):
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    calls = []
    class Engine:
        def predict(self, given, **kwargs):
            assert given is image
            calls.append(kwargs)
            return []
    monkeypatch.setattr(ocr, 'settings', SimpleNamespace(ocr_detection_max_side=side))
    monkeypatch.setattr(ocr, 'get_engine', lambda: Engine())
    assert ocr.run_ocr(image) == []
    assert calls == [expected]


@pytest.mark.parametrize('side', [-1, 10, 4001])
def test_invalid_detection_limit_fails_before_model_load(monkeypatch, side):
    monkeypatch.setattr(ocr, 'settings', SimpleNamespace(ocr_detection_max_side=side))
    monkeypatch.setattr(ocr, 'get_engine', lambda: pytest.fail('invalid config'))
    with pytest.raises(ValueError):
        ocr.run_ocr(np.zeros((10, 10, 3), dtype=np.uint8))
