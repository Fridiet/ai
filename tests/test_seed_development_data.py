from __future__ import annotations

import bcrypt
from sqlalchemy import create_engine, text

from scripts.seed_development_data import DEV_PASSWORD, seed_development_data


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    statements = (
        "CREATE TABLE `user` (user_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "email TEXT UNIQUE NOT NULL, password_hash TEXT, social_provider TEXT, "
        "health_condition TEXT, health_goal TEXT, name TEXT, is_deleted BOOLEAN NOT NULL, "
        "deleted_at DATETIME, main_fridge_id INTEGER, created_at DATETIME, updated_at DATETIME)",
        "CREATE TABLE fridge (fridge_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, "
        "owner_user_id INTEGER NOT NULL, invite_code TEXT UNIQUE, created_at DATETIME)",
        "CREATE TABLE fridge_member (fridge_member_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "fridge_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL, "
        "status TEXT NOT NULL, joined_at DATETIME)",
        "CREATE TABLE ingredient_master (ingredient_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "canonical_name TEXT UNIQUE NOT NULL)",
        "CREATE TABLE inventory_batch (batch_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "fridge_id INTEGER NOT NULL, ingredient_id INTEGER, ingredient_name TEXT NOT NULL, "
        "location_category TEXT, consume_by_date DATE NOT NULL, remaining_quantity REAL NOT NULL, "
        "unit_type TEXT NOT NULL, unit TEXT, status TEXT NOT NULL, created_by INTEGER NOT NULL, "
        "created_at DATETIME, updated_at DATETIME)",
    )
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        for name in ("두부", "우유", "달걀", "양파", "당근"):
            connection.execute(
                text("INSERT INTO ingredient_master (canonical_name) VALUES (:name)"),
                {"name": name},
            )
    return engine


def test_seeds_spring_backend_data_idempotently() -> None:
    engine = _engine()

    first = seed_development_data(engine)
    second = seed_development_data(engine)

    assert first["schema"] == "spring_backend"
    assert first["inserted_inventory_count"] == 5
    assert second["inserted_inventory_count"] == 0
    with engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM `user`")).scalar_one() == 2
        assert connection.execute(text("SELECT COUNT(*) FROM fridge")).scalar_one() == 1
        assert connection.execute(text("SELECT COUNT(*) FROM fridge_member")).scalar_one() == 2
        assert {
            row[0]
            for row in connection.execute(text("SELECT status FROM fridge_member"))
        } == {"ACCEPTED"}
        assert connection.execute(text("SELECT COUNT(*) FROM inventory_batch")).scalar_one() == 5
        owner = connection.execute(
            text(
                "SELECT password_hash, health_goal, main_fridge_id FROM `user` "
                "WHERE email='owner@example.invalid'"
            )
        ).one()
        member_goal = connection.execute(
            text(
                "SELECT health_goal FROM `user` "
                "WHERE email='member@example.invalid'"
            )
        ).scalar_one()

    assert bcrypt.checkpw(DEV_PASSWORD.encode(), owner.password_hash.encode())
    assert owner.health_goal == "WEIGHT_LOSS"
    assert owner.main_fridge_id == first["fridge_id"]
    assert member_goal == "DIABETES_MANAGEMENT"
