"""Use the verified base classifier without loading/calling the Qwen reranker."""
import hashlib
import json
from pathlib import Path
from .choice_ocr_normalizer import ROOT, INFERENCE_FILES, validate_manifest
from .semantic_ocr_normalizer import SemanticOCRNormalizer


class FastOCRNormalizer:
    def __init__(self, manifest):
        manifest = Path(manifest)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        validate_manifest(data)
        checkpoint = (manifest.parent / data["base_checkpoint"]).resolve()
        if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != data["base_checkpoint_sha256"]:
            raise ValueError("Checkpoint hash mismatch")
        if set(data["inference_code_sha256"]) != set(INFERENCE_FILES):
            raise ValueError("Incomplete code seal")
        for filename, expected in data["inference_code_sha256"].items():
            if hashlib.sha256((ROOT / filename).read_bytes()).hexdigest() != expected:
                raise ValueError("Evaluated inference code changed")
        self.base = SemanticOCRNormalizer(checkpoint, ROOT / "output/semantic_ocr_cache")

    def predict(self, text):
        return self.base.predict(text)
