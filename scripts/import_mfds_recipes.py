from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.ingredient_matcher import IngredientCatalog, get_ingredient_catalog


SERVICE_ID = "COOKRCP01"
SOURCE_NAME = "MFDS_COOKRCP01"
API_ROOT = "https://openapi.foodsafetykorea.go.kr/api"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "app" / "data" / "recipes_ko.json"
MAX_PAGE_SIZE = 1000

# The source API does not identify optional ingredients. Pantry seasonings and
# common garnishes are optional in Fridiet so that a missing condiment does not
# remove an otherwise useful refrigerator-based recommendation.
OPTIONAL_INGREDIENTS = {
    "대파",
    "마늘",
    "생강",
    "고추",
    "버터",
    "소금",
    "설탕",
    "간장",
    "고추장",
    "된장",
    "식초",
    "식용유",
    "올리브유",
    "참기름",
    "들기름",
    "고춧가루",
    "후추",
    "케첩",
    "마요네즈",
    "꿀",
    "올리고당",
    "물엿",
    "조청",
    "겨자",
    "와사비",
    "맛술",
    "청주",
    "정종",
    "통깨",
    "참깨",
    "들깨",
    "다시마",
    "레몬즙",
    "생강청",
    "월계수잎",
    "바질",
    "파슬리",
    "로즈마리",
    "카레가루",
    "계피",
    "오레가노",
    "굴소스",
    "이스트",
    "와인",
    "베이킹파우더",
}

IGNORED_SOURCE_INGREDIENTS = {"물", "얼음", "육수"}
IGNORED_SOURCE_TERMS = {
    "꼬치",
    "나무막대기",
    "이쑤시개",
}
IGNORED_SOURCE_TERM_KEYS = {
    re.sub(r"[^0-9a-z가-힣]", "", unicodedata.normalize("NFKC", value).lower())
    for value in IGNORED_SOURCE_TERMS
}
MIN_INGREDIENT_PARSE_COVERAGE = 0.70

QUANTITY_PATTERN = re.compile(
    r"(?P<quantity>\d+\s*[/⁄]\s*\d+|[¼½¾⅓⅔⅛⅜⅝⅞]|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>kg|g|mg|ml|l|개|장|알|쪽|봉지|큰술|작은술|컵|마리|줄기|모|"
    r"포기|줌|대|개비|조각|토막|팩|캔|병|뿌리|단|가닥|cc|줄|잎|봉|톨|"
    r"티스푼|테이블스푼|tbsp|tsp|Ts|ts|T|t)",
    re.IGNORECASE,
)
STEP_NUMBER_PATTERN = re.compile(r"^\s*\d+\s*[.)]\s*")
SECTION_LABEL_PATTERN = re.compile(
    r"^\s*(?:[-•*●○■□◆◇]\s*)?(?:(?:찜|주|부|필수|선택|곁들임)?\s*재료|"
    r"소스|양념장?|드레싱|육수|국물|고명)\s*:?\s*"
)
LEADING_BRACKET_LABEL_PATTERN = re.compile(r"^\s*\[[^\]]+\]\s*")
LEADING_PARENTHESIZED_LABEL_PATTERN = re.compile(
    r"^\s*\((?:[^)]*재료|속재료|소재료)[^)]*\)\s*"
)


class MfdsRecipeImportError(RuntimeError):
    """Raised when official recipe data cannot be fetched or converted safely."""


