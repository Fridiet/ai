"""Allow unknown numeric quantities without inventing zero; preserves existing SQL types.

Run after Spring has created inventory_activity. Default dry-run; --apply local only.
MySQL ALTER TABLE auto-commits. No rows are deleted or rewritten.
"""
import argparse
import json
from sqlalchemy import inspect, text
from scripts.seed_development_data import _resolve_engine


def migrate(apply=False):
    engine = _resolve_engine(None)
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError("로컬 DB에서만 실행할 수 있습니다.")
    statements = []
    for table, name in [("inventory_batch", "remaining_quantity"), ("meal_consumption", "quantity_used")]:
        column = next(c for c in inspect(engine).get_columns(table) if c["name"] == name)
        if not column["nullable"]:
            if column.get("default") is not None:
                raise RuntimeError("기존 DEFAULT가 있어 자동 변경을 중단합니다. 마이그레이션을 검토해주세요.")
            sql_type = column["type"].compile(dialect=engine.dialect)
            statements.append(f"ALTER TABLE `{table}` MODIFY COLUMN `{name}` {sql_type} NULL")
    if apply:
        with engine.begin() as connection:
            for statement in statements:
                connection.execute(text(statement))
    return {"applied": apply, "statements": statements}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    print(json.dumps(migrate(parser.parse_args().apply), indent=2))
