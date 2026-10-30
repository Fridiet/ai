import json
import os
from datetime import date, datetime
from functools import lru_cache

from sqlalchemy import BigInteger, Date, DateTime, Float, Integer, String, Text, UniqueConstraint, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import settings
from .schemas import InventoryCandidatesResponse


class DatabaseNotConfiguredError(RuntimeError):
    pass


class DatabaseSchemaMissingError(RuntimeError):
    pass


class OcrScanNotFoundError(RuntimeError):
    pass


class OcrScanAlreadyReviewedError(RuntimeError):
    pass


class InventoryReviewValidationError(RuntimeError):
    pass


class OcrContextValidationError(RuntimeError):
    pass


class Base(DeclarativeBase):
    pass


ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class OcrScanRecord(Base):
    __tablename__ = "ocr_scan"

    ocr_scan_id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    fridge_id: Mapped[int] = mapped_column(ID_TYPE, nullable=False, index=True)
    created_by: Mapped[int] = mapped_column(ID_TYPE, nullable=False)
    filename: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_REVIEW")
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mapped_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class OcrScanItemRecord(Base):
    __tablename__ = "ocr_scan_item"
    __table_args__ = (
        UniqueConstraint("ocr_scan_id", "row_index", name="uk_ocr_scan_item_row"),
    )

    ocr_scan_item_id: Mapped[int] = mapped_column(ID_TYPE, primary_key=True, autoincrement=True)
    ocr_scan_id: Mapped[int] = mapped_column(ID_TYPE, nullable=False, index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    ingredient_id: Mapped[int | None] = mapped_column(ID_TYPE, index=True)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    recognized_product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    normalized_product_name: Mapped[str] = mapped_column(String(500), nullable=False)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    match_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    remaining_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    consume_by_date: Mapped[date | None] = mapped_column(Date)
    storage_type: Mapped[str] = mapped_column(String(32), nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_REVIEW")
    candidate_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=datetime.now)
    inventory_batch_id: Mapped[int | None] = mapped_column(ID_TYPE)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    if not settings.database_url:
        raise DatabaseNotConfiguredError("DATABASE_URL 환경 변수가 설정되지 않았습니다.")
    return create_engine(settings.database_url, pool_pre_ping=True)


def create_ocr_tables(engine: Engine | None = None) -> None:
    Base.metadata.create_all(engine or get_engine())


def _require_ocr_tables(engine: Engine) -> None:
    table_names = set(inspect(engine).get_table_names())
    missing = {"ocr_scan", "ocr_scan_item"} - table_names
    if missing:
        names = ", ".join(sorted(missing))
        raise DatabaseSchemaMissingError(
            f"OCR 저장 테이블이 없습니다: {names}. migrations/001_create_ocr_tables.sql을 적용하세요."
        )


def _resolve_ingredient_id(
    session: Session,
    table_names: set[str],
    canonical_name: str,
    alias_text: str | None,
) -> int | None:
    if "ingredient_master" not in table_names:
        return None

    ingredient_id = session.execute(
        text(
            "SELECT ingredient_id FROM ingredient_master "
            "WHERE canonical_name = :canonical_name LIMIT 1"
        ),
        {"canonical_name": canonical_name},
    ).scalar_one_or_none()
    if ingredient_id is not None or not alias_text or "ingredient_alias" not in table_names:
        return ingredient_id

    return session.execute(
        text(
            "SELECT ingredient_id FROM ingredient_alias "
            "WHERE alias_text = :alias_text LIMIT 1"
        ),
        {"alias_text": alias_text},
    ).scalar_one_or_none()


def _parse_optional_date(value: str | date | None) -> date | None:
    if isinstance(value, str):
        return date.fromisoformat(value)
    return value


def _validate_ocr_context(
    session: Session,
    table_names: set[str],
    fridge_id: int,
    created_by: int,
) -> None:
    if os.getenv('AI_DB_SCHEMA') == 'team':
        from .team_ocr_database import validate_context
        return validate_context(session, fridge_id, created_by)
    if not {"users", "fridges"}.issubset(table_names):
        return

    user_exists = session.execute(
        text("SELECT user_id FROM users WHERE user_id=:user_id LIMIT 1"),
        {"user_id": created_by},
    ).scalar_one_or_none()
    if user_exists is None:
        raise OcrContextValidationError("존재하지 않는 사용자입니다.")

    if "fridge_members" in table_names:
        has_access = session.execute(
            text(
                "SELECT f.fridge_id FROM fridges f "
                "LEFT JOIN fridge_members fm ON fm.fridge_id=f.fridge_id "
                "AND fm.user_id=:user_id AND fm.status='ACTIVE' "
                "WHERE f.fridge_id=:fridge_id "
                "AND (f.owner_user_id=:user_id OR fm.user_id IS NOT NULL) LIMIT 1"
            ),
            {"fridge_id": fridge_id, "user_id": created_by},
        ).scalar_one_or_none()
    else:
        has_access = session.execute(
            text(
                "SELECT fridge_id FROM fridges "
                "WHERE fridge_id=:fridge_id AND owner_user_id=:user_id LIMIT 1"
            ),
            {"fridge_id": fridge_id, "user_id": created_by},
        ).scalar_one_or_none()
    if has_access is None:
        raise OcrContextValidationError("해당 냉장고에 접근 권한이 없습니다.")


def save_inventory_candidates(
    result: InventoryCandidatesResponse,
    *,
    fridge_id: int,
    created_by: int,
    engine: Engine | None = None,
) -> dict:
    target_engine = engine or get_engine()
    if os.getenv('AI_DB_SCHEMA') == 'team':
        from .team_ocr_database import require_schema
        require_schema(target_engine)
    elif settings.database_auto_create:
        create_ocr_tables(target_engine)
    else:
        _require_ocr_tables(target_engine)
    table_names = set(inspect(target_engine).get_table_names())

    result_payload = result.model_dump(mode="json")
    saved_items: list[dict] = []

    with Session(target_engine) as session, session.begin():
        _validate_ocr_context(session, table_names, fridge_id, created_by)
        scan = OcrScanRecord(
            fridge_id=fridge_id,
            created_by=created_by,
            filename=result.filename,
            status="PENDING_REVIEW",
            item_count=result.count,
            mapped_item_count=0,
            result_json=json.dumps(result_payload, ensure_ascii=False),
        )
        session.add(scan)
        session.flush()

        mapped_item_count = 0
        for candidate in result_payload["inventory_candidates"]:
            ingredient = candidate["ingredient_master_candidate"]
            batch = candidate["inventory_batch_candidate"]
            ingredient_id = _resolve_ingredient_id(
                session,
                table_names,
                ingredient["canonical_name"],
                ingredient["alias_text"],
            )
            if ingredient_id is not None:
                mapped_item_count += 1

            item = OcrScanItemRecord(
                ocr_scan_id=scan.ocr_scan_id,
                row_index=candidate["row_index"],
                ingredient_id=ingredient_id,
                raw_text=candidate["raw_text"],
                recognized_product_name=candidate["recognized_product_name"],
                normalized_product_name=candidate["normalized_product_name"],
                canonical_name=ingredient["canonical_name"],
                match_confidence=ingredient["match_confidence"],
                remaining_quantity=batch["remaining_quantity"],
                unit=batch["unit"],
                consume_by_date=_parse_optional_date(batch["consume_by_date"]),
                storage_type=batch["storage_type"],
                review_status="PENDING_REVIEW",
                candidate_json=json.dumps(candidate, ensure_ascii=False),
            )
            session.add(item)
            session.flush()
            saved_items.append(
                {
                    "ocr_scan_item_id": item.ocr_scan_item_id,
                    "row_index": item.row_index,
                    "ingredient_id": item.ingredient_id,
                    "canonical_name": item.canonical_name,
                    "review_status": item.review_status,
                }
            )

        scan.mapped_item_count = mapped_item_count
        scan_id = scan.ocr_scan_id

    return {
        "ocr_scan_id": scan_id,
        "status": "PENDING_REVIEW",
        "item_count": result.count,
        "mapped_item_count": mapped_item_count,
        "requires_review_count": result.count,
        "items": saved_items,
        "inventory_candidates": result_payload["inventory_candidates"],
    }


def confirm_inventory_candidates(
    ocr_scan_id: int,
    *,
    created_by: int,
    review_items: list[dict],
    engine: Engine | None = None,
) -> dict:
    target_engine = engine or get_engine()
    if os.getenv('AI_DB_SCHEMA') == 'team':
        from .team_ocr_database import confirm
        return confirm(target_engine, ocr_scan_id, created_by, review_items)
    table_names = set(inspect(target_engine).get_table_names())
    required_tables = {"ocr_scan", "ocr_scan_item", "ingredient_master", "inventory_batches"}
    missing = required_tables - table_names
    if missing:
        names = ", ".join(sorted(missing))
        raise DatabaseSchemaMissingError(f"재고 확정에 필요한 테이블이 없습니다: {names}")

    inventory_columns = {column["name"] for column in inspect(target_engine).get_columns("inventory_batches")}
    required_columns = {"storage_type", "source_type", "source_ocr_item_id"}
    if not required_columns.issubset(inventory_columns):
        raise DatabaseSchemaMissingError(
            "inventory_batches 추적 컬럼이 없습니다. migrations/002_add_ocr_inventory_trace.sql을 적용하세요."
        )

    requested_by_id = {item["ocr_scan_item_id"]: item for item in review_items}
    if len(requested_by_id) != len(review_items):
        raise InventoryReviewValidationError("동일한 OCR 항목이 중복되었습니다.")

    created_batches: list[dict] = []
    rejected_item_count = 0
    now = datetime.now()
    lock_suffix = "" if target_engine.dialect.name == "sqlite" else " FOR UPDATE"

    with Session(target_engine) as session, session.begin():
        scan = session.execute(
            text(
                "SELECT ocr_scan_id, fridge_id, created_by, status FROM ocr_scan "
                f"WHERE ocr_scan_id=:ocr_scan_id{lock_suffix}"
            ),
            {"ocr_scan_id": ocr_scan_id},
        ).mappings().one_or_none()
        if scan is None:
            raise OcrScanNotFoundError("존재하지 않는 OCR 결과입니다.")
        if scan["created_by"] != created_by:
            raise InventoryReviewValidationError("OCR 요청 사용자와 검토 사용자가 일치하지 않습니다.")
        if scan["status"] != "PENDING_REVIEW":
            raise OcrScanAlreadyReviewedError("이미 검토가 완료된 OCR 결과입니다.")

        stored_items = session.execute(
            text(
                "SELECT ocr_scan_item_id FROM ocr_scan_item "
                f"WHERE ocr_scan_id=:ocr_scan_id{lock_suffix}"
            ),
            {"ocr_scan_id": ocr_scan_id},
        ).scalars().all()
        if set(stored_items) != set(requested_by_id):
            raise InventoryReviewValidationError("저장된 모든 OCR 항목에 대한 결정이 필요합니다.")

        for item_id in stored_items:
            review = requested_by_id[item_id]
            if review["decision"] == "REJECT":
                session.execute(
                    text(
                        "UPDATE ocr_scan_item SET review_status='REJECTED', reviewed_at=:reviewed_at "
                        "WHERE ocr_scan_item_id=:item_id"
                    ),
                    {"reviewed_at": now, "item_id": item_id},
                )
                rejected_item_count += 1
                continue

            required_values = ("ingredient_id", "remaining_quantity", "unit", "storage_type")
            missing_values = [name for name in required_values if review.get(name) is None]
            if missing_values:
                raise InventoryReviewValidationError(
                    f"확정 항목 {item_id}의 필수값이 없습니다: {', '.join(missing_values)}"
                )

            ingredient_exists = session.execute(
                text(
                    "SELECT ingredient_id FROM ingredient_master "
                    "WHERE ingredient_id=:ingredient_id LIMIT 1"
                ),
                {"ingredient_id": review["ingredient_id"]},
            ).scalar_one_or_none()
            if ingredient_exists is None:
                raise InventoryReviewValidationError(
                    f"존재하지 않는 ingredient_id입니다: {review['ingredient_id']}"
                )

            insert_result = session.execute(
                text(
                    "INSERT INTO inventory_batches "
                    "(fridge_id, ingredient_id, consume_by_date, remaining_quantity, unit, status, "
                    "created_by, storage_type, source_type, source_ocr_item_id) "
                    "VALUES (:fridge_id, :ingredient_id, :consume_by_date, :remaining_quantity, "
                    ":unit, 'ACTIVE', :created_by, :storage_type, 'OCR', :source_ocr_item_id)"
                ),
                {
                    "fridge_id": scan["fridge_id"],
                    "ingredient_id": review["ingredient_id"],
                    "consume_by_date": review.get("consume_by_date"),
                    "remaining_quantity": review["remaining_quantity"],
                    "unit": review["unit"],
                    "created_by": created_by,
                    "storage_type": review["storage_type"],
                    "source_ocr_item_id": item_id,
                },
            )
            batch_id = insert_result.lastrowid
            session.execute(
                text(
                    "UPDATE ocr_scan_item SET ingredient_id=:ingredient_id, "
                    "remaining_quantity=:remaining_quantity, unit=:unit, consume_by_date=:consume_by_date, "
                    "storage_type=:storage_type, review_status='CONFIRMED', "
                    "inventory_batch_id=:batch_id, reviewed_at=:reviewed_at "
                    "WHERE ocr_scan_item_id=:item_id"
                ),
                {
                    "ingredient_id": review["ingredient_id"],
                    "remaining_quantity": review["remaining_quantity"],
                    "unit": review["unit"],
                    "consume_by_date": review.get("consume_by_date"),
                    "storage_type": review["storage_type"],
                    "batch_id": batch_id,
                    "reviewed_at": now,
                    "item_id": item_id,
                },
            )
            created_batches.append(
                {
                    "batch_id": batch_id,
                    "ocr_scan_item_id": item_id,
                    "ingredient_id": review["ingredient_id"],
                    "remaining_quantity": review["remaining_quantity"],
                    "unit": review["unit"],
                    "consume_by_date": review.get("consume_by_date"),
                    "storage_type": review["storage_type"],
                }
            )

        session.execute(
            text(
                "UPDATE ocr_scan SET status='REVIEWED', reviewed_at=:reviewed_at "
                "WHERE ocr_scan_id=:ocr_scan_id"
            ),
            {"reviewed_at": now, "ocr_scan_id": ocr_scan_id},
        )

    return {
        "ocr_scan_id": ocr_scan_id,
        "status": "REVIEWED",
        "created_batch_count": len(created_batches),
        "rejected_item_count": rejected_item_count,
        "batches": created_batches,
    }
