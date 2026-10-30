from __future__ import annotations

import argparse
import json
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from app.ingredient_matcher import IngredientCatalog, IngredientEntry, lookup_key
from scripts.import_mfds_recipes import (
    IGNORED_SOURCE_TERMS,
    QUANTITY_PATTERN,
    _ingredient_match_text,
    _split_ingredient_segments,
)


AI_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECIPES_PATH = AI_ROOT / "app" / "data" / "recipes_ko.json"
DEFAULT_DICTIONARY_PATH = AI_ROOT / "app" / "data" / "ingredients_ko.json"

# High-frequency MFDS source variants that should share an inventory-level
# canonical ingredient instead of becoming separate vocabulary items.
CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "돼지고기": (
        "통삼겹",
        "돼지등심",
        "돈등심",
        "돈민찌",
        "다진 돼기고기",
        "등갈비",
        "찜갈비",
        "양념족발",
        "한돈",
        "쪽갈비",
        "족발",
    ),
    "소고기": (
        "L.A갈비",
        "우민찌",
        "우삼겹",
        "우육",
        "우목심",
        "양지육",
        "차돌박이",
        "소 채끝살",
        "소등심",
        "소불고기",
        "등심얇게썬것",
        "안심",
        "쇠고기안심",
    ),
    "닭고기": (
        "닭",
        "닭 정육",
        "닭날개",
        "닭봉",
        "닭살",
        "닭안심살",
        "닭다릿살",
        "닭뼈",
        "삼계탕용 닭",
        "토막 낸 닭",
    ),
    "새우": (
        "새우살",
        "자숙새우",
        "보리새우",
        "생새우",
        "칵텔새우",
        "새뱅이",
        "새우가루",
    ),
    "꽃게": ("게살", "게살냉동"),
    "소라": ("소라살",),
    "꼬막": ("꼬막살", "데친 꼬막살"),
    "명태": ("동태포", "황태", "황태포", "명태포", "황태머리"),
    "멸치": ("건멸치", "다시멸치", "뒤포리", "디포리", "앤쵸비", "실치"),
    "배추": ("배춧잎", "배추잎", "얼갈이배추"),
    "양배추": ("적채",),
    "양파": (
        "깐양파",
        "적양파",
        "레드어니언",
        "양파즙",
        "양파가루",
        "양파채썬것",
    ),
    "대파": ("파다진것", "다진쪽파", "대파뿌리", "실파송송썬것"),
    "마늘": ("알마늘", "마늘칩", "깐마늘", "편마늘", "마늘튀김"),
    "생강": ("깐생강", "통생강", "생강가루"),
    "당근": ("당근다진것", "다진당근", "당근갈은것", "당근채썬것"),
    "고추": (
        "꽈리고추",
        "아삭이고추",
        "멕시코고추",
        "실고추",
        "마른고추",
        "붉은고추",
        "청고주",
    ),
    "고추장": ("고추자",),
    "고춧가루": ("카엔패퍼", "카엔페퍼"),
    "애호박": ("주키니호박", "쥬키니호박", "돼지호박"),
    "버섯": (
        "백일송이",
        "목이버섯",
        "노루궁뎅이버섯",
        "만가닥버섯",
        "만송이버섯",
    ),
    "달걀": ("달걀물", "달걀지단", "삶은달걀", "수란", "노른자"),
    "햄": ("통조림 햄", "슬라이스햄", "햄다진것"),
    "치즈": (
        "치즈가루",
        "치즈슬라이스",
        "어린이치즈",
        "파마산가루",
        "스트링치즈",
        "슬라이스 체더치즈",
        "에벤탈치즈",
        "피자치즈",
    ),
    "버터": ("저염버터",),
    "소금": (
        "저염소금",
        "레몬소금",
        "허브소금",
        "버섯마늘소금",
        "녹차소금",
        "누룩소금",
    ),
    "간장": (
        "어간장",
        "간편 어간장",
        "간편어간장",
        "가다랑어장국",
        "노두유",
    ),
    "식초": ("감식초", "허브식초", "홍초", "발사믹크레마", "발사믹크림"),
    "식용유": (
        "식물성기름",
        "고추기름",
        "마늘오일",
        "허브오일",
        "대두유",
        "현미휴",
    ),
    "올리브유": ("오릴브오일",),
    "전분": ("전분가루", "녹말물", "물녹말", "전분물"),
    "쌀": (
        "멥쌀",
        "불린 쌀",
        "찬밥",
        "귀리밥",
        "현미밥",
        "찹쌀현미밥",
        "강황쌀",
        "볶음밥 쌀",
    ),
    "잡곡": ("잡곡밥",),
    "현미": ("볶은현미",),
    "메밀": ("메밀면",),
    "스파게티": (
        "파스타",
        "펜네",
        "먹물파스타",
        "푸실리",
        "탈리아텔레",
        "쫄면 사리",
    ),
    "라이스페이퍼": ("라이스페퍼",),
    "크랜베리": ("크린베리", "크렌베리"),
    "로즈마리": ("로즈메리",),
    "파슬리": ("파스리가루",),
    "후추": ("후추가루", "흰추후", "핑크 페퍼"),
    "와사비": ("와사미",),
    "맛술": ("요리술", "조미술", "미향"),
    "카레가루": ("커리가루",),
    "물": ("쌀뜨물", "절임물", "물김치국물", "따뜻한물", "물미지근한것"),
    "육수": (
        "사골육수",
        "야채육수",
        "채소육수",
        "치킨육수",
        "해물육수",
        "천연다시다",
        "다시물",
        "다시팩",
        "닭뼈육수",
        "야채국물",
        "치킨스톡",
    ),
    "미역": (
        "건미역",
        "마른 미역",
        "마른미역",
        "건조미역",
        "미역마른것",
        "불린미역",
    ),
    "청국장": ("청국장가루", "생청국장"),
    "인삼": ("수삼",),
    "액젓": ("멸치액젓", "까나리액젓", "젓갈"),
    "자두": ("말린 자두", "푸룬"),
    "래디쉬": ("레디쉬", "래디시"),
    "피클": ("다진 오이피클", "오이피클", "할라피뇨 피클"),
    "콩": (
        "렌틸콩",
        "강낭콩",
        "병아리콩",
        "통조림콩",
        "대두",
        "검정콩",
        "노란콩",
        "저염재래된장 콩",
    ),
    "땅콩": ("땅콩잼", "땅콩버터", "땅콩가루"),
    "녹차": ("가루녹차", "녹차가루", "잎녹차"),
    "강황": ("강황가루", "울금가루"),
    "가쓰오부시": ("가츠오부시",),
    "곤드레나물": ("건곤드레",),
    "당면": ("건당면", "당면불린것"),
    "바질": ("바질잎", "건바질", "바질다진것", "바질마른것"),
    "체리": ("건조체리",),
    "타임": ("건타임", "타임마른것"),
    "근대": ("적근대",),
    "연어": ("슬라이스연어",),
    "낙지": ("낙지다리",),
    "돌나물": ("돈나물",),
    "무": ("무우", "조선무"),
    "김": ("김밥용 김", "파래김", "생김", "김가루"),
    "김치": ("동치미", "명이김치", "석박지"),
    "매실": ("매실농축액", "매실원액"),
    "사과": ("미니사과", "사과작은것"),
    "올리브": ("블랙올리브",),
    "어린잎": ("베이비채소", "어린싹", "어린채소", "쌈채"),
    "피망": ("붉은피망",),
    "토란": ("알토란",),
    "옥수수": ("영콘",),
    "두부": ("으깬두부",),
    "된장": ("보리된장", "일식된장", "일본된장", "하얀된장", "미소된장"),
    "겨자": (
        "씨겨자",
        "발효겨자",
        "홀그레인",
        "홀그레인 머스터드",
        "홀그레인 머스터즈",
        "홀그레인머스터드",
        "르네디종 홀그레인머스터",
    ),
    "또띠아": ("토르티야",),
    "카스텔라": ("카스테라",),
    "코코넛밀크": ("코코넛우유",),
    "발효종": ("르뱅 발효종", "파트 페레멘테", "사과종"),
    "제빵개량제": ("파인소프트 C", "파인소프트 T"),
    "조미료": ("수제조미료", "천연조미료"),
    "도미": ("도미살",),
    "생선살": ("흰살생선", "손질된 흰살생선"),
    "오가피": ("가시오가피",),
    "우렁이": ("논우렁",),
    "잣": ("다진 잣", "잣가루"),
    "부추": ("호부추",),
    "죽순": ("죽순통조림",),
    "계피": ("통계피",),
    "와인": ("포도주",),
    "국수": ("쌀국수가는것",),
    "빵": ("바케트", "버거빵", "찰깨빵", "치아바타"),
    "조개": ("백합",),
    "배": ("배사과소스 배",),
    "레몬": ("레몬껍질", "레몬제스트"),
    "레몬즙": ("레몬쥬스",),
    "사과주스": ("사과쥬스", "사과즙"),
    "석류주스": ("석류 원액",),
    "마스카르포네치즈": ("마스카포네치즈",),
    "쑥가루": ("반죽 쑥가루", "쑥 파우더"),
    "빠금장": ("빠금장소스 빠금장",),
    "바질페스토": ("잣 바질페이소스",),
    "참깨": ("검정깨",),
    "호박": ("호박가루",),
    "리치": ("리치캔",),
    "물밤": ("물밤다진것",),
    "곤약쌀": ("불린 곤약쌀",),
    "민들레잎": ("민들레 잎",),
    "귤즙": ("귤 즙",),
    "코코넛오일": ("코코넛 오일",),
    "카카오닙스": ("카카오 닙스",),
    "바닐라": ("바닐라빈", "바닐라에센스", "바닐라향"),
    "초콜릿": ("블랙초콜릿",),
    "칠리소스": ("스위트칠리", "스리라차", "타바스코"),
    "생크림": ("크림",),
    "분유": ("탈지분유",),
}

