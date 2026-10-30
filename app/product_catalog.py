import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from .config import settings
from .catalog_identity import verified_identity


@dataclass(frozen=True)
class ProductEntry:
    name: str
    manufacturer: str
    food_type: str
    standard_food_name: str
    source: str
    aliases: tuple[str, ...] = ()


def product_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower().lstrip("\ufeff")
    return re.sub(r"[^0-9a-z가-힣]", "", normalized)


def _score_entry(query: str, entry: ProductEntry) -> tuple[float, str, str]:
    query_key = product_key(query)
    manufacturer_key = product_key(entry.manufacturer)
    variants = {query_key}
    if manufacturer_key and manufacturer_key in query_key:
        variants.add(query_key.replace(manufacturer_key, "", 1))

    best = (0.0, "none", entry.name)
    for candidate_name, match_type in ((entry.name, "exact"), *[(alias, "alias") for alias in entry.aliases]):
        candidate_key = product_key(candidate_name)
        for variant in variants:
            if not candidate_key or not variant:
                continue
            if variant == candidate_key:
                score = 1.0
                current_type = match_type
            elif len(candidate_key) >= 3 and candidate_key in variant:
                score = min(0.98, 0.90 + 0.08 * len(candidate_key) / len(variant))
                current_type = "contains"
            elif len(variant) >= 4 and variant in candidate_key:
                score = min(0.94, 0.82 + 0.10 * len(variant) / len(candidate_key))
                current_type = "fuzzy"
            else:
                score = SequenceMatcher(None, variant, candidate_key).ratio()
                current_type = "fuzzy"
            if score > best[0]:
                best = (score, current_type, candidate_name)
    return best


class ProductCatalog:
    def __init__(self, seed_entries: list[ProductEntry], db_path: Path) -> None:
        self.seed_entries = seed_entries
        self.db_path = db_path

    def match(self, product_name: str, threshold: float = 0.72) -> dict:
        candidates = list(self.seed_entries)
        candidates.extend(self._search_local(product_name))

        best: tuple[float, str, str, ProductEntry | None] = (0.0, "none", "", None)
        for entry in candidates:
            score, match_type, matched_name = _score_entry(product_name, entry)
            if score > best[0]:
                best = (score, match_type, matched_name, entry)

        if best[3] is None or best[0] < threshold:
            remote_candidates = self._search_mfds(product_name)
            candidates.extend(remote_candidates)
            for entry in remote_candidates:
                score, match_type, matched_name = _score_entry(product_name, entry)
                if score > best[0]:
                    best = (score, match_type, matched_name, entry)

        score, match_type, matched_name, entry = best
        if entry is None or score < threshold:
            return self._no_match(score)
        identity = None
        if match_type == 'exact' and entry.source.startswith(('K-FIND:', 'mfds:')):
            # Homonymous catalog entries must agree, not depend on FTS order.
            exact = [c for c in candidates if _score_entry(product_name, c)[1] == 'exact']
            identities = {verified_identity(c.name, c.food_type) for c in exact}
            if len(identities) == 1 and None not in identities:
                identity = identities.pop()
        return {
            "standard_food_name": identity or entry.standard_food_name,
            "food_type": entry.food_type,
            "classification": "processed_food",
            "manufacturer": entry.manufacturer or None,
            "score": round(score, 4),
            "match_type": match_type,
            "source": entry.source,
            "matched_name": matched_name,
            "inventory_identity_verified": identity is not None,
        }

    def _search_local(self, product_name: str) -> list[ProductEntry]:
        if not self.db_path.is_file():
            return []
        tokens = [product_key(token) for token in re.findall(r"[0-9A-Za-z가-힣]+", product_name)]
        full_key = product_key(product_name)
        terms = {token for token in tokens if len(token) >= 3}
        if len(full_key) >= 3:
            terms.add(full_key)
            # Receipt OCR often joins a brand prefix and product name without a
            # space. Candidate suffixes are still validated by the final score.
            for length in (12, 10, 8, 6, 4):
                if len(full_key) > length:
                    terms.add(full_key[-length:])
        terms = sorted(terms, key=len, reverse=True)

        rows: dict[int, sqlite3.Row] = {}
        try:
            connection = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
            connection.row_factory = sqlite3.Row
            for term in terms[:4]:
                escaped = term.replace('"', '""')
                query = f'"{escaped}"'
                result = connection.execute(
                    """
                    SELECT p.id, p.name, p.manufacturer, p.food_type,
                           p.standard_food_name, p.source
                    FROM product_search
                    JOIN products AS p ON p.id = product_search.rowid
                    WHERE product_search MATCH ?
                    ORDER BY rank
                    LIMIT 40
                    """,
                    (query,),
                )
                for row in result:
                    rows[row["id"]] = row
            connection.close()
        except sqlite3.Error:
            return []

        return [
            ProductEntry(
                name=row["name"],
                manufacturer=row["manufacturer"] or "",
                food_type=row["food_type"],
                standard_food_name=row["standard_food_name"],
                source=row["source"],
            )
            for row in rows.values()
        ]

    def _search_mfds(self, product_name: str) -> list[ProductEntry]:
        if not settings.mfds_api_key:
            return []
        tokens = sorted(
            {token for token in re.findall(r"[A-Za-z가-힣]{3,}", product_name)},
            key=len,
            reverse=True,
        )
        query = tokens[0] if tokens else product_name
        return _fetch_mfds_products(query)

    @staticmethod
    def _no_match(score: float = 0.0) -> dict:
        return {
            "standard_food_name": None,
            "food_type": None,
            "classification": None,
            "manufacturer": None,
            "score": round(score, 4),
            "match_type": "none",
            "source": None,
            "matched_name": None,
        }


def _standard_name(food_type: str, product_name: str) -> str:
    if food_type in {"유탕면", "건면"}:
        return "라면"
    if "라면" in product_name or "짬뽕" in product_name or "볶음면" in product_name:
        return "라면"
    return food_type


@lru_cache(maxsize=512)
def _fetch_mfds_products(query: str) -> list[ProductEntry]:
    key = quote(settings.mfds_api_key or "", safe="")
    encoded_query = quote(query, safe="")
    url = (
        f"https://openapi.foodsafetykorea.go.kr/api/{key}/I1250/json/1/30/"
        f"PRDLST_NM={encoded_query}"
    )
    try:
        with urlopen(url, timeout=settings.mfds_timeout_seconds) as response:
            payload = json.load(response)
    except Exception:
        return []

    rows = payload.get("I1250", {}).get("row", [])
    return [
        ProductEntry(
            name=row.get("PRDLST_NM", ""),
            manufacturer=row.get("BSSH_NM", ""),
            food_type=row.get("PRDLST_DCNM", "가공식품"),
            standard_food_name=_standard_name(
                row.get("PRDLST_DCNM", "가공식품"), row.get("PRDLST_NM", "")
            ),
            source="mfds:I1250",
        )
        for row in rows
        if row.get("PRDLST_NM")
    ]


@lru_cache(maxsize=1)
def get_product_catalog() -> ProductCatalog:
    seed_path = Path(__file__).with_name("data") / "processed_products_ko.json"
    with seed_path.open(encoding="utf-8") as file:
        payload = json.load(file)
    entries = [
        ProductEntry(
            name=item["name"],
            aliases=tuple(item.get("aliases", [])),
            manufacturer=item.get("manufacturer", ""),
            food_type=item["food_type"],
            standard_food_name=item["standard_food_name"],
            source="seed_product_catalog",
        )
        for item in payload
    ]
    return ProductCatalog(entries, Path(settings.product_catalog_db_path))
