import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

from .config import settings


HANGUL_BASE = 0xAC00
HANGUL_END = 0xD7A3
HANGUL_INITIAL_COUNT = 19
HANGUL_MEDIAL_COUNT = 21
HANGUL_FINAL_COUNT = 28


@dataclass(frozen=True)
class IngredientEntry:
    name: str
    aliases: tuple[str, ...]


def lookup_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^0-9a-z가-힣]", "", normalized)


def _lookup_variants(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", text).lower()
    variants = {lookup_key(normalized)}
    variants.update(
        lookup_key(token)
        for token in re.findall(r"[0-9a-z가-힣]+", normalized)
        if len(lookup_key(token)) >= 2
    )
    return sorted((variant for variant in variants if variant), key=len, reverse=True)


def _hangul_components(character: str) -> tuple[int, int, int] | None:
    codepoint = ord(character)
    if not HANGUL_BASE <= codepoint <= HANGUL_END:
        return None
    offset = codepoint - HANGUL_BASE
    initial = offset // (HANGUL_MEDIAL_COUNT * HANGUL_FINAL_COUNT)
    medial = (offset % (HANGUL_MEDIAL_COUNT * HANGUL_FINAL_COUNT)) // HANGUL_FINAL_COUNT
    final = offset % HANGUL_FINAL_COUNT
    return initial, medial, final


def _hangul_ocr_similarity(left: str, right: str) -> float:
    """Compare similarly sized Hangul words while tolerating 받침 OCR errors."""
    if len(left) != len(right) or len(left) < 3:
        return 0.0

    scores: list[float] = []
    for left_character, right_character in zip(left, right, strict=True):
        left_components = _hangul_components(left_character)
        right_components = _hangul_components(right_character)
        if left_components is None or right_components is None:
            return 0.0
        scores.append(
            (0.4 if left_components[0] == right_components[0] else 0.0)
            + (0.4 if left_components[1] == right_components[1] else 0.0)
            + (0.2 if left_components[2] == right_components[2] else 0.0)
        )
    return sum(scores) / len(scores)


class IngredientCatalog:
    def __init__(self, entries: list[IngredientEntry]) -> None:
        self.entries = entries
        self._names: list[tuple[IngredientEntry, str, str, bool]] = []
        for entry in entries:
            self._names.append((entry, entry.name, lookup_key(entry.name), True))
            for alias in entry.aliases:
                self._names.append((entry, alias, lookup_key(alias), False))
        # Prefer the full specific ingredient over a generic whitespace token:
        # "새송이 버섯" must not resolve to the token "버섯" first.
        self._names.sort(key=lambda item: len(item[2]), reverse=True)

    def match(self, product_name: str, threshold: float = 0.72) -> dict:
        queries = _lookup_variants(product_name)
        if not queries:
            return self._no_match()

        for entry, alias, key, is_canonical in self._names:
            if key in queries:
                return {
                    "standard_name": entry.name,
                    "score": 1.0,
                    "match_type": "exact" if is_canonical else "alias",
                    "matched_alias": alias,
                }

        contained: list[tuple[float, int, IngredientEntry, str]] = []
        for entry, alias, key, _ in self._names:
            # Two-syllable ingredient names such as "오이" and "사과" create many
            # false positives inside unrelated compound words. They are accepted
            # only by exact/alias matching above; substring matching requires 3+
            # characters from the curated dictionary.
            for query in queries:
                if len(key) >= 3 and key in query:
                    coverage = len(key) / len(query)
                    score = min(0.98, 0.84 + 0.14 * coverage)
                    contained.append((score, len(key), entry, alias))
        if contained:
            score, _, entry, alias = max(contained, key=lambda candidate: (candidate[0], candidate[1]))
            if score >= threshold:
                return {
                    "standard_name": entry.name,
                    "score": round(score, 4),
                    "match_type": "contains",
                    "matched_alias": alias,
                }

        best: tuple[float, IngredientEntry | None, str | None] = (0.0, None, None)
        for entry, alias, key, _ in self._names:
            for query in queries:
                if len(key) <= 2 and len(query) > len(key) + 1:
                    continue
                score = max(
                    SequenceMatcher(None, query, key).ratio(),
                    _hangul_ocr_similarity(query, key),
                )
                if score > best[0]:
                    best = (score, entry, alias)

        score, entry, alias = best
        if entry is not None and score >= threshold:
            return {
                "standard_name": entry.name,
                "score": round(score, 4),
                "match_type": "fuzzy",
                "matched_alias": alias,
            }
        return self._no_match(score)

    @staticmethod
    def _no_match(score: float = 0.0) -> dict:
        return {
            "standard_name": None,
            "score": round(score, 4),
            "match_type": "none",
            "matched_alias": None,
        }


def _dictionary_path() -> Path:
    if settings.ingredient_dictionary_path:
        return Path(settings.ingredient_dictionary_path)
    return Path(__file__).with_name("data") / "ingredients_ko.json"


@lru_cache(maxsize=1)
def get_ingredient_catalog() -> IngredientCatalog:
    path = _dictionary_path()
    with path.open(encoding="utf-8") as file:
        payload = json.load(file)
    entries = [
        IngredientEntry(name=item["name"], aliases=tuple(item.get("aliases", [])))
        for item in payload
    ]
    return IngredientCatalog(entries)
