import json
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.database import get_engine
from app.inventory_mapper import infer_default_unit, infer_ingredient_type


DEFAULT_DICTIONARY_PATH = Path(__file__).parents[1] / "app" / "data" / "ingredients_ko.json"


def seed_ingredient_master(
    engine: Engine | None = None,
    dictionary_path: Path = DEFAULT_DICTIONARY_PATH,
) -> dict[str, int]:
    target_engine = engine or get_engine()
    tables = set(inspect(target_engine).get_table_names())
    required = {"ingredient_master", "ingredient_alias"}
    if not required.issubset(tables):
        missing = ", ".join(sorted(required - tables))
        raise RuntimeError(f"식재료 마스터 테이블이 없습니다: {missing}")

    entries = json.loads(dictionary_path.read_text(encoding="utf-8"))
    inserted_master_count = 0
    inserted_alias_count = 0

    with Session(target_engine) as session, session.begin():
        for entry in entries:
            canonical_name = entry["name"].strip()
            ingredient_id = session.execute(
                text(
                    "SELECT ingredient_id FROM ingredient_master "
                    "WHERE canonical_name=:canonical_name LIMIT 1"
                ),
                {"canonical_name": canonical_name},
            ).scalar_one_or_none()

            if ingredient_id is None:
                ingredient_type = infer_ingredient_type(canonical_name, None)
                default_unit = infer_default_unit(canonical_name, ingredient_type)
                result = session.execute(
                    text(
                        "INSERT INTO ingredient_master "
                        "(ingredient_type, canonical_name, default_unit) "
                        "VALUES (:ingredient_type, :canonical_name, :default_unit)"
                    ),
                    {
                        "ingredient_type": ingredient_type,
                        "canonical_name": canonical_name,
                        "default_unit": default_unit,
                    },
                )
                ingredient_id = result.lastrowid
                inserted_master_count += 1

            for alias in entry.get("aliases", []):
                alias_text = alias.strip()
                if not alias_text or alias_text == canonical_name:
                    continue
                exists = session.execute(
                    text(
                        "SELECT alias_id FROM ingredient_alias "
                        "WHERE alias_text=:alias_text AND ingredient_id=:ingredient_id LIMIT 1"
                    ),
                    {"alias_text": alias_text, "ingredient_id": ingredient_id},
                ).scalar_one_or_none()
                if exists is None:
                    session.execute(
                        text(
                            "INSERT INTO ingredient_alias (alias_text, ingredient_id, confidence) "
                            "VALUES (:alias_text, :ingredient_id, 1.0)"
                        ),
                        {"alias_text": alias_text, "ingredient_id": ingredient_id},
                    )
                    inserted_alias_count += 1

    return {
        "dictionary_count": len(entries),
        "inserted_master_count": inserted_master_count,
        "inserted_alias_count": inserted_alias_count,
    }


def main() -> None:
    result = seed_ingredient_master()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
