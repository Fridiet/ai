"""Deterministic product rules with an optional learned classification fallback."""
import logging
import os
import re
from collections import OrderedDict
from pathlib import Path
from threading import Lock
from .receipt_name_evidence import fresh_suffix, clear_nonfood, compound_product, explicit_food_head, snack_brand_context, FRESH_NAMES
from .ingredient_taxonomy import parents
from .ingredient_matcher import lookup_key
from .receipt_name_evidence import egg_product_head
from .receipt_name_evidence import unresolved_flavor_context
from .receipt_mapper import VEGETABLE_NAMES, FRUIT_NAMES, SEAFOOD_NAMES, GRAIN_NAMES

logger = logging.getLogger("uvicorn.error.ocr_classifier")
ROOT = Path(__file__).resolve().parents[1]
_lock = Lock()
_model = None
_failed = False
_cache = OrderedDict()


def enabled():
    return os.getenv("OCR_CLASSIFIER_ENABLED", "false").lower() in {"1", "true", "yes"}


def _load():
    global _model, _failed
    if _model is None and not _failed:
        try:
            import torch
            from .ml.fast_ocr_normalizer import FastOCRNormalizer
            torch.set_num_threads(int(os.getenv("OCR_CLASSIFIER_THREADS", "2")))
            path = Path(os.getenv("OCR_CLASSIFIER_MANIFEST", "output/ocr_normalizer_experiment_v25/model.json"))
            _model = FastOCRNormalizer(path if path.is_absolute() else ROOT / path)
            logger.info("OCR fast base classifier loaded; online Qwen calls disabled")
        except Exception:
            _failed = True
            logger.exception("OCR classifier unavailable; using existing dictionary pipeline until restart")
    return _model


def warmup():
    if enabled():
        with _lock:
            _load()


def predict_product(text):
    if not enabled() or not text.strip() or len(text) > 256:
        return None
    # One shared model, serialized inference and bounded cache across requests.
    with _lock:
        model = _load()
        if model is None:
            return None
        if text in _cache:
            _cache.move_to_end(text)
            return dict(_cache[text])
        try:
            result = model.predict(text)
        except Exception:
            logger.exception("OCR classification failed; using dictionary fallback for this row")
            return None
        _cache[text] = dict(result)
        if len(_cache) > 512:
            _cache.popitem(last=False)
        return result


def apply_classification(text, original, ingredient_catalog):
    # Disabling learned inference must not disable product identity safeguards.
    # predict_product/warmup alone own the optional model switch.
    if clear_nonfood(text):
        return dict(original, standard_food_name=None, classification="nonfood", source="receipt_nonfood_rule")
    if (original.get('inventory_identity_verified') and original.get('match_type') == 'exact'
            and str(original.get('source', '')).startswith(('K-FIND:', 'mfds:'))):
        return original
    head = explicit_food_head(text)
    # A whole product head can be more specific than a partial raw-ingredient
    # match. Keep verified exact identities above, and dictionary exact/aliases.
    if head and original.get('match_type') not in {'exact', 'alias'}:
        name, food_type = head
        return dict(original, standard_food_name=name, food_type=food_type,
                    classification='ingredient' if food_type in {'채소', '알류', '육류', '과실류'} else 'processed_food',
                    manufacturer=None, score=0.9, match_type='contains',
                    source='receipt_food_head_rule', matched_name=name)
    leftover = lookup_key(text).replace(lookup_key(original.get('matched_name') or ''), '')
    conflicting_context = compound_product(leftover) or unresolved_flavor_context(text, original.get('standard_food_name')) or (
        snack_brand_context(text) and original.get('standard_food_name') in
        (FRESH_NAMES | VEGETABLE_NAMES | FRUIT_NAMES | SEAFOOD_NAMES | GRAIN_NAMES | {'올리브'}))
    # Litres on a partial fruit match suggest a liquid product, not proof of
    # that raw fruit. Let the classifier resolve it or abstain; no juice guess.
    if (original.get('source') == 'ingredient_dictionary'
            and original.get('standard_food_name') in FRUIT_NAMES
            and re.search(r'\d+(?:\.\d+)?\s*(?:ml|l)(?![a-z])', text, re.I)):
        conflicting_context = True
    # Curated whole-product exceptions (e.g. 오징어짬뽕) precede raw ingredients.
    if (original.get("source") == "seed_product_catalog" and original.get("standard_food_name")
            and (original.get("match_type") in {"exact", "alias"}
                 or (original.get("match_type") == "contains" and not conflicting_context))):
        return original
    egg = egg_product_head(text)
    if egg:
        return dict(original, standard_food_name=egg, food_type='알류', classification='ingredient',
                    manufacturer=None, score=0.9, match_type='contains',
                    source='receipt_egg_head_rule', matched_name=egg)
    if (original.get("source") == "ingredient_dictionary" and original.get("standard_food_name") in parents()
            and original.get("match_type") in {"exact", "alias", "contains"}
            and not conflicting_context):
        return original
    suffix = fresh_suffix(text)
    if suffix and suffix in {entry.name for entry in ingredient_catalog.entries}:
        return dict(original, standard_food_name=suffix, food_type="원재료", classification="ingredient",
                    manufacturer=None, score=0.9, match_type="contains", source="receipt_fresh_suffix_rule", matched_name=suffix)
    if (original.get("standard_food_name") and original.get("source") == "ingredient_dictionary"
            and original.get("match_type") in {"exact", "alias", "contains"}
            and not conflicting_context):
        return original
    head = explicit_food_head(text)
    if head:
        name, food_type = head
        classification = 'ingredient' if food_type in {'채소', '알류', '육류'} else 'processed_food'
        return dict(original, standard_food_name=name, food_type=food_type, classification=classification,
                    manufacturer=None, score=0.9, match_type="contains", source="receipt_food_head_rule", matched_name=name)
    prediction = predict_product(text)
    if prediction is None:
        if conflicting_context and (original.get('match_type') == 'contains'
                or original.get('source') == 'ingredient_dictionary'):
            return dict(original, standard_food_name=None, classification='unknown', source='conflicting_product_context')
        return original
    if prediction["needs_review"]:
        # Only curated exact/alias matches may bypass a model abstention.
        name, classification = None, "unknown"
    elif prediction["is_food"] is False:
        name, classification = None, "nonfood"
    else:
        name = prediction["canonical_name"]
        classification = "ingredient" if name in {entry.name for entry in ingredient_catalog.entries} else "processed_food"
        # The old OCR classifier was trained with collapsed categories. Never
        # turn an uncertain specific product into a generic accepted inventory.
        if name in set(parents().values()):
            name, classification = None, "unknown"
    return {
        "standard_food_name": name,
        "food_type": "원재료" if classification == "ingredient" else "모델 분류 식품",
        "classification": classification,
        "manufacturer": None,
        "score": prediction["model_score"],
        "match_type": "ml_review" if prediction["needs_review"] else "ml_classifier",
        "source": "ocr_classifier_fast_v7",
        "matched_name": text,
    }
