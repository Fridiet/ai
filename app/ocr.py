from functools import lru_cache
import os
from threading import Lock
from typing import Any
import logging
import time

import numpy as np

from .config import settings

_inference_lock = Lock()


@lru_cache(maxsize=1)
def get_numeric_recognizer() -> Any:
    from paddleocr import TextRecognition
    return TextRecognition(model_name='korean_PP-OCRv5_mobile_rec', device=settings.device,
                           cpu_threads=int(os.getenv('OCR_CPU_THREADS', '2')))


def recognize_numeric_crops(crops: list) -> list[dict]:
    if not crops:
        return []
    if len(crops) > 24:
        raise ValueError('Too many numeric crops')
    started = time.perf_counter()
    with _inference_lock:
        acquired = time.perf_counter()
        recognizer = get_numeric_recognizer()
        loaded = time.perf_counter()
        predictions = list(recognizer.predict(crops))
    logging.getLogger('uvicorn.error.ocr_pipeline').info(
        'OCR numeric_cells queue_seconds=%.3f load_seconds=%.3f inference_seconds=%.3f crops=%d',
        acquired-started, loaded-acquired, time.perf_counter()-loaded, len(crops))
    results = []
    for prediction in predictions:
        payload = prediction.json
        data = payload.get('res', payload)
        results.append(dict(text=str(data['rec_text']), confidence=float(data['rec_score'])))
    return results


@lru_cache(maxsize=1)
def get_engine() -> Any:
    # Import lazily so health checks work before the large OCR model is loaded.
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang=settings.ocr_lang,
        ocr_version=settings.ocr_version,
        device=settings.device,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        cpu_threads=int(os.getenv('OCR_CPU_THREADS', '2')),
    )


def warmup_engines():
    """Prepare cached predictors before accepting requests, without remote API calls."""
    started = time.perf_counter()
    with _inference_lock:
        engine = get_engine()
        # Exercise inference as well as loading to initialize runtime caches.
        list(engine.predict(np.full((64, 256, 3), 255, dtype=np.uint8)))
        if settings.ocr_numeric_cells_enabled:
            recognizer = get_numeric_recognizer()
            list(recognizer.predict([np.full((32, 128, 3), 255, dtype=np.uint8)]))
    logging.getLogger('uvicorn.error.ocr_pipeline').info(
        'OCR warmup seconds=%.3f numeric=%s', time.perf_counter()-started,
        settings.ocr_numeric_cells_enabled)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, np.ndarray):
        return value.tolist()
    return list(value)


def run_ocr(image: np.ndarray, *, detection_max_side: int | None = None) -> list[dict]:
    # A single cached predictor is shared by requests; serialize access to it.
    max_side = settings.ocr_detection_max_side if detection_max_side is None else detection_max_side
    if max_side != 0 and not 256 <= max_side <= 4000:
        raise ValueError('OCR_DETECTION_MAX_SIDE must be 0 or between 256 and 4000')
    options = ({'text_det_limit_side_len': max_side, 'text_det_limit_type': 'max'}
               if max_side else {})
    started = time.perf_counter()
    with _inference_lock:
        acquired = time.perf_counter()
        engine = get_engine()
        loaded = time.perf_counter()
        results = list(engine.predict(image, **options))
        predicted = time.perf_counter()
    logging.getLogger('uvicorn.error.ocr_pipeline').info(
        'OCR stages queue_seconds=%.3f engine_load_seconds=%.3f inference_seconds=%.3f image=%dx%d detection_max_side=%d',
        acquired-started, loaded-acquired, predicted-loaded, image.shape[1], image.shape[0], max_side)
    items: list[dict] = []

    for result in results:
        payload = result.json
        data = payload.get("res", payload)
        texts = _as_list(data.get("rec_texts"))
        scores = _as_list(data.get("rec_scores"))
        polygons = _as_list(data.get("rec_polys"))

        for text, score, polygon in zip(texts, scores, polygons):
            items.append(
                {
                    "text": str(text),
                    "confidence": float(score),
                    "box": [[int(x), int(y)] for x, y in polygon],
                }
            )
    return items