def _number(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _compact_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _normalize_unit(unit: str) -> str:
    if unit in {"Ts", "T"}:
        return "큰술"
    if unit in {"ts", "t"}:
        return "작은술"
    normalized = unicodedata.normalize("NFKC", unit).lower()
    return {
        "l": "L",
        "kg": "kg",
        "mg": "mg",
        "ml": "ml",
        "cc": "ml",
        "g": "g",
        "tbsp": "큰술",
        "tsp": "작은술",
    }.get(
        normalized,
        normalized,
    )


def _quantity_value(raw_quantity: str) -> float:
    normalized = (
        unicodedata.normalize("NFKC", raw_quantity)
        .replace(" ", "")
        .replace("⁄", "/")
    )
    vulgar_fractions = {
        "¼": 0.25,
        "½": 0.5,
        "¾": 0.75,
        "⅓": 1 / 3,
        "⅔": 2 / 3,
        "⅛": 0.125,
        "⅜": 0.375,
        "⅝": 0.625,
        "⅞": 0.875,
    }
    if raw_quantity.strip() in vulgar_fractions:
        return vulgar_fractions[raw_quantity.strip()]
    if "/" in normalized:
        numerator, denominator = normalized.split("/", 1)
        if float(denominator) == 0:
            raise ValueError("재료 수량의 분모는 0일 수 없습니다.")
        return float(numerator) / float(denominator)
    return float(normalized)


def _split_ingredient_segments(raw_ingredients: str) -> list[str]:
    """Split source ingredients without breaking commas inside parentheses."""
    segments: list[str] = []
    buffer: list[str] = []
    parenthesis_depth = 0
    source_text = re.sub(
        r"<br\s*/?>",
        "\n",
        unicodedata.normalize("NFKC", raw_ingredients or ""),
        flags=re.IGNORECASE,
    )
    for character in source_text:
        if character == "(":
            parenthesis_depth += 1
        elif character == ")" and parenthesis_depth:
            parenthesis_depth -= 1

        if character in ";\n" or (character == "," and parenthesis_depth == 0):
            segment = "".join(buffer).strip()
            if segment:
                segments.append(segment)
            buffer = []
        else:
            buffer.append(character)
    segment = "".join(buffer).strip()
    if segment:
        segments.append(segment)

    # Some API rows omit a comma before a new labelled component, e.g.
    # "물 100g 홍시드레싱 : 홍시 30g". Split only when a colon occurs
    # between two quantified values so ordinary dual notation such as
    # "두부 75g(3/4모)" stays intact.
    expanded: list[str] = []
    pending = list(segments)
    while pending:
        current = pending.pop(0).strip()
        quantity_matches = list(QUANTITY_PATTERN.finditer(current))
        split_at: int | None = None
        for left, right in zip(quantity_matches, quantity_matches[1:], strict=False):
            if ":" in current[left.end() : right.start()]:
                split_at = left.end()
                break
        if split_at is None:
            if current:
                expanded.append(current)
            continue
        head = current[:split_at].strip()
        tail = current[split_at:].strip(" -\t")
        if head:
            expanded.append(head)
        if tail:
            pending.insert(0, tail)
    return expanded


def normalize_source_ingredient_name(text: str) -> str:
    """Normalize the ingredient label that precedes an MFDS quantity."""
    name_text = unicodedata.normalize("NFKC", text or "").strip()

    # A few rows concatenate the previous unquantified condiment and the next
    # section marker: "소금적당량 [팥소]팥 15g".  Only the text after the final
    # section marker belongs to the quantified ingredient.
    if "]" in name_text:
        suffix = name_text.rsplit("]", 1)[-1].strip()
        if suffix:
            name_text = suffix

    previous = None
    while previous != name_text:
        previous = name_text
        name_text = LEADING_BRACKET_LABEL_PATTERN.sub("", name_text).strip()
        name_text = LEADING_PARENTHESIZED_LABEL_PATTERN.sub("", name_text).strip()
        name_text = SECTION_LABEL_PATTERN.sub("", name_text).strip()

    # Some MFDS rows use a recipe/component label before the real ingredient,
    # for example "라이스버거 : 밥 100g".
    if ":" in name_text:
        rightmost = name_text.rsplit(":", 1)[-1].strip()
        if rightmost:
            name_text = rightmost

    # Remove a trailing preparation/cut descriptor before stripping an
    # unfinished quantity parenthesis. Examples include "한돈(사태) 30g",
    # "물(반죽용)(15ml)", and "뒤포리(밴댕이) 50g".
    while re.search(r"\s*\([^()]*\)\s*$", name_text):
        name_text = re.sub(r"\s*\([^()]*\)\s*$", "", name_text).strip()

    # A quantity is frequently written inside an unfinished parenthesis at
    # this point: "자두(200g)" -> "자두(" and
    # "렌틸콩(레드, 50g)" -> "렌틸콩(레드,".
    name_text = re.sub(r"\([^)]*$", "", name_text).strip()
    while re.search(r"\s*\([^()]*\)\s*$", name_text):
        name_text = re.sub(r"\s*\([^()]*\)\s*$", "", name_text).strip()
    name_text = re.sub(r"\d+(?:\.\d+)?\s*[~\-]\s*$", "", name_text).strip()
    # MFDS occasionally numbers duplicate salt rows as 소금1/소금2.
    name_text = re.sub(r"(?<=[가-힣])\s*\d+$", "", name_text).strip()
    name_text = re.sub(r"\s+", " ", name_text)
    return name_text.rstrip("()[]{}:-•*●○■□◆◇ ")


def _ingredient_match_text(segment: str, quantity_match: re.Match[str]) -> str:
    name_text = segment[: quantity_match.start()].strip()
    name_text = re.sub(r"\(\s*\d+\s*/\s*$", "", name_text).strip()
    return normalize_source_ingredient_name(name_text)


def parse_ingredients_with_audit(
    raw_ingredients: str,
    catalog: IngredientCatalog | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ingredient_catalog = catalog or get_ingredient_catalog()
    merged: dict[str, dict[str, Any]] = {}
    quantified_segment_count = 0
    mapped_segment_count = 0
    ignored_segment_count = 0
    unmatched_segments: list[str] = []

    for segment in _split_ingredient_segments(raw_ingredients):
        quantity_match = QUANTITY_PATTERN.search(segment)
        if quantity_match is None:
            continue
        quantified_segment_count += 1

        match_text = _ingredient_match_text(segment, quantity_match)
        if not match_text or _text_key(match_text) in IGNORED_SOURCE_TERM_KEYS:
            ignored_segment_count += 1
            continue
        match = ingredient_catalog.match(match_text, threshold=0.86)
        if match["standard_name"] is None or match["match_type"] == "fuzzy":
            unmatched_segments.append(match_text)
            continue

        canonical_name = str(match["standard_name"])
        if canonical_name in IGNORED_SOURCE_INGREDIENTS:
            ignored_segment_count += 1
            continue
        mapped_segment_count += 1
        quantity = _quantity_value(quantity_match.group("quantity"))
        unit = _normalize_unit(quantity_match.group("unit"))
        if canonical_name in merged:
            # The DB stores one row per (recipe, canonical ingredient). When
            # the source repeats the same ingredient in multiple components,
            # add quantities only if their units are compatible. Otherwise
            # retain the first official quantity while still counting both
            # source segments in the parse audit.
            if merged[canonical_name]["unit"] == unit:
                merged[canonical_name]["quantity"] = _compact_number(
                    float(merged[canonical_name]["quantity"]) + quantity
                )
            continue

        merged[canonical_name] = {
            "canonical_name": canonical_name,
            "quantity": _compact_number(quantity),
            "unit": unit,
            "optional": canonical_name in OPTIONAL_INGREDIENTS,
        }

    accepted_count = mapped_segment_count + ignored_segment_count
    coverage = (
        accepted_count / quantified_segment_count if quantified_segment_count else 0.0
    )
    return list(merged.values()), {
        "quantified_segment_count": quantified_segment_count,
        "mapped_segment_count": mapped_segment_count,
        "ignored_segment_count": ignored_segment_count,
        "unmatched_segment_count": len(unmatched_segments),
        "coverage": round(coverage, 4),
        "unmatched_segments": unmatched_segments,
    }


def parse_ingredients(
    raw_ingredients: str,
    catalog: IngredientCatalog | None = None,
) -> list[dict[str, Any]]:
    """Map only explicitly quantified source ingredients to the local master list."""
    ingredients, _ = parse_ingredients_with_audit(raw_ingredients, catalog)
    return ingredients


def _text_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[^0-9a-z가-힣]", "", normalized)


def _catalog_names_in_text(
    text: str,
    catalog: IngredientCatalog,
) -> set[str]:
    """Return curated ingredient names explicitly written in a recipe title."""
    text_key = _text_key(text)
    if not text_key:
        return set()

    matches: list[tuple[int, int, str]] = []
    for entry in catalog.entries:
        for candidate in (entry.name, *entry.aliases):
            candidate_key = _text_key(candidate)
            # One-character aliases are too ambiguous for substring detection.
            if len(candidate_key) < 2:
                continue
            start = text_key.find(candidate_key)
            while start >= 0:
                matches.append((start, start + len(candidate_key), entry.name))
                start = text_key.find(candidate_key, start + 1)

    # Prefer the longest name at overlapping positions. This prevents nested
    # false positives such as 배추 in 양배추 and 고추 in 고추장.
    selected: list[tuple[int, int, str]] = []
    for start, end, name in sorted(
        matches,
        key=lambda item: (-(item[1] - item[0]), item[0], item[2]),
    ):
        if any(
            start < selected_end and selected_start < end
            for selected_start, selected_end, _ in selected
        ):
            continue
        selected.append((start, end, name))
    names = {name for _, _, name in selected}
    return names


def mark_primary_ingredients(
    recipe_name: str,
    ingredients: list[dict[str, Any]],
    catalog: IngredientCatalog,
) -> list[dict[str, Any]]:
    """Mark title ingredients, or the first required source item, as primary."""
    parsed_names = {
        str(ingredient.get("canonical_name") or "") for ingredient in ingredients
    }
    primary_names = _catalog_names_in_text(recipe_name, catalog).intersection(
        parsed_names
    )
    if not primary_names:
        first_required = next(
            (
                str(ingredient.get("canonical_name") or "")
                for ingredient in ingredients
                if not ingredient.get("optional", False)
            ),
            "",
        )
        if first_required:
            primary_names.add(first_required)

    marked: list[dict[str, Any]] = []
    for ingredient in ingredients:
        updated = dict(ingredient)
        updated.pop("primary", None)
        if str(updated.get("canonical_name") or "") in primary_names:
            updated["primary"] = True
            # A title/core ingredient cannot simultaneously be optional.
            updated["optional"] = False
        marked.append(updated)
    return marked


def _has_missing_title_ingredient(
    recipe_name: str,
    raw_ingredients: str,
    ingredients: list[dict[str, Any]],
    catalog: IngredientCatalog,
) -> bool:
    title_names = _catalog_names_in_text(recipe_name, catalog)
    if not title_names:
        return False

    quantified_source_names: set[str] = set()
    for segment in _split_ingredient_segments(raw_ingredients):
        quantity_match = QUANTITY_PATTERN.search(segment)
        if quantity_match is None:
            continue
        match_text = _ingredient_match_text(segment, quantity_match)
        quantified_source_names.update(_catalog_names_in_text(match_text, catalog))
    source_title_names = title_names.intersection(quantified_source_names)

    parsed_names = {
        str(ingredient.get("canonical_name") or "") for ingredient in ingredients
    }
    return bool(source_title_names.difference(parsed_names))


def parse_steps(row: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    for index in range(1, 21):
        value = str(row.get(f"MANUAL{index:02d}") or "").strip()
        if not value:
            continue
        value = STEP_NUMBER_PATTERN.sub("", value).strip()
        value = re.sub(r"\s+", " ", value)
        if value:
            steps.append(value)
    return steps


def map_mfds_row(
    row: dict[str, Any],
    catalog: IngredientCatalog | None = None,
) -> dict[str, Any] | None:
    source_recipe_id = str(row.get("RCP_SEQ") or "").strip()
    recipe_name = str(row.get("RCP_NM") or "").strip()
    raw_ingredients = str(row.get("RCP_PARTS_DTLS") or "").strip()
    ingredient_catalog = catalog or get_ingredient_catalog()
    ingredients, ingredient_audit = parse_ingredients_with_audit(
        raw_ingredients, ingredient_catalog
    )
    steps = parse_steps(row)
    if not source_recipe_id or not recipe_name or not steps or not ingredients:
        return None
    if not any(not ingredient["optional"] for ingredient in ingredients):
        return None
    if ingredient_audit["coverage"] < MIN_INGREDIENT_PARSE_COVERAGE:
        return None
    if _has_missing_title_ingredient(
        recipe_name,
        raw_ingredients,
        ingredients,
        ingredient_catalog,
    ):
        return None
    ingredients = mark_primary_ingredients(
        recipe_name,
        ingredients,
        ingredient_catalog,
    )

    cooking_method = str(row.get("RCP_WAY2") or "").strip()
    nutrition_values = {
        "serving_size_g": _number(row.get("INFO_WGT")),
        "calories_kcal": _number(row.get("INFO_ENG")),
        "carbohydrate_g": _number(row.get("INFO_CAR")),
        "protein_g": _number(row.get("INFO_PRO")),
        "fat_g": _number(row.get("INFO_FAT")),
        "sodium_mg": _number(row.get("INFO_NA")),
    }
    nutrition = {
        key: (_compact_number(value) if value is not None else None)
        for key, value in nutrition_values.items()
    }
    nutrition["source"] = SOURCE_NAME

    return {
        "source": SOURCE_NAME,
        "source_recipe_id": source_recipe_id,
        "recipe_name": recipe_name,
        "cook_time": None,
        "category": str(row.get("RCP_PAT2") or "기타").strip() or "기타",
        "description": (
            f"식품의약품안전처 조리식품 레시피 DB의 {cooking_method} 레시피"
            if cooking_method
            else "식품의약품안전처 조리식품 레시피 DB 레시피"
        ),
        "image_url": str(
            row.get("ATT_FILE_NO_MAIN") or row.get("ATT_FILE_NO_MK") or ""
        ).strip()
        or None,
        "ingredients": ingredients,
        "steps": steps,
        "nutrition": nutrition,
        "source_ingredients_raw": raw_ingredients,
        "ingredient_parse_coverage": ingredient_audit["coverage"],
    }


def _api_url(api_key: str, start: int, end: int) -> str:
    return (
        f"{API_ROOT}/{quote(api_key, safe='')}/{SERVICE_ID}/json/{start}/{end}"
    )


def fetch_page(api_key: str, start: int, end: int) -> tuple[int, list[dict[str, Any]]]:
    request = Request(
        _api_url(api_key, start, end),
        headers={"User-Agent": "fridiet-ai-recipe-importer/1.0"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.load(response)
    except (OSError, ValueError) as error:
        raise MfdsRecipeImportError("식약처 레시피 API를 호출할 수 없습니다.") from error

    service = payload.get(SERVICE_ID)
    if not isinstance(service, dict):
        raise MfdsRecipeImportError("식약처 API 응답에 COOKRCP01 데이터가 없습니다.")
    result = service.get("RESULT") or {}
    if result.get("CODE") != "INFO-000":
        message = result.get("MSG") or "알 수 없는 API 오류"
        raise MfdsRecipeImportError(f"식약처 API 오류: {message}")

    try:
        total_count = int(service.get("total_count") or 0)
    except (TypeError, ValueError) as error:
        raise MfdsRecipeImportError("식약처 API의 전체 건수가 올바르지 않습니다.") from error
    rows = service.get("row") or []
    if not isinstance(rows, list):
        raise MfdsRecipeImportError("식약처 API의 레시피 목록이 올바르지 않습니다.")
    return total_count, rows


def fetch_all_rows(
    api_key: str,
    *,
    limit: int | None = None,
    page_size: int = MAX_PAGE_SIZE,
) -> list[dict[str, Any]]:
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size는 1~{MAX_PAGE_SIZE}여야 합니다.")
    if limit is not None and limit < 1:
        raise ValueError("limit은 1 이상이어야 합니다.")

    rows: list[dict[str, Any]] = []
    start = 1
    total_count: int | None = None
    while total_count is None or start <= total_count:
        remaining = None if limit is None else limit - len(rows)
        if remaining is not None and remaining <= 0:
            break
        request_count = page_size if remaining is None else min(page_size, remaining)
        end = start + request_count - 1
        total_count, page_rows = fetch_page(api_key, start, end)
        if not page_rows:
            break
        rows.extend(page_rows)
        start += len(page_rows)

        # The public sample key intentionally returns only five rows regardless
        # of the requested range, so it must never be treated as a full import.
        if api_key == "sample":
            break
    return rows[:limit] if limit is not None else rows


def merge_recipes(
    existing: list[dict[str, Any]],
    imported: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(existing)
    positions = {
        (str(recipe.get("source") or ""), str(recipe.get("source_recipe_id") or "")): index
        for index, recipe in enumerate(merged)
    }
    for recipe in imported:
        key = (str(recipe.get("source") or ""), str(recipe["source_recipe_id"]))
        position = positions.get(key)
        if position is None:
            positions[key] = len(merged)
            merged.append(recipe)
        else:
            merged[position] = recipe
    return merged


def _load_existing(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as file:
            payload = json.load(file)
    except (OSError, json.JSONDecodeError) as error:
        raise MfdsRecipeImportError(f"기존 레시피 파일을 읽을 수 없습니다: {path}") from error
    if not isinstance(payload, list):
        raise MfdsRecipeImportError(f"기존 레시피 파일은 JSON 배열이어야 합니다: {path}")
    return payload


def _write_json_atomic(path: Path, payload: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            suffix=".json.tmp",
            dir=path.parent,
            delete=False,
        ) as file:
            temporary_path = Path(file.name)
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        temporary_path.replace(path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise MfdsRecipeImportError(f"레시피 파일을 저장할 수 없습니다: {path}") from error


def main() -> None:
    parser = argparse.ArgumentParser(
        description="식약처 COOKRCP01 레시피를 Fridiet JSON 데이터셋으로 가져옵니다."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--page-size", type=int, default=MAX_PAGE_SIZE)
    parser.add_argument(
        "--sample",
        action="store_true",
        help="인증키 없이 공식 샘플 5건만 변환합니다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="다운로드와 변환 결과만 확인하고 파일은 쓰지 않습니다.",
    )
    args = parser.parse_args()

    api_key = "sample" if args.sample else os.getenv("MFDS_API_KEY", "").strip()
    if not api_key:
        parser.error("MFDS_API_KEY 환경 변수를 설정하거나 --sample을 사용하세요.")

    rows = fetch_all_rows(api_key, limit=args.limit, page_size=args.page_size)
    catalog = get_ingredient_catalog()
    converted = [recipe for row in rows if (recipe := map_mfds_row(row, catalog))]
    skipped = len(rows) - len(converted)
    existing = _load_existing(args.output)
    merged = merge_recipes(existing, converted)

    if not args.dry_run:
        _write_json_atomic(args.output, merged)

    action = "검증" if args.dry_run else "저장"
    print(
        f"{action} 완료: API {len(rows):,}건, 변환 {len(converted):,}건, "
        f"제외 {skipped:,}건, 전체 데이터셋 {len(merged):,}건"
    )


if __name__ == "__main__":
    main()
