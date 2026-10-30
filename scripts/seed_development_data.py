from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date, timedelta
from pathlib import Path

import bcrypt
from sqlalchemy import URL, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session


DEV_PASSWORD = "local-development-placeholder"
DEV_USERS = (
    {
        "email": "owner@example.invalid",
        "name": "개발용 소유자",
        "health_condition": "WEIGHT_MANAGEMENT",
        "health_goal": "WEIGHT_LOSS",
    },
    {
        "email": "member@example.invalid",
        "name": "개발용 멤버",
        "health_condition": "DIABETES",
        "health_goal": "DIABETES_MANAGEMENT",
    },
)
DEV_FRIDGE_NAME = "개발용 공유 냉장고"
DEV_INVITE_CODE = "DEV001"
DEV_INVENTORY = (
    ("두부", 300.0, "g", 3, "REFRIGERATED"),
    ("우유", 1000.0, "ml", 5, "REFRIGERATED"),
    ("달걀", 10.0, "개", 10, "REFRIGERATED"),
    ("양파", 1000.0, "g", 14, "ROOM_TEMP"),
    ("당근", 500.0, "g", 7, "REFRIGERATED"),
)


def _spring_value(raw_value: str) -> str:
    value = raw_value.strip().strip('"').strip("'")
    match = re.fullmatch(r"\$\{([^}:]+)(?::([^}]*))?}", value)
    if not match:
        return value
    env_name, default = match.groups()
    return os.getenv(env_name, default or "")


def _engine_from_spring_config() -> Engine:
    project_root = Path(__file__).resolve().parents[2]
    config_path = project_root / "fridiet" / "src" / "main" / "resources" / "application.yml"
    if not config_path.exists():
        raise RuntimeError(
            "DATABASE_URL이 없고 Spring application.yml도 찾지 못했습니다."
        )

    config = config_path.read_text(encoding="utf-8")
    jdbc_match = re.search(
        r"(?m)^\s*url:\s*jdbc:mysql://([^:/\s]+)(?::(\d+))?/([^?\s]+)",
        config,
    )
    username_match = re.search(r"(?m)^\s*username:\s*([^\r\n#]+)", config)
    password_match = re.search(r"(?m)^\s*password:\s*([^\r\n#]+)", config)
    if not jdbc_match or not username_match or not password_match:
        raise RuntimeError("Spring application.yml의 MySQL 설정을 해석하지 못했습니다.")

    host, raw_port, database = jdbc_match.groups()
    url = URL.create(
        "mysql+pymysql",
        username=_spring_value(username_match.group(1)),
        password=_spring_value(password_match.group(1)),
        host=host,
        port=int(raw_port or "3306"),
        database=database,
        query={"charset": "utf8mb4"},
    )
    return create_engine(url, pool_pre_ping=True)


def _resolve_engine(database_url: str | None) -> Engine:
    resolved_url = database_url or os.getenv("DATABASE_URL")
    if resolved_url:
        return create_engine(resolved_url, pool_pre_ping=True)
    return _engine_from_spring_config()