ALLOWED_SINGLE_CHARACTER_TERMS = {"조", "쑥", "팥", "톳"}
INVALID_GENERATED_TERM_PATTERN = re.compile(r"[\[\](){}<>:]|\d")


def _catalog(payload: list[dict[str, Any]]) -> IngredientCatalog:
    return IngredientCatalog(
        [
            IngredientEntry(
                name=str(item["name"]),
                aliases=tuple(str(value) for value in item.get("aliases") or []),
            )
            for item in payload
        ]
    )


def _deduplicate_aliases(
    payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prefer canonical names and keep each normalized alias under one entry."""
    canonical_owner = {
        lookup_key(str(item["name"])): str(item["name"]) for item in payload
    }
    alias_owner: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    for item in payload:
        name = str(item["name"])
        aliases: list[str] = []
        for value in item.get("aliases") or []:
            alias = str(value).strip()
            key = lookup_key(alias)
            if not key:
                continue
            if key == lookup_key(name):
                continue
            if key in canonical_owner and canonical_owner[key] != name:
                continue
            owner = alias_owner.get(key)
            if owner is not None:
                continue
            alias_owner[key] = name
            aliases.append(alias)
        result.append({"name": name, "aliases": aliases})
    return result


def _source_terms(recipes: list[dict[str, Any]]) -> Counter[str]:
    terms: Counter[str] = Counter()
    ignored_keys = {lookup_key(value) for value in IGNORED_SOURCE_TERMS}
    for recipe in recipes:
        raw_ingredients = str(recipe.get("source_ingredients_raw") or "")
        for segment in _split_ingredient_segments(raw_ingredients):
            quantity_match = QUANTITY_PATTERN.search(segment)
            if quantity_match is None:
                continue
            term = _ingredient_match_text(segment, quantity_match)
            if not term or lookup_key(term) in ignored_keys:
                continue
            terms[term] += 1
    return terms


def sync_dictionary(
    dictionary: list[dict[str, Any]],
    recipes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    updated = [
        {
            "name": str(item["name"]).strip(),
            "aliases": list(dict.fromkeys(str(value).strip() for value in item.get("aliases") or [] if str(value).strip())),
        }
        for item in dictionary
    ]
    by_name = {item["name"]: item for item in updated}
    key_owner = {
        lookup_key(value): item["name"]
        for item in updated
        for value in (item["name"], *item["aliases"])
        if lookup_key(value)
    }

    added_aliases = 0
    added_manual_entries = 0
    for canonical_name, aliases in CANONICAL_ALIASES.items():
        entry = by_name.get(canonical_name)
        if entry is None:
            existing_owner = key_owner.get(lookup_key(canonical_name))
            if existing_owner is not None:
                entry = by_name[existing_owner]
            else:
                entry = {"name": canonical_name, "aliases": []}
                updated.append(entry)
                by_name[canonical_name] = entry
                key_owner[lookup_key(canonical_name)] = canonical_name
                added_manual_entries += 1
        for alias in aliases:
            key = lookup_key(alias)
            if not key or key in key_owner:
                continue
            entry["aliases"].append(alias)
            key_owner[key] = canonical_name
            added_aliases += 1

    generated_entries: list[str] = []
    term_counts = _source_terms(recipes)
    catalog = _catalog(updated)
    for term, _ in sorted(term_counts.items(), key=lambda item: (-item[1], item[0])):
        match = catalog.match(term, threshold=0.86)
        if match["standard_name"] is not None and match["match_type"] != "fuzzy":
            continue
        key = lookup_key(term)
        if not key or key in key_owner:
            continue
        if len(key) == 1 and term not in ALLOWED_SINGLE_CHARACTER_TERMS:
            continue
        if INVALID_GENERATED_TERM_PATTERN.search(term):
            raise ValueError(f"정규화되지 않은 원본 재료명이 남아 있습니다: {term}")

        entry = {"name": term, "aliases": []}
        updated.append(entry)
        by_name[term] = entry
        key_owner[key] = term
        generated_entries.append(term)
        # Refresh so later spelling/compound variants can map to the newly
        # discovered canonical term instead of becoming duplicates.
        catalog = _catalog(updated)

    original_names = {str(item["name"]) for item in dictionary}
    preserved = [item for item in updated if item["name"] in original_names]
    appended = sorted(
        (item for item in updated if item["name"] not in original_names),
        key=lambda item: item["name"],
    )
    result = _deduplicate_aliases(preserved + appended)
    final_catalog = _catalog(result)
    unmapped_source_terms = [
        term
        for term in term_counts
        if (
            (match := final_catalog.match(term, threshold=0.86))["standard_name"] is None
            or match["match_type"] == "fuzzy"
        )
    ]
    return result, {
        "recipe_count": len(recipes),
        "source_term_count": len(term_counts),
        "input_dictionary_count": len(dictionary),
        "output_dictionary_count": len(result),
        "added_manual_entry_count": added_manual_entries,
        "added_alias_count": added_aliases,
        "generated_entry_count": len(generated_entries),
        "generated_entries": generated_entries,
        "unmapped_source_term_count": len(unmapped_source_terms),
        "unmapped_source_terms": unmapped_source_terms,
    }


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
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="레시피 원본에 등장하는 모든 정량 재료를 로컬 재료 사전에 동기화"
    )
    parser.add_argument("--recipes", type=Path, default=DEFAULT_RECIPES_PATH)
    parser.add_argument("--dictionary", type=Path, default=DEFAULT_DICTIONARY_PATH)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    recipes = json.loads(args.recipes.read_text(encoding="utf-8"))
    dictionary = json.loads(args.dictionary.read_text(encoding="utf-8"))
    if not isinstance(recipes, list) or not isinstance(dictionary, list):
        raise SystemExit("레시피와 재료 사전은 JSON 배열이어야 합니다.")

    updated, statistics = sync_dictionary(dictionary, recipes)
    if not args.dry_run:
        _write_json_atomic(args.dictionary, updated)
    print(json.dumps(statistics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