def _upsert_user(session: Session, user: dict[str, str], password_hash: str) -> int:
    user_id = session.execute(
        text("SELECT user_id FROM `user` WHERE email=:email LIMIT 1"),
        {"email": user["email"]},
    ).scalar_one_or_none()
    parameters = {
        **user,
        "password_hash": password_hash,
        "is_deleted": False,
    }
    if user_id is None:
        result = session.execute(
            text(
                "INSERT INTO `user` "
                "(email, password_hash, social_provider, health_condition, health_goal, "
                "name, is_deleted, created_at, updated_at) "
                "VALUES (:email, :password_hash, 'LOCAL', :health_condition, :health_goal, "
                ":name, :is_deleted, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            parameters,
        )
        return int(result.lastrowid)

    session.execute(
        text(
            "UPDATE `user` SET password_hash=:password_hash, social_provider='LOCAL', "
            "health_condition=:health_condition, health_goal=:health_goal, name=:name, "
            "is_deleted=:is_deleted, deleted_at=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE user_id=:user_id"
        ),
        {**parameters, "user_id": user_id},
    )
    return int(user_id)


def _upsert_fridge_member(
    session: Session,
    *,
    fridge_id: int,
    user_id: int,
    role: str,
) -> None:
    member_id = session.execute(
        text(
            "SELECT fridge_member_id FROM fridge_member "
            "WHERE fridge_id=:fridge_id AND user_id=:user_id LIMIT 1"
        ),
        {"fridge_id": fridge_id, "user_id": user_id},
    ).scalar_one_or_none()
    if member_id is None:
        session.execute(
            text(
                "INSERT INTO fridge_member "
                "(fridge_id, user_id, role, status, joined_at) "
                "VALUES (:fridge_id, :user_id, :role, 'ACCEPTED', CURRENT_TIMESTAMP)"
            ),
            {"fridge_id": fridge_id, "user_id": user_id, "role": role},
        )
        return
    session.execute(
        text(
            "UPDATE fridge_member SET role=:role, status='ACCEPTED' "
            "WHERE fridge_member_id=:member_id"
        ),
        {"member_id": member_id, "role": role},
    )


def seed_development_data(
    engine: Engine | None = None,
    *,
    database_url: str | None = None,
) -> dict:
    target_engine = engine or _resolve_engine(database_url)
    table_names = set(inspect(target_engine).get_table_names())
    required_tables = {
        "user",
        "fridge",
        "fridge_member",
        "ingredient_master",
        "inventory_batch",
    }
    missing = required_tables - table_names
    if missing:
        raise RuntimeError(
            "Spring 개발 데이터 테이블이 없습니다: "
            + ", ".join(sorted(missing))
        )

    password_hash = bcrypt.hashpw(
        DEV_PASSWORD.encode("utf-8"), bcrypt.gensalt(rounds=10)
    ).decode("utf-8")
    today = date.today()
    inserted_inventory_count = 0

    with Session(target_engine) as session, session.begin():
        owner_id = _upsert_user(session, DEV_USERS[0], password_hash)
        member_id = _upsert_user(session, DEV_USERS[1], password_hash)

        fridge_id = session.execute(
            text("SELECT fridge_id FROM fridge WHERE invite_code=:invite_code LIMIT 1"),
            {"invite_code": DEV_INVITE_CODE},
        ).scalar_one_or_none()
        if fridge_id is None:
            fridge_result = session.execute(
                text(
                    "INSERT INTO fridge "
                    "(name, owner_user_id, invite_code, created_at) "
                    "VALUES (:name, :owner_id, :invite_code, CURRENT_TIMESTAMP)"
                ),
                {
                    "name": DEV_FRIDGE_NAME,
                    "owner_id": owner_id,
                    "invite_code": DEV_INVITE_CODE,
                },
            )
            fridge_id = int(fridge_result.lastrowid)
        else:
            fridge_id = int(fridge_id)
            session.execute(
                text(
                    "UPDATE fridge SET name=:name, owner_user_id=:owner_id "
                    "WHERE fridge_id=:fridge_id"
                ),
                {
                    "name": DEV_FRIDGE_NAME,
                    "owner_id": owner_id,
                    "fridge_id": fridge_id,
                },
            )

        _upsert_fridge_member(
            session,
            fridge_id=fridge_id,
            user_id=owner_id,
            role="OWNER",
        )
        _upsert_fridge_member(
            session,
            fridge_id=fridge_id,
            user_id=member_id,
            role="MEMBER",
        )
        session.execute(
            text(
                "UPDATE `user` SET main_fridge_id=:fridge_id "
                "WHERE user_id IN (:owner_id, :member_id)"
            ),
            {
                "fridge_id": fridge_id,
                "owner_id": owner_id,
                "member_id": member_id,
            },
        )

        for canonical_name, quantity, unit, days, location_category in DEV_INVENTORY:
            ingredient_id = session.execute(
                text(
                    "SELECT ingredient_id FROM ingredient_master "
                    "WHERE canonical_name=:canonical_name LIMIT 1"
                ),
                {"canonical_name": canonical_name},
            ).scalar_one_or_none()
            if ingredient_id is None:
                raise RuntimeError(
                    f"ingredient_master에 없는 개발용 식재료입니다: {canonical_name}"
                )

            existing_batch = session.execute(
                text(
                    "SELECT batch_id FROM inventory_batch "
                    "WHERE fridge_id=:fridge_id AND ingredient_name=:ingredient_name "
                    "AND created_by=:created_by AND status='ACTIVE' LIMIT 1"
                ),
                {
                    "fridge_id": fridge_id,
                    "ingredient_name": canonical_name,
                    "created_by": owner_id,
                },
            ).scalar_one_or_none()
            if existing_batch is not None:
                continue

            session.execute(
                text(
                    "INSERT INTO inventory_batch "
                    "(fridge_id, ingredient_id, ingredient_name, location_category, "
                    "consume_by_date, remaining_quantity, unit_type, unit, status, created_by, "
                    "created_at, updated_at) "
                    "VALUES (:fridge_id, :ingredient_id, :ingredient_name, :location_category, "
                    ":consume_by_date, :quantity, 'QUANTITY', :unit, 'ACTIVE', :created_by, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "fridge_id": fridge_id,
                    "ingredient_id": ingredient_id,
                    "ingredient_name": canonical_name,
                    "location_category": location_category,
                    "consume_by_date": today + timedelta(days=days),
                    "quantity": quantity,
                    "unit": unit,
                    "created_by": owner_id,
                },
            )
            inserted_inventory_count += 1

    return {
        "schema": "spring_backend",
        "owner_user_id": owner_id,
        "member_user_id": member_id,
        "fridge_id": fridge_id,
        "invite_code": DEV_INVITE_CODE,
        "inserted_inventory_count": inserted_inventory_count,
        "health_goals": [user["health_goal"] for user in DEV_USERS],
        "login_accounts": [
            {"email": user["email"], "password": DEV_PASSWORD}
            for user in DEV_USERS
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Spring 백엔드 스키마에 개발용 사용자·냉장고·재고 적재"
    )
    parser.add_argument(
        "--database-url",
        help="미지정 시 DATABASE_URL 또는 Spring application.yml을 사용합니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            seed_development_data(database_url=args.database_url),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
